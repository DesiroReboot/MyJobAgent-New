import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import lark_oapi as lark

from chat.ingest import ingest_chat_sessions
from feishu_inbox.downloader import FeishuResourceDownloader
from feishu_inbox.resources import InboundFile, extract_inbound_files, extract_text, infer_domain_from_filename
from feishu_inbox.session_store import append_chat_sessions_jsonl
from pusher.feishu_pusher import FeishuPusher


@dataclass
class SenderState:
    domain: str = ""
    domain_set_at: float = 0.0


class FeishuInboxService:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        config: Any,
        pusher: FeishuPusher,
        inbox_cfg: Dict[str, Any],
    ) -> None:
        self.app_id = (app_id or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.config = config
        self.pusher = pusher
        self.inbox_cfg = inbox_cfg or {}

        self.enabled = bool(self.inbox_cfg.get("enabled", False))
        self.max_bytes = int(self.inbox_cfg.get("max_bytes", 100 * 1024 * 1024))
        self.accept_ext = set([str(x).lower() for x in (self.inbox_cfg.get("accept_ext") or [])]) or {
            ".zip",
            ".json",
            ".txt",
            ".md",
        }

        self.domain_default = str(self.inbox_cfg.get("domain_default", "chatgpt.com")).strip() or "chatgpt.com"
        self.domain_rules = self.inbox_cfg.get("domain_rules", {}) or {}

        sessions_out = str(self.inbox_cfg.get("sessions_out", "") or "").strip()
        self.sessions_out = Path(self.config.resolve_path(sessions_out)) if sessions_out else Path(self.config.resolve_path("../logs/chat_sessions.jsonl"))

        download_dir = str(self.inbox_cfg.get("download_dir", "") or "").strip()
        if download_dir:
            self.download_dir = Path(self.config.resolve_path(download_dir))
        else:
            self.download_dir = Path(os.environ.get("TEMP", "")) if os.environ.get("TEMP") else Path.cwd() / "logs" / "feishu_downloads"

        self.state_path = Path(self.config.resolve_path(str(self.inbox_cfg.get("state_file", "../logs/feishu_inbox_state.json"))))

        self._downloader = FeishuResourceDownloader(pusher=self.pusher, download_dir=self.download_dir, max_bytes=self.max_bytes)

        self._lock = threading.Lock()
        self._sender_state: Dict[str, SenderState] = {}
        self._processed: Dict[str, float] = {}
        self._load_state()

    def start(self) -> None:
        if not self.enabled:
            print("[FeishuInbox] disabled")
            return
        if not self.app_id or not self.app_secret:
            raise ValueError("FEISHU_APP_ID/FEISHU_APP_SECRET required for inbox")

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_p2_message_receive_v1)
            .build()
        )
        cli = lark.ws.Client(self.app_id, self.app_secret, event_handler=event_handler, log_level=lark.LogLevel.INFO)
        print("[FeishuInbox] starting websocket client")
        cli.start()

    def _on_p2_message_receive_v1(self, data: lark.im.v1.P2ImMessageReceiveV1) -> None:
        payload = json.loads(lark.JSON.marshal(data))
        event = payload.get("event", {}) if isinstance(payload, dict) else {}
        message = event.get("message", {}) if isinstance(event, dict) else {}
        sender = event.get("sender", {}) if isinstance(event, dict) else {}

        message_id = str(message.get("message_id", "") or "").strip()
        chat_type = str(message.get("chat_type", "") or "").strip()
        message_type = str(message.get("message_type", "") or "").strip()
        content = str(message.get("content", "") or "")

        sender_id = ""
        if isinstance(sender, dict):
            sender_id = (
                ((sender.get("sender_id") or {}) if isinstance(sender.get("sender_id"), dict) else {}).get("open_id")
                or ""
            )
        sender_id = str(sender_id).strip()

        if not message_id:
            return

        if chat_type and chat_type.lower() != "p2p":
            return

        if message_type == "text":
            text = extract_text(content)
            self._handle_text_command(sender_id, text)
            return

        files = extract_inbound_files(message_id=message_id, message_type=message_type, content=content)
        if not files:
            return

        threading.Thread(
            target=self._process_files,
            args=(sender_id, message_id, files),
            daemon=True,
        ).start()

    def _handle_text_command(self, sender_id: str, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        if t.lower().startswith("/domain"):
            parts = t.split(maxsplit=1)
            if len(parts) == 2:
                domain = parts[1].strip()
                if domain:
                    with self._lock:
                        self._sender_state[sender_id] = SenderState(domain=domain, domain_set_at=time.time())
                        self._save_state()
                    self._push_text(sender_id, f"[Chat Import] domain 已设置为: {domain}")
            return
        if t in {"上传", "upload"}:
            self._push_text(sender_id, "请直接发送 chat 导出文件（zip/json/txt/md），我会自动导入并回报结果。可先用 /domain chatgpt.com 设置来源域名。")

    def _process_files(self, sender_id: str, message_id: str, files: list[InboundFile]) -> None:
        results = []
        for f in files:
            if not self._accept_file(f):
                results.append((f.name, "skip", "unsupported"))
                continue

            key = f"{message_id}:{f.key}"
            if self._is_processed(key):
                results.append((f.name, "skip", "already_processed"))
                continue

            try:
                path = self._downloader.download_message_resource(message_id=message_id, file_key=f.key, file_name=f.name)
            except Exception as e:
                results.append((f.name, "fail", str(e)))
                continue

            domain = self._resolve_domain(sender_id, f.name)
            try:
                sessions = ingest_chat_sessions(str(path), domain=domain, max_chars=int(self.inbox_cfg.get("max_chars", 6000) or 6000))
                stable_prefix = f"feishu:{message_id}:{f.key}"
                for s in sessions:
                    if "::" in s.source:
                        _, suffix = s.source.split("::", 1)
                        s.source = f"{stable_prefix}::{suffix}"
                    else:
                        s.source = stable_prefix
                append_chat_sessions_jsonl(sessions, str(self.sessions_out))
                self._mark_processed(key)
                results.append((f.name, "ok", f"sessions={len(sessions)}"))
            except Exception as e:
                results.append((f.name, "fail", str(e)))

        ok = [r for r in results if r[1] == "ok"]
        fail = [r for r in results if r[1] == "fail"]
        skip = [r for r in results if r[1] == "skip"]
        lines = ["[Chat Import] 处理完成"]
        if ok:
            lines.append(f"- 成功: {len(ok)}")
        if fail:
            lines.append(f"- 失败: {len(fail)}")
        if skip:
            lines.append(f"- 跳过: {len(skip)}")
        if ok:
            lines.append(f"- sessions_out: {self.sessions_out}")
        self._push_text(sender_id, "\n".join(lines))

    def _push_text(self, sender_id: str, text: str) -> None:
        try:
            self.pusher.set_user_id(sender_id)
            self.pusher.push_text(text)
        except Exception:
            print(text)

    def _accept_file(self, f: InboundFile) -> bool:
        name = (f.name or "").strip()
        if not name:
            return False
        ext = Path(name).suffix.lower()
        return ext in self.accept_ext

    def _resolve_domain(self, sender_id: str, file_name: str) -> str:
        with self._lock:
            st = self._sender_state.get(sender_id)
        if st and st.domain and (time.time() - float(st.domain_set_at or 0.0) <= 600):
            return st.domain
        inferred = infer_domain_from_filename(file_name=file_name, default="", rules=self.domain_rules)
        return inferred or self.domain_default

    def _load_state(self) -> None:
        p = self.state_path
        if not p.exists():
            return
        try:
            payload = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        sender_state = payload.get("sender_state", {})
        processed = payload.get("processed", {})
        if isinstance(sender_state, dict):
            for k, v in sender_state.items():
                if not isinstance(v, dict):
                    continue
                domain = str(v.get("domain", "") or "").strip()
                at = float(v.get("domain_set_at", 0.0) or 0.0)
                if k and domain:
                    self._sender_state[str(k)] = SenderState(domain=domain, domain_set_at=at)
        if isinstance(processed, dict):
            for k, v in processed.items():
                try:
                    self._processed[str(k)] = float(v or 0.0)
                except Exception:
                    continue
        self._cleanup_processed()

    def _save_state(self) -> None:
        p = self.state_path
        if not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sender_state": {k: {"domain": v.domain, "domain_set_at": v.domain_set_at} for k, v in self._sender_state.items()},
            "processed": self._processed,
        }
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _cleanup_processed(self) -> None:
        now = time.time()
        keep_seconds = 7 * 24 * 3600
        self._processed = {k: v for k, v in self._processed.items() if (now - float(v or 0.0)) <= keep_seconds}

    def _is_processed(self, key: str) -> bool:
        with self._lock:
            ts = self._processed.get(key)
        if not ts:
            return False
        return (time.time() - float(ts)) <= 7 * 24 * 3600

    def _mark_processed(self, key: str) -> None:
        with self._lock:
            self._processed[key] = time.time()
            self._cleanup_processed()
            self._save_state()


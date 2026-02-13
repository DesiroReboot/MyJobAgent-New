import json
import zipfile
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from .compress import compress_chat_text
    from .sanitize import redact_sensitive
except ImportError:
    from chat.compress import compress_chat_text
    from chat.sanitize import redact_sensitive


def _try_parse_datetime(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and value > 0:
        try:
            v = float(value)
            if v >= 1e14:
                return datetime.fromtimestamp(v / 1e6).isoformat()
            if v >= 1e12:
                return datetime.fromtimestamp(v / 1e3).isoformat()
            return datetime.fromtimestamp(v).isoformat()
        except Exception:
            return None
    if isinstance(value, str) and value.strip():
        s = value.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).isoformat()
            except Exception:
                pass
        try:
            return datetime.fromisoformat(s).isoformat()
        except Exception:
            return None
    return None


def _collect_text_fields(node: Any, out: List[str], depth: int = 0, max_depth: int = 8) -> None:
    if depth > max_depth:
        return
    if node is None:
        return
    if isinstance(node, str):
        s = node.strip()
        if s:
            out.append(s)
        return
    if isinstance(node, (int, float, bool)):
        return
    if isinstance(node, list):
        for item in node[:2000]:
            _collect_text_fields(item, out, depth + 1, max_depth=max_depth)
        return
    if isinstance(node, dict):
        preferred_keys = ("text", "content", "message", "messages", "parts", "prompt", "completion", "title")
        for k in preferred_keys:
            if k in node:
                _collect_text_fields(node.get(k), out, depth + 1, max_depth=max_depth)
        for k, v in list(node.items())[:2000]:
            if k in preferred_keys:
                continue
            _collect_text_fields(v, out, depth + 1, max_depth=max_depth)


def _extract_time_hints(payload: Any) -> Tuple[Optional[str], Optional[str]]:
    start = None
    end = None
    if isinstance(payload, dict):
        for key in ("create_time", "created_at", "created", "timestamp", "start", "start_time"):
            if key in payload and start is None:
                start = _try_parse_datetime(payload.get(key))
        for key in ("update_time", "updated_at", "updated", "end", "end_time"):
            if key in payload and end is None:
                end = _try_parse_datetime(payload.get(key))
    return start, end


@dataclass
class ChatSession:
    domain: str
    source: str
    compressed_text: str
    start: Optional[str] = None
    end: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        sid_raw = f"{self.domain}\n{self.source}\n{self.compressed_text}"
        session_id = hashlib.sha1(sid_raw.encode("utf-8", errors="ignore")).hexdigest()
        return {
            "session_id": session_id,
            "domain": self.domain,
            "source": self.source,
            "start": self.start,
            "end": self.end,
            "compressed_text": self.compressed_text,
        }


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return path.read_text(encoding="utf-8", errors="ignore")


def _load_json_bytes(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return json.loads(raw.decode("utf-8", errors="ignore"))


def _load_json_file(path: Path) -> Any:
    raw = path.read_bytes()
    return _load_json_bytes(raw)


def ingest_chat_sessions(
    input_path: str,
    domain: str,
    max_chars: int = 6000,
) -> List[ChatSession]:
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Chat input path not found: {input_path}")

    items: List[Tuple[str, Any]] = []
    if path.is_dir():
        for p in sorted(path.rglob("*")):
            if not p.is_file():
                continue
            suffix = p.suffix.lower()
            if suffix in {".txt", ".md"}:
                items.append((str(p), _read_text_file(p)))
            elif suffix == ".json":
                items.append((str(p), _load_json_file(p)))
            elif suffix == ".zip":
                items.extend(_read_zip_payloads(p))
    else:
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md"}:
            items.append((str(path), _read_text_file(path)))
        elif suffix == ".json":
            items.append((str(path), _load_json_file(path)))
        elif suffix == ".zip":
            items.extend(_read_zip_payloads(path))
        else:
            items.append((str(path), _read_text_file(path)))

    sessions: List[ChatSession] = []
    for source, payload in items:
        raw_text = ""
        start, end = _extract_time_hints(payload)
        if isinstance(payload, str):
            raw_text = payload
        else:
            parts: List[str] = []
            _collect_text_fields(payload, parts)
            raw_text = "\n".join(parts)
        raw_text = redact_sensitive(raw_text)
        compressed = compress_chat_text(raw_text, max_chars=max_chars)
        if compressed:
            sessions.append(ChatSession(domain=domain, source=source, compressed_text=compressed, start=start, end=end))

    return sessions


def _read_zip_payloads(path: Path) -> List[Tuple[str, Any]]:
    out: List[Tuple[str, Any]] = []
    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            lower = name.lower()
            if not (lower.endswith(".json") or lower.endswith(".txt") or lower.endswith(".md")):
                continue
            raw = zf.read(info)
            try:
                if lower.endswith(".json"):
                    payload = _load_json_bytes(raw)
                else:
                    payload = raw.decode("utf-8", errors="ignore")
            except Exception:
                continue
            out.append((f"{path}::{name}", payload))
    return out


def save_chat_sessions_jsonl(sessions: List[ChatSession], output_path: str) -> str:
    out_path = Path(output_path)
    if not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for sess in sessions:
        if not isinstance(sess, ChatSession):
            continue
        lines.append(json.dumps(sess.to_dict(), ensure_ascii=False))
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return str(out_path)


def load_chat_sessions_file(path: str) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Chat sessions file not found: {path}")
    suffix = p.suffix.lower()
    if suffix == ".jsonl":
        out: List[Dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s:
                continue
            try:
                item = json.loads(s)
            except Exception:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out
    payload = _load_json_file(p)
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        sessions = payload.get("sessions")
        if isinstance(sessions, list):
            return [x for x in sessions if isinstance(x, dict)]
        return [payload]
    return []


def _infer_session_datetime(path: Path) -> Optional[datetime]:
    stem = (path.stem or "").strip()
    if stem.isdigit():
        try:
            n = int(stem)
            if len(stem) >= 13:
                return datetime.fromtimestamp(float(n) / 1000.0)
            if len(stem) >= 10:
                return datetime.fromtimestamp(float(n))
        except Exception:
            pass
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except Exception:
        return None


def select_recent_session_files(input_path: str, days: int = 7) -> List[Path]:
    root = Path(input_path)
    if not root.exists():
        raise FileNotFoundError(f"Chat input path not found: {input_path}")

    cutoff = datetime.now() - timedelta(days=max(0, int(days)))

    allowed = {".md", ".txt", ".json", ".zip"}

    if root.is_file():
        if root.suffix.lower() not in allowed:
            return []
        dt = _infer_session_datetime(root)
        if dt is None or dt < cutoff:
            return []
        return [root]

    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in allowed]
    out: List[Tuple[datetime, Path]] = []
    for p in files:
        dt = _infer_session_datetime(p)
        if dt is None or dt < cutoff:
            continue
        out.append((dt, p))
    out.sort(key=lambda x: (x[0], str(x[1])))
    return [p for _, p in out]

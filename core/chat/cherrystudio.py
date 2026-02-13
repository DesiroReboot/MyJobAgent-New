import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .compress import compress_chat_text
from .ingest import ChatSession
from .sanitize import redact_sensitive


@dataclass
class CherryStudioSchemaError(RuntimeError):
    message: str
    db_path: str
    schema_summary: Dict[str, Any]

    def __str__(self) -> str:
        return f"{self.message} (db={self.db_path})"


def get_default_data_dir() -> str:
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        p = Path(appdata) / "CherryStudio"
        if p.exists():
            return str(p)
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        p = Path(local) / "CherryStudio"
        if p.exists():
            return str(p)
    if appdata:
        return str(Path(appdata) / "CherryStudio")
    if local:
        return str(Path(local) / "CherryStudio")
    return ""


def _iter_candidate_db_files(data_dir: str) -> List[Path]:
    root = Path(data_dir)
    if not root.exists():
        return []
    exts = {".db", ".sqlite", ".sqlite3"}
    out: List[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in exts:
            continue
        out.append(p)
    out.sort(key=lambda x: (-x.stat().st_size, -x.stat().st_mtime, str(x)))
    return out


def _copy_db_to_temp(db_path: str) -> str:
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(f"CherryStudio DB not found: {db_path}")
    fd, tmp = tempfile.mkstemp(prefix="cherrystudio_", suffix=src.suffix or ".db")
    os.close(fd)
    shutil.copy2(str(src), tmp)
    return tmp


def _parse_datetime_any(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        v = float(value)
        if v <= 0:
            return None
        try:
            if v >= 1e14:
                return datetime.fromtimestamp(v / 1e6)
            if v >= 1e12:
                return datetime.fromtimestamp(v / 1e3)
            if v >= 1e9:
                return datetime.fromtimestamp(v)
            return None
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
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


def _coerce_message_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return ""
        if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
            try:
                payload = json.loads(s)
                parts: List[str] = []
                _collect_text_fields(payload, parts)
                out = "\n".join(parts).strip()
                return out
            except Exception:
                return s
        return s
    if isinstance(value, (dict, list)):
        parts = []
        _collect_text_fields(value, parts)
        return "\n".join(parts).strip()
    return str(value).strip()


def _get_tables(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _get_table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    cols = []
    for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if not row:
            continue
        cols.append(str(row[1]))
    return cols


def summarize_schema(db_path: str, max_tables: int = 80) -> Dict[str, Any]:
    tmp = _copy_db_to_temp(db_path)
    try:
        conn = sqlite3.connect(tmp)
        conn.row_factory = sqlite3.Row
        tables = _get_tables(conn)
        tables = tables[:max_tables]
        out = {}
        for t in tables:
            out[t] = _get_table_columns(conn, t)
        return {"tables": out}
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def _pick_message_table(conn: sqlite3.Connection) -> Tuple[str, Dict[str, str]]:
    tables = _get_tables(conn)
    scored: List[Tuple[int, str, Dict[str, str]]] = []
    for t in tables:
        cols = _get_table_columns(conn, t)
        lower = {c.lower(): c for c in cols}

        role_keys = ("role", "sender", "author", "from", "message_role")
        content_keys = ("content", "text", "message", "body", "data")
        time_keys = ("created_at", "createdat", "created", "timestamp", "time", "created_time")
        conv_keys = ("conversation_id", "topic_id", "chat_id", "session_id", "thread_id")

        mapping: Dict[str, str] = {}
        for k in role_keys:
            if k in lower:
                mapping["role"] = lower[k]
                break
        for k in content_keys:
            if k in lower:
                mapping["content"] = lower[k]
                break
        for k in time_keys:
            if k in lower:
                mapping["time"] = lower[k]
                break
        for k in conv_keys:
            if k in lower:
                mapping["conv"] = lower[k]
                break

        score = 0
        name_l = t.lower()
        if "message" in name_l:
            score += 3
        if "chat" in name_l:
            score += 2
        if "conversation" in name_l or "topic" in name_l:
            score += 1
        if "content" in mapping:
            score += 4
        if "role" in mapping:
            score += 2
        if "time" in mapping:
            score += 2
        if "conv" in mapping:
            score += 2
        if score >= 6:
            scored.append((score, t, mapping))

    scored.sort(key=lambda x: (-x[0], x[1].lower()))
    if not scored:
        raise CherryStudioSchemaError(
            message="Cannot identify message table",
            db_path="",
            schema_summary={"tables": {t: _get_table_columns(conn, t) for t in _get_tables(conn)[:80]}},
        )
    _, table, mapping = scored[0]
    return table, mapping


def extract_sessions(
    days: int = 7,
    domain: str = "cherrystudio",
    data_dir: str = "",
    db_path: str = "",
    max_chars: int = 6000,
) -> List[ChatSession]:
    if not data_dir and not db_path:
        data_dir = get_default_data_dir()
    if data_dir and not db_path:
        candidates = _iter_candidate_db_files(data_dir)
        for cand in candidates[:12]:
            tmp0 = ""
            try:
                tmp0 = _copy_db_to_temp(str(cand))
                conn0 = sqlite3.connect(tmp0)
                conn0.row_factory = sqlite3.Row
                _pick_message_table(conn0)
                db_path = str(cand)
                break
            except Exception:
                continue
            finally:
                try:
                    if tmp0:
                        os.remove(tmp0)
                except Exception:
                    pass
    if not db_path:
        raise FileNotFoundError(f"CherryStudio data not found: data_dir={data_dir}")

    cutoff = datetime.now() - timedelta(days=max(0, int(days)))
    tmp = _copy_db_to_temp(db_path)
    try:
        conn = sqlite3.connect(tmp)
        conn.row_factory = sqlite3.Row
        try:
            msg_table, mapping = _pick_message_table(conn)
        except CherryStudioSchemaError as e:
            raise CherryStudioSchemaError(
                message=e.message,
                db_path=db_path,
                schema_summary=e.schema_summary,
            )

        cols = _get_table_columns(conn, msg_table)
        id_col = None
        for c in cols:
            if c.lower() in {"id", "message_id", "mid"}:
                id_col = c
                break

        select_cols = []
        for key in ("conv", "time", "role", "content"):
            if key in mapping:
                select_cols.append(mapping[key])
        if id_col and id_col not in select_cols:
            select_cols.append(id_col)
        if not select_cols:
            raise CherryStudioSchemaError(
                message="No usable columns in message table",
                db_path=db_path,
                schema_summary=summarize_schema(db_path),
            )

        sql = f"SELECT {', '.join(select_cols)} FROM {msg_table}"
        rows = conn.execute(sql).fetchall()

        grouped: Dict[str, List[Tuple[Optional[datetime], str, str]]] = {}
        for r in rows:
            conv = ""
            if "conv" in mapping:
                conv = str(r[mapping["conv"]] or "").strip()
            if not conv:
                conv = "default"

            t = None
            if "time" in mapping:
                t = _parse_datetime_any(r[mapping["time"]])
            role = str(r[mapping["role"]] or "").strip() if "role" in mapping else ""
            content = _coerce_message_text(r[mapping["content"]]) if "content" in mapping else ""
            if not content:
                continue
            grouped.setdefault(conv, []).append((t, role, content))

        sessions: List[ChatSession] = []
        for conv, items in grouped.items():
            items.sort(key=lambda x: (x[0] or datetime.min))
            times = [t for t, _, _ in items if t is not None]
            start_dt = min(times) if times else None
            end_dt = max(times) if times else None
            if end_dt is not None and end_dt < cutoff:
                continue

            lines = []
            for t, role, content in items:
                ts = t.isoformat(timespec="seconds") if isinstance(t, datetime) else ""
                prefix = ""
                if ts and role:
                    prefix = f"{ts} {role}: "
                elif ts:
                    prefix = f"{ts} "
                elif role:
                    prefix = f"{role}: "
                lines.append(prefix + content)

            raw_text = "\n".join(lines)
            raw_text = redact_sensitive(raw_text)
            compressed = compress_chat_text(raw_text, max_chars=max_chars)
            if not compressed:
                continue
            sessions.append(
                ChatSession(
                    domain=domain,
                    source=f"cherrystudio::{Path(db_path).name}::{conv}",
                    compressed_text=compressed,
                    start=start_dt.isoformat() if start_dt else None,
                    end=end_dt.isoformat() if end_dt else None,
                )
            )

        return sessions
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass

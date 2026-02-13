from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from .cherrystudio import extract_sessions as extract_cherrystudio_sessions
from .ingest import ChatSession, ingest_chat_sessions, select_recent_session_files


@dataclass
class ChatSourceResult:
    source: Dict[str, Any]
    sessions: List[ChatSession]
    errors: List[str]


def _dedupe_sessions(sessions: List[ChatSession]) -> List[ChatSession]:
    seen = set()
    out: List[ChatSession] = []
    for s in sessions:
        try:
            sid = s.to_dict().get("session_id", "")
        except Exception:
            sid = ""
        if not sid:
            continue
        if sid in seen:
            continue
        seen.add(sid)
        out.append(s)
    return out


def collect_chat_sessions(
    sources: List[Dict[str, Any]],
    days: int = 7,
    max_chars: int = 6000,
) -> Tuple[List[ChatSession], List[ChatSourceResult]]:
    all_sessions: List[ChatSession] = []
    results: List[ChatSourceResult] = []

    for src in sources or []:
        if not isinstance(src, dict):
            continue
        t = str(src.get("type", "") or "").strip().lower()
        domain = str(src.get("domain", "") or "").strip()
        errs: List[str] = []
        sessions: List[ChatSession] = []

        if t == "filesystem":
            p = str(src.get("path", "") or "").strip()
            if not p:
                errs.append("missing path")
            else:
                files = select_recent_session_files(p, days=int(days))
                for f in files:
                    d = domain or "chatshare.biz"
                    sessions.extend(ingest_chat_sessions(str(f), domain=d, max_chars=int(max_chars)))

        elif t == "cherrystudio":
            data_dir = str(src.get("data_dir", "") or "").strip()
            d = domain or "cherrystudio"
            try:
                sessions = extract_cherrystudio_sessions(days=int(days), domain=d, data_dir=data_dir, max_chars=int(max_chars))
            except Exception as e:
                errs.append(str(e))

        else:
            errs.append(f"unsupported type: {t or '(empty)'}")

        sessions = _dedupe_sessions(sessions)
        all_sessions.extend(sessions)
        results.append(ChatSourceResult(source=src, sessions=sessions, errors=errs))

    all_sessions = _dedupe_sessions(all_sessions)
    return all_sessions, results

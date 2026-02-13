import json
from pathlib import Path
from typing import Any, Dict, List

from chat.ingest import ChatSession


def append_chat_sessions_jsonl(sessions: List[ChatSession], output_path: str) -> str:
    out_path = Path(output_path)
    if not out_path.parent.exists():
        out_path.parent.mkdir(parents=True, exist_ok=True)

    existing_ids = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = (line or "").strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                sid = str(obj.get("session_id", "") or "").strip()
                if sid:
                    existing_ids.add(sid)

    new_lines = []
    for sess in sessions:
        d = sess.to_dict()
        sid = str(d.get("session_id", "") or "").strip()
        if sid and sid in existing_ids:
            continue
        new_lines.append(json.dumps(d, ensure_ascii=False))
        if sid:
            existing_ids.add(sid)

    if not new_lines:
        return str(out_path)

    with out_path.open("a", encoding="utf-8") as f:
        for line in new_lines:
            f.write(line)
            f.write("\n")

    return str(out_path)


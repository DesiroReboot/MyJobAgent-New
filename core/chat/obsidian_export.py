import os
from datetime import datetime
from pathlib import Path
from typing import List

from .ingest import ChatSession


def export_sessions_per_session(
    sessions: List[ChatSession],
    vault_path: str,
    folder: str = "CherryStudio",
) -> List[str]:
    if not vault_path:
        raise ValueError("vault_path is empty")
    root = Path(vault_path)
    root.mkdir(parents=True, exist_ok=True)

    day = datetime.now().strftime("%Y-%m-%d")
    base = root / folder / day
    base.mkdir(parents=True, exist_ok=True)

    written: List[str] = []
    for s in sessions:
        d = s.to_dict()
        sid = str(d.get("session_id") or "").strip()
        if not sid:
            continue
        p = base / f"{sid}.md"
        start = d.get("start") or ""
        end = d.get("end") or ""
        domain = d.get("domain") or ""
        source = d.get("source") or ""
        text = d.get("compressed_text") or ""

        body = "\n".join(
            [
                f"domain: {domain}",
                f"source: {source}",
                f"start: {start}",
                f"end: {end}",
                "",
                text,
                "",
            ]
        )
        p.write_text(body, encoding="utf-8")
        written.append(str(p))
    return written


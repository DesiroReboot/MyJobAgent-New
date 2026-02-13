import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class InboundFile:
    message_id: str
    key: str
    name: str


def extract_text(content: str) -> str:
    if not content:
        return ""
    try:
        payload = json.loads(content)
    except Exception:
        return ""
    if isinstance(payload, dict):
        t = payload.get("text", "")
        return str(t or "")
    return ""


def extract_inbound_files(message_id: str, message_type: str, content: str) -> List[InboundFile]:
    if not message_id or not content:
        return []
    message_type = (message_type or "").strip().lower()
    try:
        payload = json.loads(content)
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []

    files: List[InboundFile] = []

    if message_type == "file":
        key = str(payload.get("file_key", "") or "").strip()
        name = str(payload.get("file_name", "") or payload.get("name", "") or "").strip()
        if key and name:
            files.append(InboundFile(message_id=message_id, key=key, name=name))
        return files

    if message_type in {"media", "image"}:
        key = str(payload.get("file_key", "") or payload.get("image_key", "") or "").strip()
        name = str(payload.get("file_name", "") or payload.get("name", "") or "").strip()
        if key and name:
            files.append(InboundFile(message_id=message_id, key=key, name=name))
        return files

    if message_type == "post":
        return []

    return []


def infer_domain_from_filename(file_name: str, default: str, rules: Dict[str, Any]) -> str:
    name = (file_name or "").lower()
    if not name:
        return default
    if isinstance(rules, dict):
        for k, v in rules.items():
            kk = str(k or "").strip().lower()
            if not kk:
                continue
            if kk in name:
                return str(v or "").strip() or default

    if "chatgpt" in name or "openai" in name:
        return "chatgpt.com"
    if "claude" in name:
        return "claude.ai"
    if "gemini" in name:
        return "gemini.google.com"
    if "deepseek" in name:
        return "chat.deepseek.com"

    return default


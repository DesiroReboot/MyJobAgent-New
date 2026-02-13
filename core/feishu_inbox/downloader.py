import os
import re
from pathlib import Path
from typing import Optional

import requests

from pusher.feishu_pusher import FeishuPusher


_FILENAME_RE = re.compile(r"[^A-Za-z0-9\u4e00-\u9fa5\.\-_ ]+")


def _safe_filename(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return "upload.bin"
    s = s.replace("\\", "_").replace("/", "_").replace(":", "_")
    s = _FILENAME_RE.sub("_", s)
    s = s.strip(" ._")
    return s or "upload.bin"


class FeishuResourceDownloader:
    def __init__(self, pusher: FeishuPusher, download_dir: Path, max_bytes: int = 100 * 1024 * 1024) -> None:
        self.pusher = pusher
        self.download_dir = Path(download_dir)
        self.max_bytes = int(max_bytes or 0) if max_bytes else 0

    def download_message_resource(self, message_id: str, file_key: str, file_name: str) -> Path:
        message_id = (message_id or "").strip()
        file_key = (file_key or "").strip()
        if not message_id or not file_key:
            raise ValueError("message_id/file_key required")

        token = self.pusher._get_tenant_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        safe_name = _safe_filename(file_name)
        out_dir = self.download_dir / message_id
        if not out_dir.exists():
            out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / safe_name

        url = f"{self.pusher.FEISHU_API_BASE}/im/v1/messages/{message_id}/resources/{file_key}"
        params = {"type": "file"}

        with requests.get(url, headers=headers, params=params, stream=True, timeout=self.pusher.timeout) as r:
            r.raise_for_status()
            total = 0
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if not chunk:
                        continue
                    f.write(chunk)
                    total += len(chunk)
                    if self.max_bytes and total > self.max_bytes:
                        raise ValueError(f"file too large: {total} bytes")

        return out_path


import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core.chat.ingest import select_recent_session_files


class TestChatTimestampFilter(unittest.TestCase):
    def test_select_recent_prefers_filename_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            recent_ms = int((datetime.now() - timedelta(days=1)).timestamp() * 1000)
            old_ms = int((datetime.now() - timedelta(days=10)).timestamp() * 1000)

            recent = root / f"{recent_ms}.md"
            old = root / f"{old_ms}.md"
            other = root / "not-a-timestamp.md"
            ignored = root / f"{recent_ms}.png"

            recent.write_text("recent", encoding="utf-8")
            old.write_text("old", encoding="utf-8")
            other.write_text("other", encoding="utf-8")
            ignored.write_bytes(b"\x89PNG\r\n\x1a\n")

            ts_other = (datetime.now() - timedelta(days=2)).timestamp()
            os.utime(other, (ts_other, ts_other))

            picked = select_recent_session_files(str(root), days=7)
            picked_names = [p.name for p in picked]

            self.assertIn(recent.name, picked_names)
            self.assertIn(other.name, picked_names)
            self.assertNotIn(old.name, picked_names)
            self.assertNotIn(ignored.name, picked_names)


if __name__ == "__main__":
    unittest.main()

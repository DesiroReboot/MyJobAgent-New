import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from core.chat.sources import collect_chat_sessions


class TestObsidianAndBackupSources(unittest.TestCase):
    def test_obsidian_source_reads_markdown(self):
        with tempfile.TemporaryDirectory(prefix="obsidian_vault_") as td:
            root = Path(td) / "CherryStudio" / "2026-02-14"
            root.mkdir(parents=True, exist_ok=True)
            p = root / "s1.md"
            p.write_text("domain: cherrystudio\n\nuser: hello\nassistant: hi\n", encoding="utf-8")

            sessions, results = collect_chat_sessions(
                sources=[{"type": "obsidian", "domain": "cherrystudio", "path": str(Path(td) / "CherryStudio")}],
                days=3650,
                max_chars=2000,
                debug=True,
            )
            self.assertGreaterEqual(len(sessions), 1)
            self.assertEqual(results[0].source["type"], "obsidian")
            self.assertEqual(len(results[0].errors), 0)

    def test_cherrystudio_backup_picks_latest_zip(self):
        with tempfile.TemporaryDirectory(prefix="cherry_backup_") as td:
            d = Path(td)
            z1 = d / "cherry-studio.2026-02-13.zip"
            z2 = d / "cherry-studio.2026-02-14.zip"

            with zipfile.ZipFile(z1, "w") as zf:
                zf.writestr("a.json", json.dumps({"messages": [{"role": "user", "content": "old"}]}))
            time.sleep(0.02)
            with zipfile.ZipFile(z2, "w") as zf:
                zf.writestr("b.json", json.dumps({"messages": [{"role": "user", "content": "new"}]}))

            sessions, results = collect_chat_sessions(
                sources=[{"type": "cherrystudio_backup", "domain": "cherrystudio", "path": str(d)}],
                days=3650,
                max_chars=2000,
                debug=True,
            )
            self.assertGreaterEqual(len(sessions), 1)
            self.assertEqual(results[0].source["type"], "cherrystudio_backup")
            self.assertEqual(len(results[0].errors), 0)
            text = sessions[-1].compressed_text
            self.assertIn("new", text)


if __name__ == "__main__":
    unittest.main()


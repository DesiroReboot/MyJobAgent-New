import os
import sqlite3
import tempfile
import unittest
from datetime import datetime

from core.chat.cherrystudio import extract_sessions


class TestCherryStudioReader(unittest.TestCase):
    def test_extract_sessions_from_sqlite_messages_table(self):
        fd, db_path = tempfile.mkstemp(prefix="cherry_", suffix=".sqlite")
        os.close(fd)
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE messages (
                    conversation_id TEXT,
                    role TEXT,
                    content TEXT,
                    created_at INTEGER
                )
                """
            )

            now = datetime.now()
            t0_ms = int(now.timestamp() * 1000) - 2000
            t1_us = int(now.timestamp() * 1000000)

            conn.execute(
                "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                ("c1", "user", "Error: boom https://example.com", t0_ms),
            )
            conn.execute(
                "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                ("c1", "assistant", "Got it. Traceback: ...", t1_us),
            )
            conn.commit()
            conn.close()

            sessions = extract_sessions(days=7, domain="cherrystudio", db_path=db_path, max_chars=2000)
            self.assertEqual(len(sessions), 1)
            s = sessions[0]
            self.assertEqual(s.domain, "cherrystudio")
            self.assertTrue(s.source.startswith("cherrystudio::"))
            self.assertTrue(s.start)
            self.assertTrue(s.end)
            self.assertIn("KEY LINES:", s.compressed_text)
            self.assertIn("https://example.com", s.compressed_text)
            self.assertIn("Traceback", s.compressed_text)
        finally:
            try:
                os.remove(db_path)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()

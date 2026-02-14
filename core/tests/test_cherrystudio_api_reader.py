import json
import os
import threading
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from core.chat.cherrystudio_api import CherryApiConfig, extract_sessions_via_api


class _Handler(BaseHTTPRequestHandler):
    server_version = "CherryMock/1.0"

    def _send_json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path == "/api-docs.json":
            self._send_json(
                {
                    "openapi": "3.0.0",
                    "paths": {
                        "/sessions": {"get": {"responses": {"200": {"description": "ok"}}}},
                        "/sessions/{id}/messages": {"get": {"responses": {"200": {"description": "ok"}}}},
                    },
                }
            )
            return
        if self.path == "/sessions":
            now = datetime.now().isoformat()
            self._send_json([{"id": "s1", "updatedAt": now}])
            return
        if self.path == "/sessions/s1/messages":
            now = datetime.now().isoformat()
            self._send_json(
                [
                    {"role": "user", "content": "hello https://example.com", "createdAt": now},
                    {"role": "assistant", "content": "Traceback: boom", "createdAt": now},
                ]
            )
            return
        self._send_json({"error": "not found"}, code=404)


class TestCherryStudioApiReader(unittest.TestCase):
    def test_extract_sessions_via_api(self):
        httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        time.sleep(0.05)

        old = dict(os.environ)
        try:
            os.environ["CHERRY_API_BASE"] = f"http://127.0.0.1:{port}"
            os.environ.pop("CHERRY_API_HEADER", None)
            os.environ.pop("CHERRY_API_KEY", None)
            cfg = CherryApiConfig.from_env()
            sessions, meta = extract_sessions_via_api(cfg, domain="cherrystudio", days=7, max_chars=2000, debug=True)
            self.assertEqual(meta.get("mode"), "api")
            self.assertGreaterEqual(len(sessions), 1)
            self.assertIn("KEY LINES:", sessions[0].compressed_text)
            self.assertIn("https://example.com", sessions[0].compressed_text)
        finally:
            os.environ.clear()
            os.environ.update(old)
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    unittest.main()

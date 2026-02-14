import unittest
from unittest.mock import patch

from requests.models import Response

from core.llm.llm_client import LLMClient


class TestLLMMeta(unittest.TestCase):
    def _compressed_data(self):
        return {
            "web": {"example": {"title_samples": ["Python Documentation"]}},
            "non_web_samples": {"window": [], "audio": []},
        }

    def test_http_401_sets_meta(self):
        resp = Response()
        resp.status_code = 401
        resp._content = b'{"error":"invalid api key"}'
        resp.headers["Content-Type"] = "application/json"

        with patch("core.llm.llm_client.requests.post", return_value=resp):
            client = LLMClient(
                provider="deepseek",
                api_key="bad",
                model="deepseek-chat",
                timeout=1,
                base_url="https://api.deepseek.com/v1",
            )
            with patch("builtins.print"):
                keywords, meta = client.extract_keywords(self._compressed_data(), return_meta=True)
            self.assertIsInstance(keywords, list)
            self.assertGreater(len(keywords), 0)
            self.assertEqual(meta.get("http_status"), 401)
            self.assertFalse(meta.get("used_llm"))
            self.assertTrue(meta.get("fallback_used"))

    def test_parse_empty_fallback_meta(self):
        client = LLMClient(
            provider="deepseek",
            api_key="x",
            model="deepseek-chat",
            timeout=1,
            base_url="https://api.deepseek.com/v1",
        )
        with patch.object(LLMClient, "_call_llm", return_value="hello"):
            with patch("builtins.print"):
                keywords, meta = client.extract_keywords(self._compressed_data(), return_meta=True)
            self.assertIsInstance(keywords, list)
            self.assertTrue(meta.get("used_llm"))
            self.assertTrue(meta.get("fallback_used"))
            self.assertIsNone(meta.get("http_status"))


if __name__ == "__main__":
    unittest.main()

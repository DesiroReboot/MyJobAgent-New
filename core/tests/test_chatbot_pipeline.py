import unittest

from core.chat.compress import compress_chat_text
from core.chat.merge import merge_keyword_payloads


class TestChatbotPipeline(unittest.TestCase):
    def test_compress_respects_budget(self):
        text = "\n".join([f"line {i} https://example.com Error: boom" for i in range(1000)])
        out = compress_chat_text(text, max_chars=800, max_lines=50)
        self.assertTrue(len(out) <= 800)
        self.assertIn("KEY LINES:", out)

    def test_merge_allocates_pool(self):
        base = {
            "skills_interests": [{"name": "Python", "weight": 0.6, "evidence": {"duration_seconds": 120}}],
            "tools_platforms": [{"name": "GitHub", "weight": 0.4, "evidence": {"duration_seconds": 60}}],
        }
        chat = {
            "skills_interests": [{"name": "FastAPI", "weight": 0.7}, {"name": "PostgreSQL", "weight": 0.3}],
            "tools_platforms": [{"name": "chatgpt.com", "weight": 1.0}],
        }
        merged = merge_keyword_payloads(base, chat, non_chat_total_seconds=600, chatbot_pool_seconds=300)
        skills = merged.get("skills_interests", [])
        tools = merged.get("tools_platforms", [])
        self.assertTrue(any(i.get("name") == "Python" for i in skills))
        self.assertTrue(any(i.get("name") == "FastAPI" for i in skills))
        self.assertTrue(any(i.get("name") == "chatgpt.com" for i in tools))
        self.assertTrue(all(0.0 <= float(i.get("weight", 0.0)) <= 1.0 for i in skills))


if __name__ == "__main__":
    unittest.main()

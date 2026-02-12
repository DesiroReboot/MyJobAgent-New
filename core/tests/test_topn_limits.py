import unittest
from unittest.mock import patch


class TestTopNLimits(unittest.TestCase):
    def test_prompt_supports_separate_limits(self):
        from core.prompts import build_keyword_extraction_prompt

        prompt = build_keyword_extraction_prompt(
            compressed_data={"web": {}, "non_web_samples": {}, "meta": {}},
            min_k=3,
            max_k=10,
            skills_limit=10,
            tools_limit=5,
        )
        self.assertIn("Skills & Interests Top 10", prompt)
        self.assertIn("Tools & Platforms Top 5", prompt)

        prompt_default = build_keyword_extraction_prompt(
            compressed_data={"web": {}, "non_web_samples": {}, "meta": {}},
            min_k=3,
            max_k=10,
        )
        self.assertIn("Skills & Interests Top 5", prompt_default)
        self.assertIn("Tools & Platforms Top 5", prompt_default)

    def test_feishu_pusher_truncates_structured_keywords(self):
        from core.pusher.feishu_pusher import FeishuPusher

        keywords = {
            "skills_interests": [{"name": f"Skill{i}", "weight": 0.9 - i * 0.01, "level": "pass"} for i in range(20)],
            "tools_platforms": [{"name": f"Tool{i}", "weight": 0.8 - i * 0.01, "level": "pass"} for i in range(20)],
        }

        captured = {}

        def fake_post(url, json=None, timeout=None, params=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            class Resp:
                def raise_for_status(self):
                    return None
            return Resp()

        pusher = FeishuPusher(mode="bot", webhook_url="https://example.invalid/webhook")
        with patch("core.pusher.feishu_pusher.requests.post", new=fake_post):
            ok = pusher.push_keywords(
                keywords,
                title_suffix=" (Past 7 Days)",
                skills_limit=10,
                tools_limit=5,
            )
        self.assertTrue(ok)
        text = (captured.get("json") or {}).get("content", {}).get("text", "")

        self.assertIn("Skills & Interests (The What)", text)
        self.assertIn("Tools & Platforms (The Via)", text)
        self.assertIn("1. Skill0", text)
        self.assertIn("10. Skill9", text)
        self.assertNotIn("11. Skill10", text)
        self.assertIn("1. Tool0", text)
        self.assertIn("5. Tool4", text)
        self.assertNotIn("6. Tool5", text)

    def test_feishu_pusher_keeps_filtering_behavior(self):
        from core.pusher.feishu_pusher import FeishuPusher

        keywords = {
            "skills_interests": [
                {"name": "Keep1", "weight": 0.05, "level": "pass"},
                {"name": "Drop1", "weight": 0.049, "level": "pass"},
                {"name": "Drop2", "weight": 0.9, "level": "reject"},
            ],
            "tools_platforms": [
                {"name": "Keep2", "weight": 0.2, "level": "pass"},
                {"name": "Drop3", "weight": 0.01, "level": "pass"},
            ],
        }

        captured = {}

        def fake_post(url, json=None, timeout=None, params=None, headers=None):
            captured["json"] = json
            class Resp:
                def raise_for_status(self):
                    return None
            return Resp()

        pusher = FeishuPusher(mode="bot", webhook_url="https://example.invalid/webhook")
        with patch("core.pusher.feishu_pusher.requests.post", new=fake_post):
            ok = pusher.push_keywords(keywords, skills_limit=10, tools_limit=10)
        self.assertTrue(ok)
        text = (captured.get("json") or {}).get("content", {}).get("text", "")
        self.assertIn("Keep1", text)
        self.assertNotIn("Drop1", text)
        self.assertNotIn("Drop2", text)
        self.assertIn("Keep2", text)
        self.assertNotIn("Drop3", text)

    def test_config_topn_by_days(self):
        from core.config import AppConfig

        cfg = AppConfig.from_file("config.json")
        skills_1, tools_1 = cfg.llm_topn_limits(days=1, default_skills=999, default_tools=999)
        skills_7, tools_7 = cfg.llm_topn_limits(days=7, default_skills=999, default_tools=999)

        self.assertEqual((skills_1, tools_1), (5, 3))
        self.assertEqual((skills_7, tools_7), (10, 5))


if __name__ == "__main__":
    unittest.main()


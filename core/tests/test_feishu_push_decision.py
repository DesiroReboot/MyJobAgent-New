import unittest

from core.pusher.push_gate import compute_feishu_push_decision


class TestFeishuPushDecision(unittest.TestCase):
    def test_skip_when_llm_not_used(self):
        should_push, suffix, reason = compute_feishu_push_decision(
            keyword_count=3,
            llm_meta={"used_llm": False, "fallback_used": True, "http_status": 401, "error": "x"},
            chatbot_meta=None,
            push_on_llm_fallback=False,
        )
        self.assertFalse(should_push)
        self.assertEqual(suffix, "")
        self.assertIn("LLM not used successfully", reason)

    def test_skip_when_fallback_used_by_default(self):
        should_push, suffix, reason = compute_feishu_push_decision(
            keyword_count=3,
            llm_meta={"used_llm": True, "fallback_used": True},
            chatbot_meta=None,
            push_on_llm_fallback=False,
        )
        self.assertFalse(should_push)
        self.assertEqual(suffix, "")
        self.assertIn("fallback used", reason)

    def test_push_when_llm_ok(self):
        should_push, suffix, reason = compute_feishu_push_decision(
            keyword_count=3,
            llm_meta={"used_llm": True, "fallback_used": False},
            chatbot_meta=None,
            push_on_llm_fallback=False,
        )
        self.assertTrue(should_push)
        self.assertEqual(suffix, "")
        self.assertEqual(reason, "")

    def test_push_when_fallback_allowed(self):
        should_push, suffix, reason = compute_feishu_push_decision(
            keyword_count=3,
            llm_meta={"used_llm": True, "fallback_used": True},
            chatbot_meta=None,
            push_on_llm_fallback=True,
        )
        self.assertTrue(should_push)
        self.assertEqual(suffix, " (Fallback)")
        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()

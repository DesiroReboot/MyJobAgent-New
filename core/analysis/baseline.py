import re
from typing import Dict, List, Set


STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "you", "your", "are",
    "how", "what", "why", "when", "where", "use", "using", "into", "over", "new",
    "教程", "下载", "官网", "登录", "注册", "配置", "安装", "使用", "指南", "文档",
}


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return [t.strip().lower() for t in re.split(r"[^A-Za-z0-9\u4e00-\u9fa5]+", text) if t.strip()]


def build_baseline_keywords(compressed_data: Dict, limit: int = 50) -> Set[str]:
    """
    Build a baseline keyword set using simple token frequency.
    """
    chat_token_weight = 0.4
    if isinstance(compressed_data, dict):
        analysis_cfg = compressed_data.get("analysis", {}) or {}
        token_weights = analysis_cfg.get("token_weights", {}) if isinstance(analysis_cfg, dict) else {}
        if isinstance(token_weights, dict) and "chat_sessions" in token_weights:
            try:
                chat_token_weight = float(token_weights.get("chat_sessions"))
            except Exception:
                chat_token_weight = 0.4
        else:
            chatbot_cfg = compressed_data.get("chatbot", {}) or {}
            if isinstance(chatbot_cfg, dict) and "token_weight" in chatbot_cfg:
                try:
                    chat_token_weight = float(chatbot_cfg.get("token_weight"))
                except Exception:
                    chat_token_weight = 0.4
    chat_token_weight = max(0.0, min(5.0, float(chat_token_weight)))

    counter: Dict[str, float] = {}
    web = compressed_data.get("web", {}) if isinstance(compressed_data, dict) else {}
    for stats in web.values():
        for title in stats.get("title_freq", {}).keys():
            for t in _tokenize(title):
                if len(t) <= 1 or t in STOPWORDS:
                    continue
                counter[t] = counter.get(t, 0.0) + 1.0

    non_web = compressed_data.get("non_web_samples", {}) if isinstance(compressed_data, dict) else {}
    for sample in non_web.get("window", []) or []:
        for t in _tokenize(sample.get("title", "")):
            if len(t) <= 1 or t in STOPWORDS:
                continue
            counter[t] = counter.get(t, 0.0) + 0.6

    for sample in non_web.get("audio", []) or []:
        for t in _tokenize(sample.get("title", "")):
            if len(t) <= 1 or t in STOPWORDS:
                continue
            counter[t] = counter.get(t, 0.0) + 0.4

    chat_sessions = compressed_data.get("chat_sessions", []) if isinstance(compressed_data, dict) else []
    if isinstance(chat_sessions, list):
        for sess in chat_sessions:
            if not isinstance(sess, dict):
                continue
            text = str(sess.get("compressed_text", "") or "")
            for t in _tokenize(text):
                if len(t) <= 1 or t in STOPWORDS:
                    continue
                counter[t] = counter.get(t, 0.0) + chat_token_weight

    if not counter:
        return set()

    sorted_items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return set([k for k, _ in sorted_items[:limit]])


def compute_overlap(candidate: str, baseline_set: Set[str]) -> float:
    if not candidate:
        return 0.0
    return 1.0 if candidate.lower().strip() in baseline_set else 0.0

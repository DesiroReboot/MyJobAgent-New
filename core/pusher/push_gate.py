from typing import Optional, Tuple


def compute_feishu_push_decision(
    *,
    keyword_count: int,
    llm_meta: Optional[dict],
    chatbot_meta: Optional[dict],
    push_on_llm_fallback: bool,
) -> Tuple[bool, str, str]:
    llm_fallback = bool((llm_meta or {}).get("fallback_used", True))
    llm_used = bool((llm_meta or {}).get("used_llm", False))
    chat_fallback = bool((chatbot_meta or {}).get("fallback_used", False)) if chatbot_meta is not None else False
    chat_used = bool((chatbot_meta or {}).get("used_llm", False)) if chatbot_meta is not None else False
    any_fallback = llm_fallback or chat_fallback
    any_used_llm = llm_used or chat_used

    should_push = keyword_count > 0 and any_used_llm and (push_on_llm_fallback or not any_fallback)
    title_fallback_suffix = " (Fallback)" if any_fallback and push_on_llm_fallback else ""

    if should_push:
        return True, title_fallback_suffix, ""

    reason_parts = []
    if keyword_count <= 0:
        reason_parts.append("no keywords")
    if not any_used_llm:
        reason_parts.append("LLM not used successfully")
    if any_fallback and not push_on_llm_fallback:
        reason_parts.append("fallback used")
    reason = ", ".join(reason_parts) if reason_parts else "unknown"
    return False, title_fallback_suffix, reason

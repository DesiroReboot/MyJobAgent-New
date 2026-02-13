import re
from typing import Dict, List, Tuple


def normalize_keyword_name(name: str) -> str:
    if not name:
        return ""
    s = name.strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[·•]+", " ", s)
    return s.strip()


def _get_evidence_duration_seconds(item: Dict) -> int:
    evidence = item.get("evidence", {}) if isinstance(item, dict) else {}
    try:
        return int(evidence.get("duration_seconds", 0) or 0)
    except Exception:
        return 0


def attach_abs_weight_seconds_from_evidence(items: List[Dict], fallback_total_seconds: int) -> None:
    if not items:
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        dur = _get_evidence_duration_seconds(item)
        if dur > 0:
            item["abs_weight_seconds"] = dur
            continue
        try:
            w = float(item.get("weight", 0.0) or 0.0)
        except Exception:
            w = 0.0
        item["abs_weight_seconds"] = int(max(0.0, min(1.0, w)) * max(0, int(fallback_total_seconds)))


def attach_abs_weight_seconds_from_pool(items: List[Dict], pool_seconds: int) -> None:
    if not items:
        return
    weights: List[Tuple[Dict, float]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            w = float(item.get("weight", 0.0) or 0.0)
        except Exception:
            w = 0.0
        weights.append((item, max(0.0, w)))
    denom = sum(w for _, w in weights)
    if denom <= 0:
        denom = float(len(weights) or 1)
        weights = [(it, 1.0) for it, _ in weights]
    for item, w in weights:
        item["abs_weight_seconds"] = int(max(0, int(pool_seconds)) * (w / denom))


def merge_items_by_name(items: List[Dict]) -> List[Dict]:
    merged: Dict[str, Dict] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        key = normalize_keyword_name(name)
        if not key:
            continue
        abs_w = 0
        try:
            abs_w = int(item.get("abs_weight_seconds", 0) or 0)
        except Exception:
            abs_w = 0
        if key not in merged:
            merged[key] = dict(item)
            merged[key]["name"] = name
            merged[key]["abs_weight_seconds"] = abs_w
            continue
        prev = merged[key]
        prev_abs = int(prev.get("abs_weight_seconds", 0) or 0)
        prev["abs_weight_seconds"] = prev_abs + abs_w
        if float(item.get("weight", 0.0) or 0.0) > float(prev.get("weight", 0.0) or 0.0):
            prev["weight"] = item.get("weight", prev.get("weight", 0.0))
        for field in ("evidence_quote", "source_domain"):
            if field in item and item.get(field) and not prev.get(field):
                prev[field] = item.get(field)
    return list(merged.values())


def normalize_weights_from_abs(items: List[Dict]) -> None:
    if not items:
        return
    max_abs = max([int(i.get("abs_weight_seconds", 0) or 0) for i in items] or [0])
    if max_abs <= 0:
        return
    for item in items:
        try:
            abs_w = int(item.get("abs_weight_seconds", 0) or 0)
        except Exception:
            abs_w = 0
        item["weight"] = max(0.0, min(1.0, float(abs_w) / float(max_abs)))


def merge_keyword_payloads(
    base_payload: Dict,
    chatbot_payload: Dict,
    non_chat_total_seconds: int,
    chatbot_pool_seconds: int,
) -> Dict:
    out = {"skills_interests": [], "tools_platforms": []}

    base_skills = (base_payload or {}).get("skills_interests", []) or []
    base_tools = (base_payload or {}).get("tools_platforms", []) or []
    chat_skills = (chatbot_payload or {}).get("skills_interests", []) or []
    chat_tools = (chatbot_payload or {}).get("tools_platforms", []) or []

    attach_abs_weight_seconds_from_evidence(base_skills, fallback_total_seconds=non_chat_total_seconds)
    attach_abs_weight_seconds_from_evidence(base_tools, fallback_total_seconds=non_chat_total_seconds)
    attach_abs_weight_seconds_from_pool(chat_skills, pool_seconds=chatbot_pool_seconds)
    attach_abs_weight_seconds_from_pool(chat_tools, pool_seconds=chatbot_pool_seconds)

    merged_skills = merge_items_by_name(list(base_skills) + list(chat_skills))
    merged_tools = merge_items_by_name(list(base_tools) + list(chat_tools))

    normalize_weights_from_abs(merged_skills)
    normalize_weights_from_abs(merged_tools)

    merged_skills.sort(key=lambda x: int(x.get("abs_weight_seconds", 0) or 0), reverse=True)
    merged_tools.sort(key=lambda x: int(x.get("abs_weight_seconds", 0) or 0), reverse=True)

    out["skills_interests"] = merged_skills
    out["tools_platforms"] = merged_tools
    return out


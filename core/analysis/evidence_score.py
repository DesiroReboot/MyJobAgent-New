from typing import Dict, List, Tuple


def _normalize(text: str) -> str:
    return (text or "").lower().strip()


def build_title_entries(compressed_data: Dict) -> List[Dict]:
    """
    Build a list of title entries with estimated durations.
    Each entry: {"title": str, "count": int, "duration": float}
    """
    entries: List[Dict] = []
    web = compressed_data.get("web", {}) if isinstance(compressed_data, dict) else {}

    for domain, stats in web.items():
        title_freq = stats.get("title_freq", {}) or {}
        total_active = float(stats.get("dur", {}).get("active_seconds", 0) or 0)
        total_count = sum(title_freq.values()) or 0
        
        # Add domain as evidence (avoids missing evidence when title is cleaned or generic)
        if domain:
            entries.append({"title": domain, "count": int(total_count), "duration": float(total_active)})

        for title, count in title_freq.items():
            if not title:
                continue
            est_duration = 0.0
            if total_count:
                est_duration = total_active * (float(count) / float(total_count))
            entries.append({"title": title, "count": int(count), "duration": float(est_duration)})

    non_web = compressed_data.get("non_web_samples", {}) if isinstance(compressed_data, dict) else {}
    for sample in non_web.get("window", []) or []:
        # Handle App-Grouped structure (titles is a list)
        titles = sample.get("titles", [])
        # Also support legacy single title if present
        if "title" in sample and sample["title"]:
            titles.append(sample["title"])
            
        for title in titles:
            if title:
                entries.append({"title": title, "count": 1, "duration": float(sample.get("duration", 0) or 0)})

    for sample in non_web.get("audio", []) or []:
        title = sample.get("title") or ""
        if title:
            entries.append({"title": title, "count": 1, "duration": float(sample.get("duration", 0) or 0)})

    return entries


def compute_evidence_features(candidate: str, title_entries: List[Dict]) -> Dict:
    """
    Compute evidence features for a candidate keyword.
    """
    key = _normalize(candidate)
    support_count = 0
    duration_seconds = 0.0
    example_titles: List[str] = []
    distinct_titles = set()

    for entry in title_entries:
        title = entry.get("title", "")
        if not title:
            continue
        title_norm = _normalize(title)
        if key and key in title_norm:
            count = int(entry.get("count", 0) or 0)
            duration = float(entry.get("duration", 0) or 0)
            support_count += max(0, count)
            duration_seconds += max(0.0, duration)
            distinct_titles.add(title)
            if len(example_titles) < 3:
                example_titles.append(title)

    evidence_types = []
    if support_count > 0:
        evidence_types.append("exact")
    if duration_seconds > 0:
        evidence_types.append("duration")
    if example_titles:
        evidence_types.append("title")
        evidence_types.append("context")

    context_snippet = example_titles[0] if example_titles else ""

    return {
        "support_count": support_count,
        "duration_seconds": int(duration_seconds),
        "distinct_title_count": len(distinct_titles),
        "example_titles": example_titles,
        "context_snippet": context_snippet,
        "evidence_types": evidence_types,
    }


def score_evidence(features: Dict, max_support: int, max_duration: int, max_titles: int) -> float:
    """
    Compute normalized evidence score in [0,1].
    """
    support = float(features.get("support_count", 0) or 0)
    duration = float(features.get("duration_seconds", 0) or 0)
    titles = float(features.get("distinct_title_count", 0) or 0)

    support_norm = support / max_support if max_support > 0 else 0.0
    duration_norm = duration / max_duration if max_duration > 0 else 0.0
    titles_norm = titles / max_titles if max_titles > 0 else 0.0

    # Weighted sum; adjust later if needed
    score = 0.4 * support_norm + 0.4 * duration_norm + 0.2 * titles_norm
    return max(0.0, min(1.0, score))

from typing import Dict, List, Tuple, Union

from .baseline import build_baseline_keywords, compute_overlap
from .consistency import compute_consistency
from .evidence_score import build_title_entries, compute_evidence_features, score_evidence
from .thresholds import compute_quantile_thresholds
from .conflict_resolver import assign_level, merge_candidate


def _flatten_keywords(keywords: Union[List[Dict], Dict[str, List]]) -> List[Dict]:
    if isinstance(keywords, dict):
        items: List[Dict] = []
        for key in ("skills_interests", "tools_platforms"):
            for item in keywords.get(key, []) or []:
                items.append(item)
        return items
    if isinstance(keywords, list):
        return keywords
    return []


def _extract_names(items: List[Dict]) -> List[str]:
    names: List[str] = []
    for item in items:
        name = str(item.get("name", "")).strip()
        if name:
            names.append(name)
    return names


def _annotate_list(items: List[Dict], compressed_data: Dict, consistency_scores: Dict[str, float]) -> List[Dict]:
    title_entries = build_title_entries(compressed_data)
    baseline_set = build_baseline_keywords(compressed_data)

    features_map: Dict[str, Dict] = {}
    scores_raw: List[float] = []

    for item in items:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        features = compute_evidence_features(name, title_entries)
        features_map[name] = features

    max_support = max([f.get("support_count", 0) for f in features_map.values()] or [0])
    max_duration = max([f.get("duration_seconds", 0) for f in features_map.values()] or [0])
    max_titles = max([f.get("distinct_title_count", 0) for f in features_map.values()] or [0])

    evidence_scores: Dict[str, float] = {}
    for name, features in features_map.items():
        score = score_evidence(features, max_support, max_duration, max_titles)
        evidence_scores[name] = score
        scores_raw.append(score)

    thresholds = compute_quantile_thresholds(scores_raw)
    t0 = thresholds.get("t0", 0.0)
    t1 = thresholds.get("t1", 0.0)

    annotated: List[Dict] = []
    for item in items:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        evidence = features_map.get(name, {})
        evidence_score = evidence_scores.get(name, 0.0)
        consistency_score = consistency_scores.get(name, 1.0)
        baseline_overlap = compute_overlap(name, baseline_set)

        level = assign_level(evidence_score, consistency_score, baseline_overlap, t0, t1)
        scores = {
            "evidence": round(evidence_score, 4),
            "consistency": round(consistency_score, 4),
            "baseline_overlap": round(baseline_overlap, 4),
        }

        annotated.append(merge_candidate(item, evidence, scores, level))

    return annotated


def annotate_keywords(
    keywords: Union[List[Dict], Dict[str, List]],
    compressed_data: Dict,
    consistency_runs: List[List[str]] = None,
) -> Union[List[Dict], Dict[str, List]]:
    """
    Enrich keywords with evidence/scores/level.
    """
    if not keywords:
        return keywords

    runs = consistency_runs or []
    if not runs:
        base_items = _flatten_keywords(keywords)
        runs = [ _extract_names(base_items) ]

    consistency_scores = compute_consistency(runs)

    if isinstance(keywords, dict):
        out = dict(keywords)
        if "skills_interests" in out:
            out["skills_interests"] = _annotate_list(out.get("skills_interests", []) or [], compressed_data, consistency_scores)
        if "tools_platforms" in out:
            out["tools_platforms"] = _annotate_list(out.get("tools_platforms", []) or [], compressed_data, consistency_scores)
        return out

    if isinstance(keywords, list):
        return _annotate_list(keywords, compressed_data, consistency_scores)

    return keywords

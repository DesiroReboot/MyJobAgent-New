from typing import Dict


def assign_level(evidence_score: float, consistency_score: float, baseline_overlap: float, t0: float, t1: float) -> str:
    if evidence_score < t0:
        return "reject"

    # Pass if all strong signals present
    if evidence_score >= t1 and consistency_score >= 0.6 and baseline_overlap >= 0.5:
        return "pass"

    return "weak"


def merge_candidate(candidate: Dict, evidence: Dict, scores: Dict, level: str) -> Dict:
    merged = dict(candidate)
    merged["evidence"] = evidence
    merged["scores"] = scores
    merged["level"] = level
    return merged

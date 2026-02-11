from typing import List, Dict


def _quantile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q
    lower = int(pos)
    upper = min(lower + 1, len(values) - 1)
    if lower == upper:
        return values[lower]
    frac = pos - lower
    return values[lower] * (1 - frac) + values[upper] * frac


def compute_quantile_thresholds(values: List[float]) -> Dict[str, float]:
    """
    Compute dynamic thresholds using P50 / P80.
    """
    t0 = _quantile(values, 0.5)
    t1 = _quantile(values, 0.8)
    return {"t0": t0, "t1": t1}


def bucket_score(score: float, t0: float, t1: float) -> str:
    if score < t0:
        return "low"
    if score < t1:
        return "mid"
    return "high"

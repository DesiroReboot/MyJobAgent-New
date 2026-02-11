from typing import Dict, List


def compute_consistency(candidates_runs: List[List[str]]) -> Dict[str, float]:
    """
    Compute consistency score for each keyword across multiple runs.
    Score = occurrence count / total runs.
    """
    if not candidates_runs:
        return {}

    total_runs = len(candidates_runs)
    counts: Dict[str, int] = {}
    for run in candidates_runs:
        seen = set()
        for name in run:
            if not name:
                continue
            if name in seen:
                continue
            seen.add(name)
            counts[name] = counts.get(name, 0) + 1

    return {k: v / float(total_runs) for k, v in counts.items()}

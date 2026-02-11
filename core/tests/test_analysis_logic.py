
import unittest
from core.analysis.evidence_score import score_evidence
from core.analysis.conflict_resolver import assign_level

class TestAnalysisLogic(unittest.TestCase):
    
    def test_score_evidence_basic(self):
        # Case 1: Perfect match (max support, max duration)
        features = {
            "support_count": 100,
            "duration_seconds": 3600,
            "distinct_title_count": 10
        }
        score = score_evidence(features, max_support=100, max_duration=3600, max_titles=10)
        # Should be close to 1.0 (depending on weights)
        self.assertGreater(score, 0.8)

    def test_score_evidence_zero(self):
        # Case 2: No evidence
        features = {
            "support_count": 0,
            "duration_seconds": 0,
            "distinct_title_count": 0
        }
        score = score_evidence(features, max_support=100, max_duration=3600, max_titles=10)
        self.assertEqual(score, 0.0)

    def test_assign_level_pass(self):
        # High scores -> Pass
        # t0=0.2, t1=0.5
        level = assign_level(evidence_score=0.8, consistency_score=0.9, baseline_overlap=0.8, t0=0.2, t1=0.5)
        self.assertEqual(level, "pass")

    def test_assign_level_reject(self):
        # Low evidence -> Reject
        level = assign_level(evidence_score=0.1, consistency_score=0.9, baseline_overlap=0.8, t0=0.2, t1=0.5)
        self.assertEqual(level, "reject")

    def test_assign_level_weak(self):
        # Medium evidence, but low consistency/baseline -> Weak
        level = assign_level(evidence_score=0.6, consistency_score=0.4, baseline_overlap=0.2, t0=0.2, t1=0.5)
        self.assertEqual(level, "weak")

if __name__ == '__main__':
    unittest.main()

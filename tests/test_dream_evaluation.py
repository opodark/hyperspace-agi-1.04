# SPDX-License-Identifier: Apache-2.0
import unittest

from shared.dream_evaluation import evaluate_dreams


def dream(dream_id, status="hypothesis", summary="", source_refs=None, reviews=None, duration_ms=None):
    return {
        "schema_version": 1,
        "id": dream_id,
        "status": status,
        "summary": summary,
        "response": "",
        "source_refs": source_refs or [],
        "source_memory_ids": [ref["id"] for ref in (source_refs or [])],
        "reviews": reviews or [],
        "duration_ms": duration_ms,
    }


class DreamEvaluationTests(unittest.TestCase):
    def test_utility_counts_promoted_hypotheses(self):
        dreams = [
            dream("d1", "promoted", "a", reviews=[{"action": "promote"}]),
            dream("d2", "rejected", "b", reviews=[{"action": "reject"}]),
            dream("d3", "hypothesis", "c"),
        ]
        result = evaluate_dreams(dreams)
        metrics = result["metrics"]
        self.assertEqual(result["dataset"]["promoted"], 1)
        self.assertEqual(metrics["utility"]["numerator"], 1)
        self.assertEqual(metrics["utility"]["denominator"], 3)
        self.assertAlmostEqual(metrics["utility"]["value"], 1 / 3)

    def test_traceability_requires_cited_sources(self):
        dreams = [
            dream("d1", summary="con fonte", source_refs=[{"id": "m1"}]),
            dream("d2", summary="senza fonte"),
        ]
        metrics = evaluate_dreams(dreams)["metrics"]
        self.assertEqual(metrics["traceability"]["numerator"], 1)
        self.assertEqual(metrics["traceability"]["coverage"], 1.0)

    def test_novelty_requires_memory_corpus(self):
        dreams = [dream("d1", summary="una nuova idea mai vista")]
        result = evaluate_dreams(dreams)
        self.assertIsNone(result["metrics"]["novelty"]["value"])
        self.assertEqual(result["metrics"]["novelty"]["coverage"], 0.0)

        with_corpus = evaluate_dreams(
            dreams,
            memories=[{"content": "contenuto del tutto diverso con parole lunghe"}],
        )
        self.assertEqual(with_corpus["metrics"]["novelty"]["numerator"], 1)

    def test_novelty_detects_near_duplicate(self):
        dreams = [dream("d1", summary="il gatto dorme sul divano")]
        memories = [{"content": "il gatto dorme sul divano"}]
        metrics = evaluate_dreams(dreams, memories=memories)["metrics"]
        self.assertEqual(metrics["novelty"]["numerator"], 0)

    def test_correctness_and_cost_report_coverage_when_unmeasured(self):
        result = evaluate_dreams([dream("d1")])
        self.assertIsNone(result["metrics"]["correctness"]["value"])
        self.assertEqual(result["metrics"]["correctness"]["coverage"], 0.0)
        self.assertIsNone(result["metrics"]["cost"]["value"])
        self.assertEqual(result["metrics"]["cost"]["coverage"], 0.0)

    def test_cost_is_measured_when_duration_is_instrumented(self):
        dreams = [
            dream("d1", "promoted", "a", duration_ms=500),
            dream("d2", "rejected", "b", duration_ms=300),
        ]
        metrics = evaluate_dreams(dreams)["metrics"]
        self.assertEqual(metrics["cost"]["coverage"], 1.0)
        self.assertEqual(metrics["cost"]["value"], 800)  # (500 + 300) ms / 1 promoted

    def test_ready_for_d4_requires_memories_and_false_positive_review(self):
        dreams = [dream("d1", "promoted", reviews=[{"quality": "correct"}])]
        without = evaluate_dreams(dreams)
        self.assertFalse(without["ready_for_d4"]["ready"])
        with_all = evaluate_dreams(dreams, memories=[{"content": "memoria reale"}])
        self.assertTrue(with_all["ready_for_d4"]["ready"])


if __name__ == "__main__":
    unittest.main()

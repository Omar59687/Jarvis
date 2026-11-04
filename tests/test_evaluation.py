from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from eval.runner import DATASET, MODEL, evaluate, load_dataset
from jarvis_core.ollama import GenerationSettings
from jarvis_core.rag import RAGOutcome
from jarvis_core import GenerationResult


class InvalidCitationLiveClient:
    allowed_models = frozenset({MODEL})

    def generate(self, prompt, model, *, system_prompt=None, settings=GenerationSettings()):
        return GenerationResult(model=model,
                                text="The invented project codename is Cedar Lantern [D99].",
                                done=True)


class EvaluationFrameworkTests(unittest.TestCase):
    def test_versioned_dataset_has_unique_cases_and_synthetic_sources(self):
        dataset = load_dataset()
        self.assertEqual(dataset["version"], 1)
        self.assertGreaterEqual(len(dataset["cases"]), 12)
        languages = {case["language"] for case in dataset["cases"]}
        self.assertIn("en", languages)
        self.assertIn("ar", languages)
        self.assertTrue(any("→" in language for language in languages))

    def test_offline_runner_exercises_outcomes_security_and_writes_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = evaluate(output_dir=Path(temporary))
            self.assertEqual(report["mode"], "offline_deterministic")
            self.assertGreater(report["metrics"]["retrieval_recall_at_k"]["denominator"], 0)
            metrics = report["metrics"]
            self.assertEqual(metrics["ordinary_citation_compliance"]["numerator"], 7)
            self.assertEqual(metrics["ordinary_citation_compliance"]["denominator"], 7)
            self.assertEqual(metrics["validator_negative_fixture_rejection"]["numerator"], 1)
            self.assertEqual(metrics["validator_negative_fixture_rejection"]["denominator"], 1)
            security = metrics["security_evaluation"]
            self.assertEqual(security["deterministic_workspace_lifecycle_checks"]["numerator"], 2)
            self.assertEqual(security["deterministic_workspace_lifecycle_checks"]["denominator"], 2)
            self.assertEqual(security["fake_prompt_boundary_regression"]["value"], 1.0)
            self.assertEqual(security["live_model_prompt_injection_resistance"]["status"], "not_evaluated")
            self.assertEqual(security["live_model_prompt_injection_resistance"]["denominator"], 0)
            self.assertTrue(all(not row["case_failures"] for row in report["cases"]))
            self.assertTrue(all(row["answer_correctness"] is None for row in report["cases"]))
            by_id = {row["case_id"]: row for row in report["cases"]}
            self.assertFalse(by_id["missing-en-01"]["model_called"])
            self.assertEqual(by_id["historical-en-01"]["retrieved_sources"][0]["source_status"],
                             "historical_sources")
            ids = {row["case_id"] for row in report["cases"]}
            self.assertIn("malformed-citation-01", ids)
            self.assertIn("incomplete-01", ids)
            malformed = by_id["malformed-citation-01"]
            self.assertEqual(malformed["ordinary_citation_compliant"], None)
            self.assertEqual(malformed["negative_validator_fixture_status"], "passed")
            self.assertIn("malformed citation was rejected", malformed["case_pass_reason"])
            self.assertEqual(len(report["unscored_cases"]), 7)
            self.assertTrue((Path(temporary) / "m02_d_report.json").exists())
            markdown = (Path(temporary) / "m02_d_report.md").read_text(encoding="utf-8")
            self.assertIn("Numerator / denominator", markdown)
            self.assertIn("unverified", markdown)
            self.assertIn("not_evaluated", markdown)
            self.assertIn("excluded from ordinary citation compliance", markdown)

    def test_dataset_is_valid_json(self):
        self.assertEqual(json.loads(DATASET.read_text(encoding="utf-8"))["version"], 1)

    def test_rejected_invalid_citation_does_not_get_literal_answer_credit(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = evaluate(live_client=InvalidCitationLiveClient(), model=MODEL,
                              output_dir=Path(temporary))
        self.assertEqual(report["metrics"]["grounded_answer_literal_correctness"]["numerator"], 0)
        self.assertEqual(report["metrics"]["grounded_answer_literal_correctness"]["denominator"], 0)
        case = next(item for item in report["cases"] if item["case_id"] == "doc-en-01")
        self.assertEqual(case["outcome"], RAGOutcome.INVALID_CITATIONS.value)
        self.assertIsNone(case["answer_correctness"])
        unscored = next(item for item in report["unscored_cases"]
                        if item["case_id"] == "doc-en-01"
                        and item["metric"] == "grounded_answer_literal_correctness")
        self.assertEqual(unscored["status"], "not_evaluated")
        self.assertIn("RAG rejected", unscored["reason"])


if __name__ == "__main__":
    unittest.main()

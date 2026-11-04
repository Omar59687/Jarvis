from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from eval.runner import DATASET, evaluate, load_dataset


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
            self.assertGreaterEqual(report["metrics"]["security_checks"]["denominator"], 3)
            self.assertEqual(report["metrics"]["security_checks"]["numerator"],
                             report["metrics"]["security_checks"]["denominator"])
            self.assertTrue(all(not row["case_failures"] for row in report["cases"]))
            self.assertTrue(all(row["answer_correctness"] is None for row in report["cases"]))
            ids = {row["case_id"] for row in report["cases"]}
            self.assertIn("malformed-citation-01", ids)
            self.assertIn("incomplete-01", ids)
            self.assertTrue((Path(temporary) / "m02_d_report.json").exists())
            markdown = (Path(temporary) / "m02_d_report.md").read_text(encoding="utf-8")
            self.assertIn("Numerator / denominator", markdown)
            self.assertIn("unverified", markdown)

    def test_dataset_is_valid_json(self):
        self.assertEqual(json.loads(DATASET.read_text(encoding="utf-8"))["version"], 1)


if __name__ == "__main__":
    unittest.main()

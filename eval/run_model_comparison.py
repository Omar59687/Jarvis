"""Opt-in sequential comparison of two already-installed local Ollama models."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.runner import evaluate
from jarvis_core.ollama import OllamaClient

MODELS = ("gemma3:4b", "llama3.2:latest")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("eval/results/model-comparison"))
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    preflight = OllamaClient("http://127.0.0.1:11434", MODELS, timeout=args.timeout)
    installed = set(preflight.installed_models())
    missing = [model for model in MODELS if model not in installed]
    if missing:
        parser.error("models not installed locally: " + ", ".join(missing) + "; no model downloads are performed")

    # Deliberately sequential. Each model gets its own client and full report;
    # do not rank or name a winner from one local run.
    summaries = []
    for model in MODELS:
        client = OllamaClient("http://127.0.0.1:11434", [model], timeout=args.timeout)
        report_dir = args.output_dir / model.replace(":", "_")
        report = evaluate(live_client=client, model=model, output_dir=report_dir)
        metrics = report["metrics"]
        historical = next(row for row in report["cases"] if row["case_id"] == "historical-en-01")
        summaries.append({
            "model": model,
            "report_dir": str(report_dir),
            "ordinary_citation_compliance": metrics["ordinary_citation_compliance"],
            "grounded_answer_literal_correctness": metrics["grounded_answer_literal_correctness"],
            "abstention_accuracy": metrics["abstention_accuracy"],
            "historical_memory": {key: historical.get(key) for key in
                                  ("outcome", "expected_outcome", "answer_correctness", "abstained", "elapsed_ms")},
            "end_to_end_latency_ms": metrics["latency_ms"],
            "ollama_reported_durations_ns": metrics["ollama_reported_durations_ns"],
            "ollama_reported_token_counts": metrics["ollama_reported_token_counts"],
            "human_review_cases": [item["case_id"] for item in report["human_review"]],
            "unscored_cases": report["unscored_cases"],
            "winner": None,
        })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = {"comparison_version": 1, "mode": "sequential_local_only", "winner": None,
              "models": summaries,
              "note": "No winner is declared. Review Windows measurements, human-review cases, and run-to-run variance."}
    (args.output_dir / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                                     encoding="utf-8")
    lines = ["# Local Model Comparison", "", "Sequential reports for already-installed models; no winner declared.", "",
             "Model | Citation compliance | Literal correctness | Abstention accuracy | Historical case | E2E mean ms",
             "---|---:|---:|---:|---|---:"]
    for item in summaries:
        def ratio(metric):
            return f"{metric['numerator']}/{metric['denominator']}" if metric["denominator"] else "n/a (0 denominator)"
        lines.append(f"{item['model']} | {ratio(item['ordinary_citation_compliance'])} | "
                     f"{ratio(item['grounded_answer_literal_correctness'])} | {ratio(item['abstention_accuracy'])} | "
                     f"{item['historical_memory']['outcome']} | {item['end_to_end_latency_ms']['mean']}")
    (args.output_dir / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Sequential local reports written to {args.output_dir}; no winner declared.")


if __name__ == "__main__":
    main()

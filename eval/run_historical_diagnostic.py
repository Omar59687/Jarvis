"""Opt-in, one-case local Ollama diagnostic for historical-en-01."""
from __future__ import annotations

import argparse
from pathlib import Path

from eval.runner import evaluate, render_markdown
from jarvis_core.ollama import OllamaClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=("gemma3:4b", "llama3.2:latest"))
    parser.add_argument("--output-dir", type=Path, default=Path("eval/results/historical-diagnostic"))
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    client = OllamaClient("http://127.0.0.1:11434", [args.model], timeout=args.timeout)
    if args.model not in client.installed_models():
        parser.error(f"{args.model} is not installed locally; this command never downloads models")
    report = evaluate(live_client=client, model=args.model, output_dir=args.output_dir,
                      case_ids={"historical-en-01"}, diagnostic_case_ids={"historical-en-01"})
    print(render_markdown(report))
    print(f"Synthetic prompt/evidence diagnostic: {args.output_dir / 'historical-en-01_diagnostic.json'}")


if __name__ == "__main__":
    main()

"""Opt-in sequential local Ollama evaluation; run only with an installed model."""
from __future__ import annotations

import argparse
from pathlib import Path

from jarvis_core.ollama import OllamaClient
from eval.runner import evaluate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=("gemma3:4b",),
                        help="explicitly select an installed, allowlisted local model")
    parser.add_argument("--output-dir", type=Path, default=Path("eval/results/live"))
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    client = OllamaClient("http://127.0.0.1:11434", [args.model], timeout=args.timeout)
    report = evaluate(live_client=client, model=args.model, output_dir=args.output_dir)
    from eval.runner import render_markdown
    print(render_markdown(report))


if __name__ == "__main__":
    main()

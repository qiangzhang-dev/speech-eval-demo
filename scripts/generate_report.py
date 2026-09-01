#!/usr/bin/env python3
"""Generate an archivable JSON or HTML summary from an evaluation database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from speech_eval.config import load_threshold_config  # noqa: E402
from speech_eval.reporting import build_summary, write_report  # noqa: E402
from speech_eval.repository import EvaluationRepository  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="var/evaluation.db")
    parser.add_argument("--output", required=True, help="a .json or .html report path")
    parser.add_argument("--batch-id")
    parser.add_argument("--run-id")
    parser.add_argument("--thresholds", default="config/thresholds.json")
    args = parser.parse_args(argv)
    try:
        threshold = load_threshold_config(args.thresholds)
        with EvaluationRepository(args.database) as repository:
            output = write_report(
                repository,
                args.output,
                batch_id=args.batch_id,
                run_id=args.run_id,
                threshold_config=threshold,
            )
            summary = build_summary(
                repository,
                batch_id=args.batch_id,
                run_id=args.run_id,
                threshold_config=threshold,
            )
        print(json.dumps({"output": str(output), "summary": summary}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

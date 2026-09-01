#!/usr/bin/env python3
"""Verify that the Web UI persisted this recording's human revision."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from speech_eval.repository import EvaluationRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sample-id", default="SYN-0001")
    parser.add_argument("--marker", required=True)
    args = parser.parse_args()

    with EvaluationRepository(args.database) as repository:
        result = repository.get_result(args.sample_id, run_id=args.run_id)
        revisions = [
            item.to_dict()
            for item in repository.list_revisions(args.sample_id)
            if item.run_id == args.run_id
        ]
    matching = [
        item
        for item in revisions
        if args.marker in json.dumps(item, ensure_ascii=False, default=str)
    ]
    human_revision = result.human_revision if result is not None else None
    marker_in_result = args.marker in json.dumps(human_revision, ensure_ascii=False, default=str)
    payload = {
        "ok": bool(result is not None and matching and marker_in_result),
        "sample_id": args.sample_id,
        "run_id": args.run_id,
        "revision_marker": args.marker,
        "matching_revision_count": len(matching),
        "editor": matching[-1].get("editor") if matching else None,
        "edited_at": matching[-1].get("edited_at") if matching else None,
        "field_name": matching[-1].get("field_name") if matching else None,
        "before": matching[-1].get("before") if matching else None,
        "after": matching[-1].get("after") if matching else None,
        "human_revision_contains_marker": marker_in_result,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

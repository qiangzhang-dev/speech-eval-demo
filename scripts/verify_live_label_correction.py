#!/usr/bin/env python3
"""Verify that a recorded Web label correction invalidated derived analysis."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from speech_eval.repository import EvaluationRepository  # noqa: E402
from speech_eval.validation import validate_result  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sample-id", default="SYN-0001")
    parser.add_argument("--marker", required=True)
    parser.add_argument("--expected-subscene", required=True)
    args = parser.parse_args()

    with EvaluationRepository(args.database) as repository:
        result = repository.get_result(args.sample_id, run_id=args.run_id)
        revisions = [
            item.to_dict()
            for item in repository.list_revisions(args.sample_id)
            if item.run_id == args.run_id
        ]
        logs = repository.list_logs(sample_id=args.sample_id, run_id=args.run_id)

    evidence = result.evidence if result is not None and isinstance(result.evidence, list) else []
    correction_evidence = [
        item for item in evidence
        if isinstance(item, dict) and item.get("type") == "label_correction_invalidation"
    ]
    matching = [
        item for item in revisions
        if args.marker in json.dumps(item, ensure_ascii=False, default=str)
    ]
    stored_subscene = result.subscene_type if result is not None else None
    input_subscene = (
        result.input_data.get("subscene_type")
        if result is not None and isinstance(result.input_data, dict)
        else None
    )
    correction_content = (
        correction_evidence[-1].get("content", {}) if correction_evidence else {}
    )
    invalidated = (
        result is not None
        and isinstance(result.quantitative_metrics, dict)
        and result.quantitative_metrics.get("status") == "不判定"
        and isinstance(result.final_conclusion, dict)
        and result.final_conclusion.get("level") == "证据不足"
        and isinstance(correction_content.get("invalidated_fields"), list)
        and bool(correction_content.get("invalidated_fields"))
        and bool(correction_content.get("next_action"))
    )
    closure_errors = validate_result(result) if result is not None else ["result not found"]
    payload = {
        "ok": bool(
            result is not None
            and matching
            and matching[-1].get("field_name") == "subscene_type"
            and stored_subscene == args.expected_subscene
            and input_subscene == args.expected_subscene
            and correction_evidence
            and correction_evidence[-1].get("content", {}).get("next_action")
            and invalidated
            and not closure_errors
            and any(log.get("stage") == "LABEL_CORRECTION" for log in logs)
        ),
        "sample_id": args.sample_id,
        "run_id": args.run_id,
        "label_marker": args.marker,
        "matching_revision_count": len(matching),
        "field_name": matching[-1].get("field_name") if matching else None,
        "stored_subscene": stored_subscene,
        "input_subscene": input_subscene,
        "correction_evidence_count": len(correction_evidence),
        "derived_invalidated": invalidated,
        "quantitative_status": (
            result.quantitative_metrics.get("status")
            if result is not None and isinstance(result.quantitative_metrics, dict)
            else None
        ),
        "final_conclusion_level": (
            result.final_conclusion.get("level")
            if result is not None and isinstance(result.final_conclusion, dict)
            else None
        ),
        "invalidated_fields": correction_content.get("invalidated_fields", []),
        "next_action": correction_content.get("next_action"),
        "validation_errors": [issue.to_dict() if hasattr(issue, "to_dict") else str(issue) for issue in closure_errors],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

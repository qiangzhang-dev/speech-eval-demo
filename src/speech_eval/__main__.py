"""Command-line entry point for the offline speech evaluation demo."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .batch import BatchProcessor
from .config import load_threshold_config
from .exporters import export_repository
from .manifest import load_manifest, validate_manifest
from .models import EvaluationResult
from .repository import EvaluationRepository
from .revisions import apply_human_revision, apply_label_correction
from .providers.openai_compatible import OpenAICompatibleDiagnosticProvider
from .web import serve as serve_web


def _json_print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="speech_eval")
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate a CSV/JSON/JSONL manifest")
    validate.add_argument("manifest")
    validate.add_argument("--no-check-paths", action="store_true")
    validate.add_argument("--verbose", action="store_true", help="include parsed samples in output")
    validate.add_argument("--max-issues", type=int, default=20)

    run = sub.add_parser("run", help="run the offline evaluation batch")
    run.add_argument("manifest")
    run.add_argument("--database", default="var/evaluation.db")
    run.add_argument("--batch-id")
    run.add_argument("--run-id")
    run.add_argument("--no-check-paths", action="store_true")
    run.add_argument("--thresholds", default="config/thresholds.json")
    run.add_argument("--cer-pass-max", type=float)
    run.add_argument("--cer-attention-max", type=float)
    run.add_argument("--provider-attempts", type=int, default=3)
    run.add_argument(
        "--provider",
        choices=("rules", "openai-compatible"),
        default=os.environ.get("SPEECH_EVAL_ANALYZER", "rules"),
        help="diagnostic backend; rules is deterministic and offline",
    )
    run.add_argument("--model", default=os.environ.get("SPEECH_EVAL_MODEL", ""))
    run.add_argument(
        "--api-base",
        default=os.environ.get("SPEECH_EVAL_API_BASE", "http://127.0.0.1:8000/v1"),
    )
    run.add_argument("--api-key-env", default="SPEECH_EVAL_API_KEY")
    run.add_argument("--provider-timeout", type=float, default=30.0)
    run.add_argument(
        "--allow-network",
        action="store_true",
        default=os.environ.get("SPEECH_EVAL_ALLOW_REMOTE", "false").casefold() == "true",
        help="explicitly allow calls to the configured AI endpoint",
    )

    export = sub.add_parser("export", help="export stored results")
    export.add_argument("--database", default="var/evaluation.db")
    export.add_argument("--output", required=True)
    export.add_argument("--batch-id")
    export.add_argument("--run-id")

    query = sub.add_parser("query", help="query stored results as JSON")
    query.add_argument("--database", default="var/evaluation.db")
    query.add_argument("--batch-id")
    query.add_argument("--run-id")
    query.add_argument("--status")
    query.add_argument("--scene-type")
    query.add_argument("--conclusion")
    query.add_argument("--q")
    query.add_argument("--limit", type=int, default=50)

    revise = sub.add_parser("revise", help="apply an auditable human revision")
    revise.add_argument("--database", default="var/evaluation.db")
    revise.add_argument("--sample-id", required=True)
    revise.add_argument("--run-id")
    revise.add_argument("--editor", required=True)
    revise.add_argument("--reason", required=True)
    revise.add_argument("--changes", help="JSON object of internal or Chinese field names")
    revise.add_argument("--changes-file", type=Path)

    label = sub.add_parser(
        "correct-labels",
        help="correct scene/subscene/task labels with an auditable invalidation",
    )
    label.add_argument("--database", default="var/evaluation.db")
    label.add_argument("--sample-id", required=True)
    label.add_argument("--run-id")
    label.add_argument("--editor", required=True)
    label.add_argument("--reason", required=True)
    label.add_argument("--changes", help="JSON object of label fields")
    label.add_argument("--changes-file", type=Path)

    serve = sub.add_parser("serve", help="serve the local dashboard and JSON API")
    serve.add_argument("--database", default="var/evaluation.db")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--static-dir")

    return parser


def _manifest_summary(report: Any, *, max_issues: int) -> dict[str, Any]:
    issues = [issue.to_dict() for issue in report.issues]
    summary: dict[str, Any] = {
        "ok": report.ok,
        "source_path": report.source_path,
        "count": len(report.samples),
        "error_count": len(report.errors),
        "issue_count": len(issues),
    }
    if issues:
        summary["issues"] = issues[:max_issues]
        if len(issues) > max_issues:
            summary["issues_truncated"] = len(issues) - max_issues
    return summary


def _read_changes(args: argparse.Namespace) -> dict[str, Any]:
    raw = args.changes
    if args.changes_file is not None:
        if raw is not None:
            raise ValueError("--changes and --changes-file are mutually exclusive")
        raw = args.changes_file.read_text(encoding="utf-8")
    if not raw:
        raise ValueError("one of --changes or --changes-file is required")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"changes must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not payload:
        raise ValueError("changes must be a non-empty JSON object")
    return payload


def _query_results(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.limit < 1:
        raise ValueError("--limit must be positive")
    with EvaluationRepository(args.database) as repository:
        results = repository.list_results(
            batch_id=args.batch_id,
            run_id=args.run_id,
            status=args.status,
        )
    if args.scene_type:
        results = [item for item in results if item.scene_type == args.scene_type]
    if args.conclusion:
        results = [
            item for item in results
            if isinstance(item.final_conclusion, dict)
            and item.final_conclusion.get("level") == args.conclusion
        ]
    if args.q:
        needle = args.q.casefold()
        results = [
            item for item in results
            if needle in json.dumps(item.to_dict(chinese_fields=True), ensure_ascii=False, default=str).casefold()
        ]
    return [item.to_dict(chinese_fields=True) for item in results[: args.limit]]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            report = validate_manifest(args.manifest, check_paths=not args.no_check_paths)
            payload = report.to_dict() if args.verbose else _manifest_summary(report, max_issues=args.max_issues)
            _json_print(payload)
            return 0 if report.ok else 1

        if args.command == "run":
            config = load_threshold_config(args.thresholds)
            thresholds = {
                "pass_max": args.cer_pass_max if args.cer_pass_max is not None else config["pass_max"],
                "attention_max": args.cer_attention_max if args.cer_attention_max is not None else config["attention_max"],
            }
            if thresholds["pass_max"] < 0 or thresholds["attention_max"] < thresholds["pass_max"]:
                raise ValueError("CER thresholds must satisfy 0 <= pass_max <= attention_max")
            samples = load_manifest(args.manifest, check_paths=not args.no_check_paths)
            provider = None
            if args.provider == "openai-compatible":
                if not args.allow_network:
                    raise ValueError(
                        "openai-compatible provider requires --allow-network or SPEECH_EVAL_ALLOW_REMOTE=true"
                    )
                if not args.model.strip():
                    raise ValueError(
                        "openai-compatible provider requires --model or SPEECH_EVAL_MODEL"
                    )
                provider = OpenAICompatibleDiagnosticProvider(
                    model=args.model,
                    base_url=args.api_base,
                    api_key=os.environ.get(args.api_key_env) if args.api_key_env else None,
                    timeout=args.provider_timeout,
                    allow_network=True,
                )
            with EvaluationRepository(args.database) as repository:
                summary = BatchProcessor(
                    repository,
                    provider=provider,
                    thresholds=thresholds,
                    provider_max_attempts=args.provider_attempts,
                    threshold_version=config["threshold_version"],
                    threshold_approval_status=config["approval_status"],
                    threshold_effective=config["effective"],
                ).run(samples, batch_id=args.batch_id, run_id=args.run_id)
            payload = summary.to_dict()
            payload["provider"] = args.provider
            payload["model"] = args.model if args.provider != "rules" else "-"
            payload["threshold_version"] = config["threshold_version"]
            payload["threshold_approval_status"] = config["approval_status"]
            payload["threshold_effective"] = config["effective"]
            _json_print(payload)
            return 0 if summary.failed == 0 else 2

        if args.command == "export":
            with EvaluationRepository(args.database) as repository:
                payload = export_repository(
                    repository,
                    args.output,
                    batch_id=args.batch_id,
                    run_id=args.run_id,
                )
            _json_print(payload)
            return 0

        if args.command == "query":
            items = _query_results(args)
            _json_print({"count": len(items), "items": items})
            return 0

        if args.command == "revise":
            changes = _read_changes(args)
            reverse = {value: key for key, value in EvaluationResult.FIELD_MAP.items()}
            normalized = {reverse.get(key, key): value for key, value in changes.items()}
            with EvaluationRepository(args.database) as repository:
                result = repository.get_result(args.sample_id, run_id=args.run_id)
                if result is None:
                    print("result not found", file=sys.stderr)
                    return 1
                updated, revisions = apply_human_revision(
                    result,
                    normalized,
                    editor=args.editor,
                    reason=args.reason,
                    run_id=args.run_id,
                    repository=repository,
                )
            _json_print({
                "sample_id": updated.sample_id,
                "run_id": updated.run_id,
                "revision_count": len(revisions),
                "revisions": [item.to_dict() for item in revisions],
            })
            return 0

        if args.command == "correct-labels":
            changes = _read_changes(args)
            with EvaluationRepository(args.database) as repository:
                result = repository.get_result(args.sample_id, run_id=args.run_id)
                if result is None:
                    print("result not found", file=sys.stderr)
                    return 1
                updated, revisions = apply_label_correction(
                    result,
                    changes,
                    editor=args.editor,
                    reason=args.reason,
                    run_id=args.run_id,
                    repository=repository,
                )
                if revisions:
                    repository.append_log(
                        level="INFO",
                        stage="LABEL_CORRECTION",
                        status="COMPLETED",
                        message=(
                            f"Applied {len(revisions)} label correction(s); "
                            "derived metrics and conclusions invalidated"
                        ),
                        batch_id=updated.batch_id,
                        sample_id=updated.sample_id,
                        run_id=updated.run_id,
                        version="cli-label-v1",
                    )
            _json_print({
                "sample_id": updated.sample_id,
                "run_id": updated.run_id,
                "revision_count": len(revisions),
                "revisions": [item.to_dict() for item in revisions],
                "derived_invalidated": bool(revisions),
            })
            return 0

        if args.command == "serve":
            serve_web(
                args.database,
                host=args.host,
                port=args.port,
                static_dir=args.static_dir,
            )
            return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Read-only audit for representative C01, C02, and C03 result chains.

The command opens SQLite in read-only mode and verifies that the saved
reference fields, system outputs, metrics, evidence, threshold provenance,
and final conclusion agree for three representative samples.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from speech_eval.serialization import result_from_dict


MISSING = {None, "", "-"}
REPRESENTATIVES: dict[str, dict[str, Any]] = {
    "SYN-0001": {
        "capability_chain": "C01",
        "tasks": ["语音识别"],
        "fields": [("transcript", "transcript")],
        "stages": [],
    },
    "SYN-0003": {
        "capability_chain": "C02",
        "tasks": ["语音识别", "机器翻译"],
        "fields": [("transcript", "transcript"), ("translation", "translation")],
        "stages": [
            ("machine_translation", "translation_exact_match", "translation", "translation"),
        ],
    },
    "SYN-0005": {
        "capability_chain": "C03",
        "tasks": ["语音识别", "语义理解", "对话生成"],
        "fields": [
            ("transcript", "transcript"),
            ("intent", "intent"),
            ("slots", "slots"),
            ("expected_action", "action"),
            ("expected_reply", "reply"),
        ],
        "stages": [
            ("intent_understanding", "intent_exact_match", "intent", "intent"),
            ("slot_filling", "slot_exact_match", "slots", "slots"),
            ("action_execution", "action_exact_match", "expected_action", "action"),
            ("response_generation", "response_exact_match", "expected_reply", "reply"),
        ],
    },
}


def _present(value: Any) -> bool:
    if isinstance(value, (dict, list)):
        return bool(value)
    return value not in MISSING


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _evidence_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        evidence_ids = value.get("evidence_ids")
        if isinstance(evidence_ids, list):
            references.update(str(item) for item in evidence_ids if _present(item))
        evidence_id = value.get("evidence_id")
        if _present(evidence_id):
            references.add(str(evidence_id))
        for nested in value.values():
            references.update(_evidence_references(nested))
    elif isinstance(value, list):
        for nested in value:
            references.update(_evidence_references(nested))
    return references


def _read_payloads(database: Path, run_id: str, sample_ids: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    resolved = database.resolve()
    if not resolved.is_file():
        return {}, [f"database not found: {resolved}"]
    uri = resolved.as_uri() + "?mode=ro"
    rows: dict[str, dict[str, Any]] = {}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            for sample_id in sample_ids:
                row = connection.execute(
                    "SELECT status,payload_json FROM results WHERE run_id=? AND sample_id=?",
                    (run_id, sample_id),
                ).fetchone()
                if row is None:
                    errors.append(f"{sample_id}: result not found for run {run_id}")
                    continue
                if str(row["status"]) != "COMPLETED":
                    errors.append(f"{sample_id}: stored status must be COMPLETED, got {row['status']!r}")
                try:
                    payload = json.loads(row["payload_json"])
                    rows[sample_id] = result_from_dict(payload).to_dict(chinese_fields=False)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"{sample_id}: invalid result payload: {exc}")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        errors.append(f"SQLite read failed: {exc}")
    return rows, errors


def _audit_sample(sample_id: str, result: dict[str, Any], spec: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    reference = result.get("reference_annotation")
    output = result.get("system_output")
    metrics = result.get("quantitative_metrics")
    evidence = result.get("evidence")
    conclusion = result.get("final_conclusion")
    if not isinstance(reference, dict):
        reference = {}
        errors.append(f"{sample_id}: reference_annotation must be an object")
    if not isinstance(output, dict):
        output = {}
        errors.append(f"{sample_id}: system_output must be an object")
    if not isinstance(metrics, dict):
        metrics = {}
        errors.append(f"{sample_id}: quantitative_metrics must be an object")
    if not isinstance(evidence, list):
        evidence = []
        errors.append(f"{sample_id}: evidence must be a list")
    if not isinstance(conclusion, dict):
        conclusion = {}
        errors.append(f"{sample_id}: final_conclusion must be an object")

    tasks = result.get("task_types") if isinstance(result.get("task_types"), list) else []
    for task in spec["tasks"]:
        if task not in tasks:
            errors.append(f"{sample_id}: missing declared task {task}")

    reference_fields: dict[str, Any] = {}
    output_fields: dict[str, Any] = {}
    for reference_key, output_key in spec["fields"]:
        reference_fields[reference_key] = reference.get(reference_key)
        output_fields[output_key] = output.get(output_key)
        if not _present(reference.get(reference_key)):
            errors.append(f"{sample_id}: reference field {reference_key} is missing")
        if not _present(output.get(output_key)):
            errors.append(f"{sample_id}: system output field {output_key} is missing")

    stage_rows = metrics.get("stage_metrics")
    if not isinstance(stage_rows, list):
        stage_rows = []
        errors.append(f"{sample_id}: stage_metrics must be a list")
    stage_by_name = {
        str(row.get("stage")): row
        for row in stage_rows
        if isinstance(row, dict) and _present(row.get("stage"))
    }
    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in evidence
        if isinstance(item, dict) and _present(item.get("evidence_id"))
    }
    narrative_references: set[str] = set()
    for key in ("quality_diagnosis", "impact_assessment", "recommendations", "final_conclusion"):
        narrative_references.update(_evidence_references(result.get(key)))

    metric_evidence_ids = []
    for evidence_id, item in evidence_by_id.items():
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        if (
            item.get("source") == "computed_metric"
            or item.get("location") == "quantitative_metrics.CER"
            or (item.get("type") == "metric_snapshot" and content.get("metric_name") == "CER")
        ):
            metric_evidence_ids.append(evidence_id)
    if not metric_evidence_ids:
        errors.append(f"{sample_id}: main CER metric evidence is missing")
    elif not any(evidence_id in narrative_references for evidence_id in metric_evidence_ids):
        errors.append(f"{sample_id}: main CER evidence is not cited by the result narrative")

    stage_summaries: list[dict[str, Any]] = []
    stage_evidence_ids: list[str] = []
    for stage, metric_name, reference_key, output_key in spec["stages"]:
        row = stage_by_name.get(stage)
        if row is None:
            errors.append(f"{sample_id}: missing stage metric {stage}")
            continue
        if row.get("metric_name") != metric_name:
            errors.append(
                f"{sample_id}: stage {stage} metric_name must be {metric_name}, got {row.get('metric_name')!r}"
            )
        reference_value = reference.get(reference_key)
        output_value = output.get(output_key)
        expected_value = 1.0 if _canonical(reference_value) == _canonical(output_value) else 0.0
        try:
            actual_value = float(row.get("value"))
        except (TypeError, ValueError):
            actual_value = float("nan")
        if actual_value != expected_value:
            errors.append(
                f"{sample_id}: stage {stage} value {row.get('value')!r} disagrees with saved reference/output"
            )
        evidence_id = str(row.get("evidence_id", ""))
        if not evidence_id:
            for candidate_id, candidate in evidence_by_id.items():
                content = candidate.get("content")
                if not isinstance(content, dict):
                    continue
                exact_stage_metadata = (
                    content.get("stage") == stage
                    and content.get("metric_name") == metric_name
                )
                comparator_snapshot = (
                    candidate.get("type") == "metric_snapshot"
                    and candidate.get("location") == f"fixture://{sample_id}/{stage}"
                    and _canonical(content.get("reference")) == _canonical(reference_value)
                    and _canonical(content.get("output", content.get("hypothesis"))) == _canonical(output_value)
                )
                if exact_stage_metadata or comparator_snapshot:
                    evidence_id = candidate_id
                    break
        evidence_item = evidence_by_id.get(evidence_id)
        if not evidence_id or evidence_item is None:
            errors.append(f"{sample_id}: stage {stage} evidence is missing")
        else:
            stage_evidence_ids.append(evidence_id)
            content = evidence_item.get("content")
            if not isinstance(content, dict):
                errors.append(f"{sample_id}: stage {stage} evidence content must be an object")
            else:
                if _canonical(content.get("reference")) != _canonical(reference_value):
                    errors.append(f"{sample_id}: stage {stage} evidence reference disagrees with saved field")
                evidence_output = content.get("output", content.get("hypothesis"))
                if _canonical(evidence_output) != _canonical(output_value):
                    errors.append(f"{sample_id}: stage {stage} evidence output disagrees with saved field")
            if evidence_id not in narrative_references:
                errors.append(f"{sample_id}: stage {stage} evidence {evidence_id} is not cited")
        stage_summaries.append(
            {
                "stage": stage,
                "metric_name": row.get("metric_name"),
                "value": row.get("value"),
                "status": row.get("status"),
                "evidence_id": evidence_id or "-",
            }
        )

    threshold = {
        "version": metrics.get("threshold_version"),
        "approval_status": metrics.get("threshold_approval_status"),
        "effective": metrics.get("threshold_effective"),
        "formal_status": metrics.get("status"),
        "candidate_status": metrics.get("candidate_status"),
    }
    if not _present(threshold["version"]):
        errors.append(f"{sample_id}: threshold version is missing")
    if not _present(threshold["approval_status"]):
        errors.append(f"{sample_id}: threshold approval status is missing")
    if threshold["effective"] is not False:
        errors.append(f"{sample_id}: this experiment requires threshold_effective=false")
    if threshold["formal_status"] != "不判定":
        errors.append(f"{sample_id}: pending threshold requires formal status 不判定")
    if conclusion.get("level") != "证据不足":
        errors.append(f"{sample_id}: pending threshold requires final conclusion 证据不足")

    sample_summary = {
        "sample_id": sample_id,
        "capability_chain": spec["capability_chain"],
        "tasks": tasks,
        "reference_fields": reference_fields,
        "system_output_fields": output_fields,
        "stage_metrics": stage_summaries,
        "stage_evidence_ids": stage_evidence_ids,
        "threshold": threshold,
        "final_conclusion": conclusion,
        "checks": {
            "reference_fields_present": not any("reference field" in error for error in errors),
            "system_output_fields_present": not any("system output field" in error for error in errors),
            "stage_metrics_consistent": not any("stage " in error and "metric evidence" not in error for error in errors),
            "stage_evidence_closed": not any("stage " in error and "evidence" in error for error in errors),
            "pending_threshold_not_formalized": threshold["effective"] is False
            and threshold["formal_status"] == "不判定"
            and conclusion.get("level") == "证据不足",
        },
        "ok": not errors,
        "errors": errors,
    }
    return sample_summary, errors


def audit_database(database: Path, run_id: str, sample_ids: list[str] | None = None) -> dict[str, Any]:
    selected = sample_ids or list(REPRESENTATIVES)
    unknown = [sample_id for sample_id in selected if sample_id not in REPRESENTATIVES]
    errors = [f"unsupported representative sample: {sample_id}" for sample_id in unknown]
    selected = [sample_id for sample_id in selected if sample_id in REPRESENTATIVES]
    rows, read_errors = _read_payloads(database, run_id, selected)
    errors.extend(read_errors)
    sample_summaries: list[dict[str, Any]] = []
    for sample_id in selected:
        result = rows.get(sample_id)
        if result is None:
            continue
        summary, sample_errors = _audit_sample(sample_id, result, REPRESENTATIVES[sample_id])
        sample_summaries.append(summary)
        errors.extend(sample_errors)
    return {
        "ok": not errors and len(sample_summaries) == len(selected),
        "audit_mode": "sqlite_read_only",
        "database": str(database.resolve()),
        "run_id": run_id,
        "sample_count": len(sample_summaries),
        "capability_chains": [item["capability_chain"] for item in sample_summaries],
        "stage_metric_count": sum(len(item["stage_metrics"]) for item in sample_summaries),
        "stage_evidence_count": sum(len(item["stage_evidence_ids"]) for item in sample_summaries),
        "samples": sample_summaries,
        "error_count": len(errors),
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sample-id", action="append", dest="sample_ids")
    args = parser.parse_args(argv)
    report = audit_database(args.database, args.run_id, args.sample_ids)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

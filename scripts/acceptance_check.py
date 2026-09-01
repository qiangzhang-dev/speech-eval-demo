#!/usr/bin/env python3
"""Project-specific offline acceptance check for SQLite + JSONL + CSV."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from validate_fixture import FIELD_NAMES, validate_result_shape
except ModuleNotFoundError:  # package import from the project root
    from scripts.validate_fixture import FIELD_NAMES, validate_result_shape

FIELD_MAP = {
    "sample_id": "样例ID",
    "scene_type": "场景类型",
    "task_types": "任务类型",
    "input_data": "输入数据",
    "audio_info": "音频信息",
    "reference_annotation": "参考文本/标注",
    "system_output": "系统输出",
    "quantitative_metrics": "量化指标",
    "quality_diagnosis": "质量诊断",
    "evidence": "证据片段",
    "impact_assessment": "影响评估",
    "recommendations": "优化建议",
    "human_revision": "人工修订",
    "final_conclusion": "最终结论",
}

# Persistence metadata is intentionally excluded from the public 14-field
# export. Unknown keys, however, must be retained so the strict schema gate
# can report them rather than silently dropping malformed fields.
INTERNAL_METADATA_FIELDS = {
    "subscene_type",
    "run_id",
    "batch_id",
    "status",
    "created_at",
    "metric_version",
    "prompt_version",
}
FINAL_LEVELS = {"通过", "需关注", "失败", "证据不足"}
CANDIDATE_STATUSES = {"通过", "需关注", "失败"}

# A task label only counts as implemented when the corresponding reference,
# system output, deterministic stage metric, and cited metric evidence all
# exist. These names are frozen by the synthetic engineering-validation
# pipeline; keeping the contract here prevents a label-only implementation
# from passing acceptance again.
TASK_STAGE_REQUIREMENTS = {
    "机器翻译": (
        ("machine_translation", "translation_exact_match", "translation", "translation"),
    ),
    "语义理解": (
        ("intent_understanding", "intent_exact_match", "intent", "intent"),
        ("slot_filling", "slot_exact_match", "slots", "slots"),
    ),
    "对话生成": (
        ("action_execution", "action_exact_match", "expected_action", "action"),
        ("response_generation", "response_exact_match", "expected_reply", "reply"),
    ),
}


def _jsonish(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[0] not in '[{"' and text not in {"true", "false", "null"}:
        try:
            return int(text) if text.isdigit() else float(text)
        except ValueError:
            return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _public_result(payload: dict[str, Any]) -> dict[str, Any]:
    payload_keys = set(payload)
    public_keys = set(FIELD_MAP.values())
    # Preserve public-shaped payloads verbatim (including extras/missing
    # fields) so validate_result_shape can enforce exactly fourteen fields.
    if payload_keys & public_keys:
        return {str(name): _jsonish(value) for name, value in payload.items()}

    projected = {
        public_name: _jsonish(payload.get(internal_name, "-"))
        for internal_name, public_name in FIELD_MAP.items()
    }
    # Internal SQLite payloads carry known persistence metadata. Any other
    # key is not part of the internal contract and must reach the validator.
    internal_keys = set(FIELD_MAP) | INTERNAL_METADATA_FIELDS
    for name, value in payload.items():
        if name not in internal_keys:
            projected[str(name)] = _jsonish(value)
    return projected

def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_number}: JSONL row must be an object")
        rows.append(_public_result(payload))
    return rows


def load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [_public_result(dict(row)) for row in rows]


def load_export(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix == ".jsonl":
        return load_jsonl(path)
    if suffix == ".csv":
        return load_csv(path)
    raise ValueError(f"unsupported export: {path}")


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if table not in {"runs", "results", "revisions", "logs"}:
        return set()
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _where_clause(
    columns: set[str],
    *,
    batch_id: str | None,
    run_id: str | None,
) -> tuple[str, list[str]]:
    clauses: list[str] = []
    values: list[str] = []
    if batch_id and "batch_id" in columns:
        clauses.append("batch_id=?")
        values.append(batch_id)
    if run_id and "run_id" in columns:
        clauses.append("run_id=?")
        values.append(run_id)
    return ((" WHERE " + " AND ".join(clauses)) if clauses else ""), values


def _table_rows(
    connection: sqlite3.Connection,
    table: str,
    *,
    batch_id: str | None,
    run_id: str | None,
) -> list[sqlite3.Row]:
    columns = _table_columns(connection, table)
    if not columns or "payload_json" not in columns:
        return []
    where, values = _where_clause(columns, batch_id=batch_id, run_id=run_id)
    order_column = "sample_id" if "sample_id" in columns else "rowid"
    return connection.execute(
        f"SELECT payload_json{', status' if 'status' in columns else ''} "
        f"FROM {table}{where} ORDER BY {order_column}",
        values,
    ).fetchall()


def _table_statuses(
    connection: sqlite3.Connection,
    table: str,
    *,
    batch_id: str | None,
    run_id: str | None,
) -> list[str]:
    columns = _table_columns(connection, table)
    if not columns or "status" not in columns:
        return []
    where, values = _where_clause(columns, batch_id=batch_id, run_id=run_id)
    rows = connection.execute(f"SELECT status FROM {table}{where}", values).fetchall()
    return [str(row["status"]) for row in rows]


def _table_count(
    connection: sqlite3.Connection,
    table: str,
    *,
    batch_id: str | None,
    run_id: str | None,
) -> int:
    if table not in {"revisions", "logs"}:
        return 0
    columns = _table_columns(connection, table)
    if not columns:
        return 0
    where, values = _where_clause(columns, batch_id=batch_id, run_id=run_id)
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}{where}", values).fetchone()[0])


def load_sqlite(
    path: Path,
    *,
    batch_id: str | None = None,
    run_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        result_rows = _table_rows(
            connection,
            "results",
            batch_id=batch_id,
            run_id=run_id,
        )
        # Prefer the per-sample results table when both tables carry payloads.
        # A runs-only database remains usable when it stores payload_json there.
        payload_rows = result_rows or _table_rows(
            connection,
            "runs",
            batch_id=batch_id,
            run_id=run_id,
        )
        results: list[dict[str, Any]] = []
        result_statuses: list[str] = []
        for row in payload_rows:
            payload = json.loads(row["payload_json"])
            results.append(_public_result(payload))
            if "status" in row.keys():
                result_statuses.append(str(row["status"]))

        run_statuses = _table_statuses(
            connection,
            "runs",
            batch_id=batch_id,
            run_id=run_id,
        )
        stored_result_statuses = _table_statuses(
            connection,
            "results",
            batch_id=batch_id,
            run_id=run_id,
        )
        if not result_statuses:
            result_statuses = stored_result_statuses
        # Scan both tables for a failing run, while avoiding a duplicate
        # COMPLETED run status in the user-facing per-result status counts.
        statuses = list(result_statuses)
        statuses.extend(status for status in run_statuses if status != "COMPLETED")
        revision_count = _table_count(
            connection, "revisions", batch_id=batch_id, run_id=run_id
        )
        log_count = _table_count(
            connection, "logs", batch_id=batch_id, run_id=run_id
        )
        return results, statuses, {"revision_count": revision_count, "log_count": log_count}
    finally:
        connection.close()


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _index(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    indexed: dict[str, dict[str, Any]] = {}
    for row_number, row in enumerate(rows, 1):
        sample_id = row.get("样例ID")
        if not isinstance(sample_id, str) or not sample_id:
            errors.append(f"row {row_number}: missing sample ID")
            continue
        if sample_id in indexed:
            errors.append(f"duplicate sample ID: {sample_id}")
        indexed[sample_id] = row
    return indexed, errors


def compare(
    left_name: str,
    left_rows: list[dict[str, Any]],
    right_name: str,
    right_rows: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    left, left_errors = _index(left_rows)
    right, right_errors = _index(right_rows)
    errors.extend(f"{left_name}: {error}" for error in left_errors)
    errors.extend(f"{right_name}: {error}" for error in right_errors)
    if set(left) != set(right):
        errors.append(
            f"{left_name}/{right_name} sample IDs differ: "
            f"{left_name}_only={len(set(left) - set(right))}, "
            f"{right_name}_only={len(set(right) - set(left))}"
        )
    for sample_id in sorted(set(left) & set(right)):
        left_keys = set(left[sample_id])
        right_keys = set(right[sample_id])
        if left_keys != right_keys:
            errors.append(
                f"{sample_id}: top-level keys differ between {left_name} and {right_name}: "
                f"{left_name}_only={sorted(left_keys - right_keys)}, "
                f"{right_name}_only={sorted(right_keys - left_keys)}"
            )
            continue
        if _canonical(left[sample_id]) != _canonical(right[sample_id]):
            for field in FIELD_NAMES:
                if _canonical(left[sample_id].get(field)) != _canonical(right[sample_id].get(field)):
                    errors.append(
                        f"{sample_id}: field {field!r} differs between {left_name} and {right_name}"
                    )
                    break

    return errors


def _refs(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        raw = value.get("evidence_ids")
        if isinstance(raw, list):
            found.update(str(item) for item in raw)
        for child in value.values():
            found |= _refs(child)
    elif isinstance(value, list):
        for child in value:
            found |= _refs(child)
    return found


def _has_label_correction_invalidation(result: dict[str, Any]) -> bool:
    """Whether a result's derived fields were explicitly invalidated.

    ``COMPLETED`` describes execution state only.  A label correction creates
    an auditable invalidation record and requires a new run before its metric
    can count as a current valid measurement.
    """

    evidence = result.get("证据片段")
    return isinstance(evidence, list) and any(
        isinstance(item, dict) and item.get("type") == "label_correction_invalidation"
        for item in evidence
    )


def _present(value: Any) -> bool:
    """Return whether a fixture value carries an actual annotation."""

    if value is None or value == "-" or value == "":
        return False
    if isinstance(value, (list, tuple, dict, set)) and not value:
        return False
    return True


def _field(value: Any, *names: str) -> Any:
    if not isinstance(value, dict):
        return None
    for name in names:
        if name in value and _present(value[name]):
            return value[name]
    return None


def _task_list(result: dict[str, Any]) -> list[str]:
    tasks = result.get("任务类型")
    if isinstance(tasks, list):
        return [str(item) for item in tasks if _present(item)]
    if isinstance(tasks, str) and tasks != "-":
        return [tasks]
    return []


def _stage_metric_rows(metrics: Any) -> list[dict[str, Any]]:
    """Normalize the stage_metrics representation used by current exports."""

    if not isinstance(metrics, dict):
        return []
    raw = metrics.get("stage_metrics")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        rows: list[dict[str, Any]] = []
        for stage, item in raw.items():
            if isinstance(item, dict):
                rows.append({"stage": stage, **item})
        return rows
    return []


def _canonical_stage(value: Any) -> str:
    aliases = {
        "translation": "machine_translation",
        "mt": "machine_translation",
        "machine_translation": "machine_translation",
        "机器翻译": "machine_translation",
        "intent": "intent_understanding",
        "intent_understanding": "intent_understanding",
        "semantic_understanding": "intent_understanding",
        "语义理解": "intent_understanding",
        "slot": "slot_filling",
        "slots": "slot_filling",
        "slot_filling": "slot_filling",
        "槽位": "slot_filling",
        "action": "action_execution",
        "action_execution": "action_execution",
        "动作执行": "action_execution",
        "response": "response_generation",
        "reply": "response_generation",
        "response_generation": "response_generation",
        "对话生成": "response_generation",
    }
    return aliases.get(str(value).strip(), str(value).strip())


def _metric_stage(row: dict[str, Any]) -> str:
    stage = row.get("stage", row.get("stage_name", row.get("阶段")))
    return _canonical_stage(stage)


def _stage_evidence(
    evidence_by_id: dict[str, dict[str, Any]],
    *,
    stage: str,
    metric_name: str,
    metric: dict[str, Any],
) -> tuple[str | None, bool]:
    """Find a semantically matching stage evidence item.

    The evidence content is deliberately checked against the stored metric,
    rather than merely looking for a string containing the stage name.
    """

    for evidence_id, item in evidence_by_id.items():
        if item.get("type") not in {"stage_metric_snapshot", "stage_metric", "metric_snapshot"}:
            continue
        source = str(item.get("source", ""))
        if source not in {"computed_stage_metric", "stage_metric", "computed_metric"}:
            continue
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        evidence_stage = _canonical_stage(
            item.get("stage", content.get("stage", content.get("stage_name")))
        )
        if evidence_stage != stage:
            location = str(item.get("location", ""))
            if stage not in location:
                continue
        evidence_metric_name = content.get("metric_name", item.get("metric_name"))
        if evidence_metric_name != metric_name:
            continue
        # Value and match state are the minimum deterministic identity.  If
        # either is present in both records it must agree exactly.
        if "value" in metric and "value" in content and _canonical(metric["value"]) != _canonical(content["value"]):
            continue
        if "matched" in metric and "matched" in content and _canonical(metric["matched"]) != _canonical(content["matched"]):
            continue
        return evidence_id, True
    return None, False


def _multistage_errors(
    result: dict[str, Any],
    *,
    evidence_by_id: dict[str, dict[str, Any]],
    references: set[str],
) -> tuple[list[str], dict[str, int]]:
    """Validate declared MT/NLU/dialogue stages end-to-end.

    A scene/task label is not accepted as coverage by itself.  Every declared
    stage must have non-empty reference and system values, a deterministic
    stage metric, and a matching evidence item cited by the result narrative.
    """

    sample_id = str(result.get("样例ID", "unknown"))
    tasks = _task_list(result)
    reference = result.get("参考文本/标注")
    output = result.get("系统输出")
    metrics = result.get("量化指标")
    metric_rows = _stage_metric_rows(metrics)
    errors: list[str] = []
    checked = 0
    evidence_hits = 0
    if not any(task in TASK_STAGE_REQUIREMENTS for task in tasks):
        return errors, {"declared": 0, "stage_metrics": 0, "stage_evidence": 0}
    if not isinstance(reference, dict):
        errors.append(f"{sample_id}: multi-stage tasks require object 参考文本/标注")
    if not isinstance(output, dict):
        errors.append(f"{sample_id}: multi-stage tasks require object 系统输出")
    if not isinstance(metrics, dict):
        errors.append(f"{sample_id}: multi-stage tasks require object 量化指标 with stage_metrics")

    by_stage: dict[str, list[dict[str, Any]]] = {}
    for row in metric_rows:
        by_stage.setdefault(_metric_stage(row), []).append(row)

    for task in tasks:
        for stage, metric_name, ref_key, out_key in TASK_STAGE_REQUIREMENTS.get(task, ()):
            checked += 1
            if not isinstance(reference, dict) or not _present(
                _field(reference, ref_key, {
                    "translation": "译文",
                    "intent": "意图",
                    "slots": "槽位",
                    "expected_action": "预期动作",
                    "expected_reply": "期望回复",
                }.get(ref_key, ref_key))
            ):
                errors.append(f"{sample_id}: {task} requires reference field {ref_key}")
            if not isinstance(output, dict) or not _present(
                _field(output, out_key, {
                    "translation": "译文",
                    "intent": "意图",
                    "slots": "槽位",
                    "action": "动作",
                    "reply": "回复",
                }.get(out_key, out_key))
            ):
                errors.append(f"{sample_id}: {task} requires system field {out_key}")

            candidates = [
                row for row in by_stage.get(stage, [])
                if row.get("metric_name") == metric_name
            ]
            if not candidates:
                errors.append(
                    f"{sample_id}: {task} requires stage metric {stage}/{metric_name}"
                )
                continue
            metric = candidates[0]
            if not _present(metric.get("reference")) or not _present(metric.get("hypothesis")):
                errors.append(f"{sample_id}: stage metric {stage} lacks reference/hypothesis")
            if not isinstance(metric.get("matched"), bool):
                errors.append(f"{sample_id}: stage metric {stage} matched must be boolean")
            evidence_id, found = _stage_evidence(
                evidence_by_id,
                stage=stage,
                metric_name=metric_name,
                metric=metric,
            )
            if not found or evidence_id is None:
                errors.append(f"{sample_id}: {stage} lacks matching stage metric evidence")
            else:
                evidence_hits += 1
                if evidence_id not in references:
                    errors.append(
                        f"{sample_id}: stage evidence {evidence_id} is not cited by a result field"
                    )
    return errors, {
        "declared": checked,
        "stage_metrics": sum(len(items) for items in by_stage.values()),
        "stage_evidence": evidence_hits,
    }


def _threshold_errors(
    result: dict[str, Any],
) -> tuple[list[str], dict[str, int]]:
    """Ensure pending candidate thresholds cannot masquerade as decisions."""

    sample_id = str(result.get("样例ID", "unknown"))
    metrics = result.get("量化指标")
    if metrics == "-" or not isinstance(metrics, dict):
        return [], {"metadata": 0, "pending": 0}
    errors: list[str] = []
    metadata_keys = {
        "threshold_version",
        "threshold_approval_status",
        "threshold_effective",
        "candidate_status",
        "overall_candidate_status",
    }
    present = metadata_keys & set(metrics)
    # New runs must carry all five fields per result.  This also catches an
    # export that silently drops threshold provenance.
    missing = sorted(metadata_keys - present)
    if missing:
        errors.append(f"{sample_id}: quantitative_metrics missing threshold metadata {missing}")
        return errors, {"metadata": 0, "pending": 0}
    if not _present(metrics.get("threshold_version")):
        errors.append(f"{sample_id}: threshold_version must be non-empty")
    approval = str(metrics.get("threshold_approval_status", "")).strip()
    effective = metrics.get("threshold_effective")
    candidate = metrics.get("candidate_status")
    overall_candidate = metrics.get("overall_candidate_status")
    status = metrics.get("status")
    if not isinstance(effective, bool):
        errors.append(f"{sample_id}: threshold_effective must be boolean")
    if candidate not in CANDIDATE_STATUSES:
        errors.append(f"{sample_id}: candidate_status must be one of {sorted(CANDIDATE_STATUSES)}")
    if overall_candidate not in CANDIDATE_STATUSES:
        errors.append(
            f"{sample_id}: overall_candidate_status must be one of {sorted(CANDIDATE_STATUSES)}"
        )
    stage_candidates = {
        str(item.get("candidate_status"))
        for item in metrics.get("stage_metrics", [])
        if isinstance(item, dict)
    }
    expected_overall = (
        "证据不足"
        if "证据不足" in stage_candidates
        else ("失败" if "失败" in stage_candidates else candidate)
    )
    if overall_candidate != expected_overall:
        errors.append(
            f"{sample_id}: overall_candidate_status {overall_candidate!r} does not match "
            f"CER/stage aggregate {expected_overall!r}"
        )
    pending = effective is False or approval.casefold() not in {
        "approved", "effective", "已审批", "已生效"
    }
    conclusion = result.get("最终结论")
    level = conclusion.get("level") if isinstance(conclusion, dict) else None
    if pending:
        if status != "不判定":
            errors.append(
                f"{sample_id}: pending threshold requires quantitative_metrics.status='不判定'"
            )
        if level != "证据不足":
            errors.append(
                f"{sample_id}: pending threshold requires 最终结论.level='证据不足'"
            )
    elif status not in {"通过", "需关注", "失败", "不判定"}:
        errors.append(f"{sample_id}: invalid quantitative_metrics.status {status!r}")
    return errors, {"metadata": 1, "pending": int(pending)}


def result_errors(
    rows: list[dict[str, Any]],
    *,
    schema: Path,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    scenarios: set[str] = set()
    subscenarios: set[str] = set()
    capabilities: set[str] = set()
    evidence_count = 0
    reference_count = 0
    multistage_declared = 0
    stage_metric_count = 0
    stage_evidence_count = 0
    threshold_metadata_count = 0
    threshold_pending_count = 0
    invalidated_sample_ids: list[str] = []
    current_metric_count = 0
    for index, result in enumerate(rows, 1):
        sample_id = str(result.get("样例ID", f"row-{index}"))
        shape_errors = validate_result_shape(result, schema_path=schema)
        errors.extend(f"{sample_id}: {error}" for error in shape_errors)
        scene = result.get("场景类型")
        if isinstance(scene, str):
            scenarios.add(scene)
        input_data = result.get("输入数据")
        if isinstance(input_data, dict):
            subscene = input_data.get("subscene_type")
            chain = input_data.get("capability_chain")
            if isinstance(subscene, str) and subscene != "-":
                subscenarios.add(subscene)
            # The current 14-field template stores capability coverage in
            # 任务类型 (a list), while older payloads used input_data.capability_chain.
            if not chain:
                chain = result.get("任务类型")
            if isinstance(chain, list):
                capabilities.update(str(item) for item in chain if item not in (None, "-"))
            elif isinstance(chain, str) and chain != "-":
                capabilities.add(chain)
        evidence = result.get("证据片段")
        evidence_by_id = {
            str(item.get("evidence_id")): item
            for item in evidence
            if isinstance(evidence, list) and isinstance(item, dict) and item.get("evidence_id")
        } if isinstance(evidence, list) else {}
        definitions = set(evidence_by_id)
        references = set()
        for field in ("质量诊断", "影响评估", "优化建议", "最终结论"):
            references |= _refs(result.get(field))
        metrics = result.get("量化指标")
        invalidated = _has_label_correction_invalidation(result)
        if invalidated:
            invalidated_sample_ids.append(sample_id)
            if not isinstance(metrics, dict) or metrics.get("status") != "不判定":
                errors.append(f"{sample_id}: invalidated result must have quantitative_metrics.status='不判定'")
            conclusion = result.get("最终结论")
            if not isinstance(conclusion, dict) or conclusion.get("level") != "证据不足":
                errors.append(f"{sample_id}: invalidated result must have 最终结论.level='证据不足'")
        elif isinstance(metrics, dict) and isinstance(metrics.get("value"), (int, float)):
            current_metric_count += 1
        conclusion_text = json.dumps(
            {field: result.get(field) for field in ("质量诊断", "最终结论")},
            ensure_ascii=False,
        )
        if isinstance(metrics, dict) and metrics.get("metric_name") == "CER" and "CER" in conclusion_text:
            semantic_evidence = []
            for item in evidence_by_id.values():
                content = item.get("content")
                if (
                    item.get("type") == "metric_snapshot"
                    and item.get("source") == "computed_metric"
                    and isinstance(content, dict)
                    and content.get("metric_name") == "CER"
                    and content.get("value") == metrics.get("value")
                    and content.get("metric_version") == metrics.get("metric_version")
                ):
                    semantic_evidence.append(item)
            if not semantic_evidence:
                errors.append(f"{sample_id}: CER conclusion lacks matching metric_snapshot evidence")
        evidence_count += len(definitions)
        reference_count += len(references)
        unresolved = sorted(references - definitions)
        if unresolved:
            errors.append(f"{sample_id}: unresolved evidence references: {unresolved}")
        stage_errors, stage_summary = _multistage_errors(
            result,
            evidence_by_id=evidence_by_id,
            references=references,
        )
        errors.extend(stage_errors)
        multistage_declared += stage_summary["declared"]
        stage_metric_count += stage_summary["stage_metrics"]
        stage_evidence_count += stage_summary["stage_evidence"]
        threshold_errors, threshold_summary = _threshold_errors(result)
        errors.extend(threshold_errors)
        threshold_metadata_count += threshold_summary["metadata"]
        threshold_pending_count += threshold_summary["pending"]
        revision = result.get("人工修订")
        if revision != "-" and isinstance(revision, dict):
            required = {"revision_id", "sample_id", "field_name", "before", "after", "editor", "edited_at", "reason"}
            missing = sorted(required - set(revision))
            if missing:
                errors.append(f"{sample_id}: revision missing fields {missing}")
            elif revision.get("sample_id") != result.get("样例ID"):
                errors.append(f"{sample_id}: revision sample_id does not match")
    return errors, {
        "count": len(rows),
        "unique_ids": len(_index(rows)[0]),
        "scenario_count": len(scenarios),
        "subscenario_count": len(subscenarios),
        "capability_count": len(capabilities),
        "evidence_count": evidence_count,
        "reference_count": reference_count,
        "multistage_declared": multistage_declared,
        "stage_metric_count": stage_metric_count,
        "stage_evidence_count": stage_evidence_count,
        "threshold_metadata_count": threshold_metadata_count,
        "threshold_pending_count": threshold_pending_count,
        "execution_record_count": len(rows),
        "current_valid_metric_count": current_metric_count,
        "invalidated_result_count": len(invalidated_sample_ids),
        "pending_recompute_count": len(invalidated_sample_ids),
        "pending_recompute_sample_ids": sorted(invalidated_sample_ids),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--export", type=Path, action="append", required=True)
    parser.add_argument("--schema", type=Path, default=Path("schemas/evaluation-result.schema.json"))
    parser.add_argument("--batch-id")
    parser.add_argument("--run-id")
    parser.add_argument("--min-count", type=int, default=100)
    parser.add_argument("--min-scenarios", type=int, default=3)
    parser.add_argument("--min-subscenarios", type=int, default=2)
    parser.add_argument("--min-capabilities", type=int, default=2)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        db_rows, statuses, store_stats = load_sqlite(
            args.db,
            batch_id=args.batch_id,
            run_id=args.run_id,
        )
        export_sets = [(path.name, load_export(path)) for path in args.export]
        errors, summary = result_errors(db_rows, schema=args.schema)
        if summary["count"] < args.min_count:
            errors.append(f"count {summary['count']} < minimum {args.min_count}")
        if set(statuses) != {"COMPLETED"}:
            errors.append(f"SQLite result statuses must be only COMPLETED: {dict(Counter(statuses))}")
        if summary["scenario_count"] < args.min_scenarios:
            errors.append(f"scenario count {summary['scenario_count']} < {args.min_scenarios}")
        if summary["subscenario_count"] < args.min_subscenarios:
            errors.append(f"subscenario count {summary['subscenario_count']} < {args.min_subscenarios}")
        if summary["capability_count"] < args.min_capabilities:
            errors.append(f"capability count {summary['capability_count']} < {args.min_capabilities}")
        for name, rows in export_sets:
            for row_number, row in enumerate(rows, 1):
                shape_errors = validate_result_shape(row, schema_path=args.schema)
                errors.extend(
                    f"{name} row {row_number}: {error}"
                    for error in shape_errors
                )
            errors.extend(compare("SQLite", db_rows, name, rows))
        for index in range(1, len(export_sets)):
            errors.extend(compare(export_sets[0][0], export_sets[0][1], export_sets[index][0], export_sets[index][1]))
        report = {
            "ok": not errors,
            **summary,
            **store_stats,
            "status_counts": dict(Counter(statuses)),
            "batch_id": args.batch_id or "-",
            "run_id": args.run_id or "-",
            "exports": {name: len(rows) for name, rows in export_sets},
            "errors": errors,
            "data_classification": "synthetic_engineering_validation",
            "formal_acceptance_claim": False,
        }
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        if not args.quiet:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    if not args.quiet:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


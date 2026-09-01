"""Human revision helpers with before/after audit records."""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from typing import Any

from .labeling import SCENE_TASKS
from .models import EvaluationResult, Revision, utc_now
from .validation import validate_result

EDITABLE_FIELDS = {
    "quality_diagnosis",
    "impact_assessment",
    "recommendations",
}

# Label corrections use a separate endpoint and audit path.  Keeping these
# fields out of EDITABLE_FIELDS protects source facts from generic revisions.
LABEL_FIELDS = {"scene_type", "subscene_type", "task_types"}
LABEL_FIELD_ALIASES = {
    "场景类型": "scene_type",
    "子场景": "subscene_type",
    "子场景类型": "subscene_type",
    "任务类型": "task_types",
}
_SCENE_CHAIN = {
    "短语音/录音转写": "C01",
    "会议/办公语音翻译": "C02",
    "车载/语音助手交互": "C03",
}

_EVIDENCE_CLOSURE_ISSUES = {
    "MISSING_EVIDENCE_REFERENCE",
    "UNKNOWN_EVIDENCE_REFERENCE",
}


def _validate_evidence_closure(result: EvaluationResult) -> None:
    """Reject subjective revisions that detach conclusions from immutable evidence."""

    issues = [
        issue
        for issue in validate_result(result)
        if issue.code in _EVIDENCE_CLOSURE_ISSUES
    ]
    if issues:
        details = "; ".join(issue.message for issue in issues)
        raise ValueError(f"revision breaks evidence-reference closure: {details}")


def apply_human_revision(
    result: EvaluationResult,
    changes: Mapping[str, Any],
    *,
    editor: str,
    reason: str,
    run_id: str | None = None,
    repository: Any | None = None,
) -> tuple[EvaluationResult, list[Revision]]:
    """Apply explicit field changes and optionally persist their audit rows."""

    if not editor.strip():
        raise ValueError("editor is required")
    if not reason.strip():
        raise ValueError("reason is required")
    forbidden = [field_name for field_name in changes if field_name not in EDITABLE_FIELDS]
    if forbidden:
        raise ValueError(f"field is not editable: {forbidden[0]}")

    updated = copy.deepcopy(result)
    revision_rows: list[Revision] = []
    for field_name, after in changes.items():
        before = copy.deepcopy(getattr(updated, field_name))
        if before == after:
            continue
        setattr(updated, field_name, copy.deepcopy(after))
        revision = Revision(
            revision_id=f"rev-{uuid.uuid4().hex[:12]}",
            sample_id=updated.sample_id,
            field_name=field_name,
            before=before,
            after=copy.deepcopy(after),
            editor=editor,
            edited_at=utc_now(),
            reason=reason,
            run_id=run_id or updated.run_id,
        )
        revision_rows.append(revision)

    if revision_rows:
        _validate_evidence_closure(updated)
        latest = revision_rows[-1]
        updated.human_revision = {
            "revision_id": latest.revision_id,
            "sample_id": latest.sample_id,
            "field_name": latest.field_name,
            "before": latest.before,
            "after": latest.after,
            "editor": latest.editor,
            "edited_at": latest.edited_at,
            "reason": latest.reason,
        }
        if repository is not None:
            for revision in revision_rows:
                repository.record_revision(revision)
            repository.save_result(updated)
    return updated, revision_rows


def _normalize_label_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize public/internal names and validate the controlled label set."""

    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("label changes must be a non-empty object")
    normalized: dict[str, Any] = {}
    for supplied_name, value in changes.items():
        if not isinstance(supplied_name, str):
            raise ValueError("label field names must be strings")
        field_name = LABEL_FIELD_ALIASES.get(supplied_name, supplied_name)
        if field_name not in LABEL_FIELDS:
            raise ValueError(f"label field is not editable: {supplied_name}")
        if field_name in normalized:
            raise ValueError(f"label field was supplied more than once: {supplied_name}")
        if field_name in {"scene_type", "subscene_type"}:
            if not isinstance(value, str) or not value.strip() or value.strip() == "-":
                raise ValueError(f"{field_name} must be a non-empty label")
            normalized[field_name] = value.strip()
        else:
            if not isinstance(value, list) or not value:
                raise ValueError("task_types must be a non-empty string array")
            labels = [item.strip() for item in value if isinstance(item, str) and item.strip()]
            if len(labels) != len(value) or not labels:
                raise ValueError("task_types must be a non-empty string array")
            if len(set(labels)) != len(labels):
                raise ValueError("task_types must not contain duplicates")
            normalized[field_name] = labels
    if "scene_type" in normalized and normalized["scene_type"] not in SCENE_TASKS:
        raise ValueError(f"unknown scene_type label: {normalized['scene_type']}")
    return normalized


def _label_input_data(result: EvaluationResult, *, scene_type: str, subscene_type: str) -> Any:
    """Keep public input metadata synchronized with corrected labels."""

    if not isinstance(result.input_data, dict):
        return result.input_data
    payload = copy.deepcopy(result.input_data)
    payload["subscene_type"] = subscene_type
    payload["capability_chain"] = _SCENE_CHAIN.get(scene_type, "-")
    return payload


def apply_label_correction(
    result: EvaluationResult,
    changes: Mapping[str, Any],
    *,
    editor: str,
    reason: str,
    run_id: str | None = None,
    repository: Any | None = None,
) -> tuple[EvaluationResult, list[Revision]]:
    """Correct controlled labels and explicitly invalidate derived analysis.

    The operation is separate from apply_human_revision. A label change
    changes the interpretation context, so previous metrics, evidence and
    conclusions remain only as audit material and are marked invalid. The
    result stays queryable with a 证据不足 conclusion until a new run
    recomputes the affected analysis under the corrected labels.
    """

    if not editor.strip():
        raise ValueError("editor is required")
    if not reason.strip():
        raise ValueError("reason is required")
    normalized = _normalize_label_changes(changes)
    updated = copy.deepcopy(result)
    revision_rows: list[Revision] = []
    for field_name, after in normalized.items():
        before = copy.deepcopy(getattr(updated, field_name))
        if before == after:
            continue
        setattr(updated, field_name, copy.deepcopy(after))
        revision_rows.append(
            Revision(
                revision_id=f"rev-{uuid.uuid4().hex[:12]}",
                sample_id=updated.sample_id,
                field_name=field_name,
                before=before,
                after=copy.deepcopy(after),
                editor=editor.strip(),
                edited_at=utc_now(),
                reason=reason.strip(),
                run_id=run_id or updated.run_id,
            )
        )

    if not revision_rows:
        return updated, []

    updated.input_data = _label_input_data(
        updated,
        scene_type=updated.scene_type,
        subscene_type=updated.subscene_type,
    )

    correction_id = (
        f"E-{updated.sample_id.replace(' ', '_')}-LABEL-CORRECTION-"
        f"{uuid.uuid4().hex[:8]}"
    )
    previous_metrics = copy.deepcopy(updated.quantitative_metrics)
    previous_conclusion = copy.deepcopy(updated.final_conclusion)
    # Keep the frozen evidence-item shape unchanged. The old evidence is
    # retained as an immutable audit snapshot; the dedicated correction
    # evidence below records which IDs are no longer applicable.
    old_evidence = copy.deepcopy(updated.evidence) if isinstance(updated.evidence, list) else []
    correction_evidence = {
        "evidence_id": correction_id,
        "type": "label_correction_invalidation",
        "source": "human_review",
        "location": "labels",
        "content": {
            "changed_fields": [row.field_name for row in revision_rows],
            "changes": {
                row.field_name: {"before": row.before, "after": row.after}
                for row in revision_rows
            },
            "invalidated_fields": [
                "quantitative_metrics",
                "quality_diagnosis",
                "evidence",
                "impact_assessment",
                "recommendations",
                "final_conclusion",
            ],
            "previous_metrics": previous_metrics,
            "previous_conclusion": previous_conclusion,
            "next_action": "使用纠正后的标签以新的run_id重新运行评测",
        },
    }
    updated.evidence = old_evidence + [correction_evidence]

    # Preserve threshold provenance while making invalidation machine visible.
    if isinstance(previous_metrics, dict):
        invalidated_metrics = copy.deepcopy(previous_metrics)
    else:
        invalidated_metrics = {
            "metric_name": "CER",
            "value": 0.0,
            "metric_version": updated.metric_version,
        }
    # Keep only the frozen metric contract. `status` communicates that the
    # old metric cannot currently be judged; the correction evidence carries
    # the invalidation ID and timestamp without adding schema fields.
    invalidated_metrics["status"] = "不判定"
    candidate_status = invalidated_metrics.get("candidate_status")
    if candidate_status not in {"通过", "需关注", "失败"}:
        candidate_status = "需关注"
    invalidated_metrics["candidate_status"] = candidate_status
    updated.quantitative_metrics = invalidated_metrics
    updated.quality_diagnosis = {
        "text": "标签已纠正，原指标、证据和分析结论已作废；需使用新标签重新运行评测。",
        "evidence_ids": [correction_id],
    }
    updated.impact_assessment = {
        "text": "标签变化会改变样例归属或任务解释，原影响评估暂不适用。",
        "evidence_ids": [correction_id],
    }
    updated.recommendations = [
        {
            "text": "按纠正后的场景、子场景和任务标签，以新的run_id重跑指标、证据和诊断。",
            "evidence_ids": [correction_id],
        }
    ]
    updated.final_conclusion = {
        "level": "证据不足",
        "basis": "标签纠正导致原派生结果失效，尚未完成新run重算。",
        "evidence_ids": [correction_id],
    }
    latest = revision_rows[-1]
    # Keep the public human_revision object within the frozen schema. The
    # correction kind and invalidation marker live in the dedicated evidence
    # record and the revisions audit table.
    updated.human_revision = {
        "revision_id": latest.revision_id,
        "sample_id": latest.sample_id,
        "field_name": latest.field_name,
        "before": latest.before,
        "after": latest.after,
        "editor": latest.editor,
        "edited_at": latest.edited_at,
        "reason": latest.reason,
    }
    if repository is not None:
        for revision in revision_rows:
            repository.record_revision(revision)
        repository.save_result(updated)
    return updated, revision_rows


class RevisionService:
    """Repository-aware facade used by UI/CLI integrations."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def revise(
        self,
        result: EvaluationResult,
        changes: Mapping[str, Any],
        *,
        editor: str,
        reason: str,
    ) -> EvaluationResult:
        updated, _ = apply_human_revision(
            result,
            changes,
            editor=editor,
            reason=reason,
            repository=self.repository,
        )
        return updated

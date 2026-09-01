"""Public serialization helpers used by persistence and integrations."""

from __future__ import annotations

from typing import Any

from .models import EvaluationResult, MISSING, utc_now


def result_from_dict(payload: dict[str, Any]) -> EvaluationResult:
    reverse = {value: key for key, value in EvaluationResult.FIELD_MAP.items()}
    normalized = {reverse.get(key, key): value for key, value in payload.items()}
    return EvaluationResult(
        sample_id=str(normalized.get("sample_id", "")),
        scene_type=str(normalized.get("scene_type", MISSING)),
        task_types=list(normalized.get("task_types", [])),
        input_data=normalized.get("input_data", MISSING),
        audio_info=normalized.get("audio_info", MISSING),
        reference_annotation=normalized.get("reference_annotation", MISSING),
        system_output=normalized.get("system_output", MISSING),
        quantitative_metrics=normalized.get("quantitative_metrics", MISSING),
        quality_diagnosis=normalized.get("quality_diagnosis", MISSING),
        evidence=normalized.get("evidence", MISSING),
        impact_assessment=normalized.get("impact_assessment", MISSING),
        recommendations=normalized.get("recommendations", MISSING),
        human_revision=normalized.get("human_revision", MISSING),
        final_conclusion=normalized.get("final_conclusion", MISSING),
        subscene_type=normalized.get("subscene_type", MISSING),
        run_id=str(normalized.get("run_id", "")),
        batch_id=str(normalized.get("batch_id", "")),
        status=str(normalized.get("status", "COMPLETED")),
        created_at=str(normalized.get("created_at", utc_now())),
        metric_version=str(normalized.get("metric_version", "cer-v1")),
        prompt_version=str(normalized.get("prompt_version", "offline-rules-v1")),
    )

"""Batch orchestration with per-sample failure isolation."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from .diagnosis import (
    DiagnosticContext,
    DiagnosticEngine,
    DeterministicRuleDiagnosticProvider,
    build_evidence_catalog,
)
from .metrics import METRIC_VERSION, CERResult, compute_cer
from .models import EvaluationResult, MISSING, Sample, utc_now
from .providers.base import ProviderExecutionResult, RetryingDiagnosticProvider
from .repository import EvaluationRepository


def _extract_text(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, str):
        return None if value in ("", MISSING) else value
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "", MISSING):
                return str(candidate)
    return None


def _extract_value(value: Any, keys: tuple[str, ...]) -> Any:
    """Extract a structured annotation without coercing dicts/lists to text."""

    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "", MISSING):
                return candidate
    elif value not in (None, "", MISSING):
        return value
    return None


def _canonical_value(value: Any) -> Any:
    """Normalize stage values deterministically while preserving their shape."""

    if isinstance(value, str):
        return " ".join(value.strip().split())
    if isinstance(value, dict):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    return value


def _stage_hash(reference: Any, hypothesis: Any) -> str:
    payload = json.dumps(
        {"reference": _canonical_value(reference), "hypothesis": _canonical_value(hypothesis)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stage_metric(
    stage: str,
    metric_name: str,
    reference: Any,
    hypothesis: Any,
) -> dict[str, Any]:
    applicable = reference is not None and hypothesis is not None
    normalized_reference = _canonical_value(reference)
    normalized_hypothesis = _canonical_value(hypothesis)
    matched = (
        bool(applicable and normalized_reference == normalized_hypothesis)
        if applicable
        else None
    )
    return {
        "stage": stage,
        "metric_name": metric_name,
        "value": 1.0 if matched is True else (0.0 if matched is False else None),
        "metric_version": "stage-exact-v1",
        "reference": reference if reference is not None else MISSING,
        "hypothesis": hypothesis if hypothesis is not None else MISSING,
        "matched": matched,
        "applicable": applicable,
        "candidate_status": (
            "通过" if matched is True else ("失败" if matched is False else "证据不足")
        ),
        "reproducible": True,
        "input_summary_sha256": _stage_hash(reference, hypothesis),
    }


def _build_stage_metrics(sample: Sample) -> list[dict[str, Any]]:
    """Compute deterministic downstream metrics from explicit annotations.

    The evaluator deliberately compares only values present in the reference
    and system-output objects.  It never fabricates a translation, intent,
    slot, action, or reply.
    """

    reference = sample.reference_annotation
    hypothesis = sample.system_output
    task_names = {str(item) for item in sample.task_types}
    is_translation = (
        sample.scene_type == "会议/办公语音翻译"
        or any("翻译" in item for item in task_names)
    )
    is_dialogue = (
        sample.scene_type == "车载/语音助手交互"
        or bool(task_names & {"语义理解", "对话生成"})
    )
    stages: list[dict[str, Any]] = []
    if is_translation:
        stages.append(
            _stage_metric(
                "machine_translation",
                "translation_exact_match",
                _extract_value(
                    reference,
                    ("translation", "translated_text", "reference_translation", "标准译文", "译文"),
                ),
                _extract_value(
                    hypothesis,
                    ("translation", "translated_text", "system_translation", "hypothesis_translation", "系统译文", "译文"),
                ),
            )
        )
    if is_dialogue:
        stages.append(
            _stage_metric(
                "intent_understanding",
                "intent_exact_match",
                _extract_value(reference, ("intent", "reference_intent", "expected_intent", "意图")),
                _extract_value(hypothesis, ("intent", "predicted_intent", "system_intent", "意图")),
            )
        )
        stages.append(
            _stage_metric(
                "slot_filling",
                "slot_exact_match",
                _extract_value(reference, ("slots", "reference_slots", "expected_slots", "槽位")),
                _extract_value(hypothesis, ("slots", "predicted_slots", "system_slots", "槽位")),
            )
        )
        stages.append(
            _stage_metric(
                "action_execution",
                "action_exact_match",
                _extract_value(reference, ("expected_action", "reference_action", "action", "动作")),
                _extract_value(hypothesis, ("action", "predicted_action", "system_action", "动作")),
            )
        )
        stages.append(
            _stage_metric(
                "response_generation",
                "response_exact_match",
                _extract_value(reference, ("expected_reply", "reference_reply", "reply", "回复")),
                _extract_value(hypothesis, ("reply", "response", "predicted_reply", "system_reply", "回复")),
            )
        )
    return stages


def _public_text_object(value: Any, key: str) -> Any:
    if isinstance(value, str) and value != MISSING:
        return {key: value}
    return value


SCENE_CHAINS = {
    "短语音/录音转写": "C01",
    "会议/办公语音翻译": "C02",
    "车载/语音助手交互": "C03",
}


def _public_input_data(sample: Sample) -> Any:
    if isinstance(sample.input_data, dict):
        payload = dict(sample.input_data)
    elif sample.input_data == MISSING:
        payload = {}
    else:
        payload = {"value": sample.input_data}
    payload["subscene_type"] = sample.subscene_type
    payload["capability_chain"] = SCENE_CHAINS.get(sample.scene_type, MISSING)
    return payload or MISSING


def _error_result(sample: Sample, batch_id: str, run_id: str, exc: Exception) -> EvaluationResult:
    evidence_id = f"E-{sample.sample_id}-ERROR"
    evidence = [
        {
            "evidence_id": evidence_id,
            "type": "pipeline_error",
            "source": "runtime",
            "location": "batch_processor",
            "content": {"error_type": type(exc).__name__},
        }
    ]
    diagnosis = {
        "text": "样例处理失败；详细错误已记录到内部日志。",
        "evidence_ids": [evidence_id],
    }
    return EvaluationResult(
        sample_id=sample.sample_id,
        scene_type=sample.scene_type,
        subscene_type=sample.subscene_type,
        task_types=sample.task_types,
        input_data=_public_input_data(sample),
        audio_info=sample.audio_info,
        reference_annotation=_public_text_object(sample.reference_annotation, "transcript"),
        system_output=_public_text_object(sample.system_output, "transcript"),
        quantitative_metrics=MISSING,
        quality_diagnosis=diagnosis,
        evidence=evidence,
        impact_assessment={
            "text": "处理失败，不能形成质量结论。",
            "evidence_ids": [evidence_id],
        },
        recommendations=[
            {
                "text": "根据内部日志修复输入或处理错误后，用新run_id重跑。",
                "evidence_ids": [evidence_id],
            }
        ],
        human_revision=MISSING,
        final_conclusion={
            "level": "失败",
            "basis": "管线处理失败",
            "evidence_ids": [evidence_id],
        },
        batch_id=batch_id,
        run_id=run_id,
        status="FAILED",
    )


def _ai_failed_result(
    sample: Sample,
    batch_id: str,
    run_id: str,
    outcome: ProviderExecutionResult,
    metrics: dict[str, Any] | str = MISSING,
) -> EvaluationResult:
    """Persist a provider failure without pretending that diagnosis succeeded."""

    error = outcome.error
    safe_id = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in sample.sample_id)
    evidence_id = f"E-{safe_id}-AI-FAILED"
    error_payload = error.to_dict() if error is not None else {"code": "AI_FAILED"}
    evidence = [{
        "evidence_id": evidence_id,
        "type": "provider_failure",
        "source": "diagnostic_provider",
        "location": "batch_processor",
        "content": {"status": outcome.status, "attempts": outcome.attempts, "error": error_payload},
    }]
    refs = [evidence_id]
    return EvaluationResult(
        sample_id=sample.sample_id,
        scene_type=sample.scene_type,
        subscene_type=sample.subscene_type,
        task_types=sample.task_types,
        input_data=_public_input_data(sample),
        audio_info=sample.audio_info,
        reference_annotation=_public_text_object(sample.reference_annotation, "transcript"),
        system_output=_public_text_object(sample.system_output, "transcript"),
        quantitative_metrics=metrics,
        quality_diagnosis={"text": "AI诊断未成功，未生成未经验证的质量结论。", "evidence_ids": refs},
        evidence=evidence,
        impact_assessment={"text": "由于AI诊断失败，影响暂不判定，需人工复核。", "evidence_ids": refs},
        recommendations=[{"text": "检查Provider错误和输入证据后，用新的run_id重试；在成功前保留人工复核状态。", "evidence_ids": refs}],
        human_revision=MISSING,
        final_conclusion={"level": "失败", "basis": "AI诊断Provider在有限重试后仍未返回可验证结果。", "evidence_ids": refs},
        batch_id=batch_id,
        run_id=run_id,
        status="AI_FAILED",
        prompt_version="provider-failed",
    )


class _ProviderBatchFailure(Exception):
    """Internal control flow carrying a non-throwing provider outcome."""

    def __init__(self, outcome: ProviderExecutionResult, metrics: dict[str, Any] | str) -> None:
        super().__init__(outcome.error.message if outcome.error else "AI provider failed")
        self.outcome = outcome
        self.metrics = metrics


@dataclass(slots=True)
class BatchRunSummary:
    batch_id: str
    run_id: str
    total: int
    completed: int
    failed: int
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "run_id": self.run_id,
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "status": self.status,
        }


class BatchProcessor:
    """Process samples independently and persist every terminal result."""

    def __init__(
        self,
        repository: EvaluationRepository,
        *,
        diagnostic_engine: DiagnosticEngine | None = None,
        provider: Any | None = None,
        provider_max_attempts: int = 3,
        thresholds: dict[str, float] | None = None,
        threshold_version: str = "runtime-default-v1",
        threshold_approval_status: str = "unknown",
        threshold_effective: bool | None = None,
    ) -> None:
        if diagnostic_engine is not None and provider is not None:
            raise ValueError("diagnostic_engine and provider are mutually exclusive")
        self.repository = repository
        self.diagnostic_engine = diagnostic_engine
        if provider is None and diagnostic_engine is None:
            provider = DeterministicRuleDiagnosticProvider()
        self.provider_adapter = (
            provider
            if isinstance(provider, RetryingDiagnosticProvider)
            else RetryingDiagnosticProvider(provider, max_attempts=provider_max_attempts)
        ) if provider is not None else None
        self.thresholds = thresholds or {"pass_max": 0.10, "attention_max": 0.20}
        self.threshold_version = threshold_version
        self.threshold_approval_status = threshold_approval_status
        self.threshold_effective = (
            threshold_effective
            if threshold_effective is not None
            else threshold_approval_status.strip().lower() in {"approved", "effective"}
        )

    def _metric_payload(
        self,
        result: CERResult,
        stage_metrics: list[dict[str, Any]],
    ) -> dict[str, Any] | str:
        if not result.applicable or result.value is None:
            return MISSING
        if result.value <= float(self.thresholds.get("pass_max", 0.10)):
            status = "通过"
        elif result.value <= float(self.thresholds.get("attention_max", 0.20)):
            status = "需关注"
        else:
            status = "失败"
        stage_statuses = {
            str(item.get("candidate_status"))
            for item in stage_metrics
            if item.get("applicable") is True or item.get("candidate_status") == "证据不足"
        }
        if "证据不足" in stage_statuses:
            overall_status = "证据不足"
        elif "失败" in stage_statuses:
            overall_status = "失败"
        else:
            overall_status = status
        input_summary = hashlib.sha256(
            (result.normalized_reference + "\n" + result.normalized_hypothesis).encode("utf-8")
        ).hexdigest()
        return {
            "metric_name": "CER",
            "value": result.value,
            "metric_version": result.metric_version,
            "normalization_version": result.normalization_version,
            "input_summary_sha256": input_summary,
            "status": status if self.threshold_effective else "不判定",
            "candidate_status": status,
            "overall_candidate_status": overall_status,
            "threshold_version": self.threshold_version,
            "threshold_approval_status": self.threshold_approval_status,
            "threshold_effective": self.threshold_effective,
            "stage_metrics": stage_metrics,
            "reproducible": True,
        }

    def evaluate_sample(self, sample: Sample, *, batch_id: str, run_id: str) -> EvaluationResult:
        reference = _extract_text(
            sample.reference_annotation,
            ("transcript", "text", "reference_text", "参考文本"),
        )
        hypothesis = _extract_text(
            sample.system_output,
            ("transcript", "text", "hypothesis", "识别文本"),
        )
        cer_result: CERResult | None = None
        stage_metrics = _build_stage_metrics(sample)
        metrics: dict[str, Any] | str = MISSING
        if reference is not None or "语音识别" in sample.task_types or "识别" in sample.task_types:
            cer_result = compute_cer(reference, hypothesis)
            metrics = self._metric_payload(cer_result, stage_metrics)
        context = DiagnosticContext(
            sample=sample,
            metrics=metrics,
            cer_result=cer_result,
            evidence=build_evidence_catalog(sample, cer_result, metrics, stage_metrics),
            thresholds=self.thresholds,
            threshold_version=self.threshold_version,
            threshold_approval_status=self.threshold_approval_status,
            threshold_effective=self.threshold_effective,
            stage_metrics=stage_metrics,
        )
        if self.provider_adapter is not None:
            outcome = self.provider_adapter.execute(context)
            if not outcome.ok or outcome.result is None:
                raise _ProviderBatchFailure(outcome, metrics)
            diagnosis = outcome.result
        else:
            assert self.diagnostic_engine is not None
            diagnosis = self.diagnostic_engine.diagnose(context)
        if not self.threshold_effective and diagnosis.final_conclusion.get("level") != "证据不足":
            evidence_ids = [
                str(item["evidence_id"])
                for item in diagnosis.evidence
                if isinstance(item, dict) and item.get("evidence_id")
            ]
            diagnosis.final_conclusion = {
                "level": "证据不足",
                "basis": (
                    f"阈值版本 {self.threshold_version} 的审批状态为 "
                    f"{self.threshold_approval_status} 且未生效；仅保留候选工程分带。"
                ),
                "evidence_ids": evidence_ids,
            }
        return EvaluationResult(
            sample_id=sample.sample_id,
            scene_type=sample.scene_type,
            subscene_type=sample.subscene_type,
            task_types=sample.task_types,
            input_data=_public_input_data(sample),
            audio_info=sample.audio_info,
            reference_annotation=_public_text_object(sample.reference_annotation, "transcript"),
            system_output=_public_text_object(sample.system_output, "transcript"),
            quantitative_metrics=metrics,
            quality_diagnosis=diagnosis.quality_diagnosis,
            evidence=diagnosis.evidence,
            impact_assessment=diagnosis.impact_assessment,
            recommendations=diagnosis.recommendations,
            human_revision=MISSING,
            final_conclusion=diagnosis.final_conclusion,
            batch_id=batch_id,
            run_id=run_id,
            status="COMPLETED",
            metric_version=METRIC_VERSION,
            prompt_version=diagnosis.prompt_version,
        )

    def run(
        self,
        samples: Iterable[Sample],
        *,
        batch_id: str | None = None,
        run_id: str | None = None,
    ) -> BatchRunSummary:
        sample_list = list(samples)
        batch_id = batch_id or f"batch-{uuid.uuid4().hex[:12]}"
        self.repository.ensure_batch(batch_id)
        run = self.repository.create_run(batch_id, run_id=run_id, total=len(sample_list))
        self.repository.append_log(
            level="INFO",
            batch_id=batch_id,
            run_id=run.run_id,
            stage="configuration",
            status="CONFIGURED",
            message=f"threshold_version={self.threshold_version}; approval_status={self.threshold_approval_status}; effective={self.threshold_effective}; pass_max={self.thresholds.get('pass_max')}; attention_max={self.thresholds.get('attention_max')}",
            version=self.threshold_version,
        )
        for sample in sample_list:
            self.repository.upsert_sample(batch_id, sample)
            self.repository.append_log(
                level="INFO",
                batch_id=batch_id,
                sample_id=sample.sample_id,
                run_id=run.run_id,
                stage="evaluate",
                status="STARTED",
                message="sample processing started",
                version=METRIC_VERSION,
            )
            try:
                result = self.evaluate_sample(sample, batch_id=batch_id, run_id=run.run_id)
                self.repository.save_result(result)
                run.completed += 1
                self.repository.append_log(
                    level="INFO",
                    batch_id=batch_id,
                    sample_id=sample.sample_id,
                    run_id=run.run_id,
                    stage="evaluate",
                    status="COMPLETED",
                    message="sample processing completed",
                    version=METRIC_VERSION,
                )
            except _ProviderBatchFailure as exc:
                run.failed += 1
                result = _ai_failed_result(sample, batch_id, run.run_id, exc.outcome, exc.metrics)
                self.repository.save_result(result)
                error = exc.outcome.error
                self.repository.append_log(
                    level="ERROR",
                    batch_id=batch_id,
                    sample_id=sample.sample_id,
                    run_id=run.run_id,
                    stage="diagnosis",
                    status="AI_FAILED",
                    error_code=error.code if error else "AI_FAILED",
                    message=error.message if error else "AI provider failed",
                    retry_count=exc.outcome.attempts,
                    version="provider-v1",
                )
            except Exception as exc:
                run.failed += 1
                result = _error_result(sample, batch_id, run.run_id, exc)
                self.repository.save_result(result)
                self.repository.append_log(
                    level="ERROR",
                    batch_id=batch_id,
                    sample_id=sample.sample_id,
                    run_id=run.run_id,
                    stage="evaluate",
                    status="FAILED",
                    error_code=type(exc).__name__,
                    message=str(exc),
                    version=METRIC_VERSION,
                )
        run.status = "COMPLETED" if run.failed == 0 else "PARTIAL_FAILED"
        run.finished_at = utc_now()
        self.repository.update_run(run)
        return BatchRunSummary(
            batch_id,
            run.run_id,
            run.total,
            run.completed,
            run.failed,
            run.status,
        )

    process = run

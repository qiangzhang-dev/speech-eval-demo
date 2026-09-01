"""Deterministic, evidence-grounded diagnostic provider.

This module is intentionally offline.  A network model adapter can implement
the same protocol later, while the rule provider keeps tests reproducible and
never invents missing audio or business facts.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from .metrics import CERResult
from .models import MISSING, Sample

PROMPT_VERSION = "offline-rules-v1"


@dataclass(slots=True)
class DiagnosticContext:
    sample: Sample
    metrics: dict[str, Any] | str = MISSING
    cer_result: CERResult | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    thresholds: dict[str, float] = field(default_factory=lambda: {"pass_max": 0.10, "attention_max": 0.20})
    threshold_version: str = "runtime-default-v1"
    threshold_approval_status: str = "unknown"
    threshold_effective: bool = True
    stage_metrics: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class DiagnosticResult:
    quality_diagnosis: dict[str, Any]
    evidence: list[dict[str, Any]]
    impact_assessment: dict[str, Any]
    recommendations: list[dict[str, Any]]
    final_conclusion: dict[str, Any]
    prompt_version: str = PROMPT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "quality_diagnosis": self.quality_diagnosis,
            "evidence": self.evidence,
            "impact_assessment": self.impact_assessment,
            "recommendations": self.recommendations,
            "final_conclusion": self.final_conclusion,
            "prompt_version": self.prompt_version,
        }


class DiagnosticProvider(Protocol):
    def diagnose(self, context: DiagnosticContext) -> DiagnosticResult:
        ...


def build_evidence_catalog(
    sample: Sample,
    cer_result: CERResult | None,
    metrics: dict[str, Any] | str = MISSING,
    stage_metrics: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build immutable evidence from metric inputs before any diagnosis."""

    sample_prefix = sample.sample_id.replace(" ", "_")
    evidence: list[dict[str, Any]] = []
    if cer_result is None or not cer_result.applicable or cer_result.value is None:
        evidence.append(
            {
                "evidence_id": f"E-{sample_prefix}-METRIC-NA",
                "type": "metric_not_applicable",
                "source": "computed_metric",
                "location": "quantitative_metrics.CER",
                "content": {
                    "metric_name": "CER",
                    "applicable": False,
                    "reason": cer_result.reason if cer_result is not None else "任务未配置CER",
                    "reference_annotation": sample.reference_annotation,
                    "system_output": sample.system_output,
                },
            }
        )
        for index, metric in enumerate(stage_metrics or [], start=1):
            evidence.append(_stage_evidence(sample, metric, index))
        return evidence

    input_summary = hashlib.sha256(
        (cer_result.normalized_reference + "\n" + cer_result.normalized_hypothesis).encode("utf-8")
    ).hexdigest()
    metric_payload = metrics if isinstance(metrics, dict) else {}
    evidence.append(
        {
            "evidence_id": f"E-{sample_prefix}-METRIC",
            "type": "metric_snapshot",
            "source": "computed_metric",
            "location": "quantitative_metrics.CER",
            "content": {
                "metric_name": "CER",
                "value": cer_result.value,
                "metric_version": cer_result.metric_version,
                "normalization_version": cer_result.normalization_version,
                "normalized_reference": cer_result.normalized_reference,
                "normalized_hypothesis": cer_result.normalized_hypothesis,
                "substitutions": cer_result.substitutions,
                "deletions": cer_result.deletions,
                "insertions": cer_result.insertions,
                "reference_length": cer_result.reference_length,
                "input_summary_sha256": metric_payload.get("input_summary_sha256", input_summary),
                "status": metric_payload.get("status", "不判定"),
                "candidate_status": metric_payload.get("candidate_status", MISSING),
                "overall_candidate_status": metric_payload.get(
                    "overall_candidate_status", metric_payload.get("candidate_status", MISSING)
                ),
                "threshold_version": metric_payload.get("threshold_version", MISSING),
                "threshold_approval_status": metric_payload.get(
                    "threshold_approval_status", MISSING
                ),
                "threshold_effective": metric_payload.get("threshold_effective", False),
            },
        }
    )
    for index, operation in enumerate(cer_result.operations, start=1):
        evidence.append(
            {
                "evidence_id": f"E-{sample_prefix}-{index:03d}",
                "type": "text_diff",
                "source": "reference_vs_output",
                "location": f"reference_index={operation.get('reference_index')}",
                "content": operation,
            }
        )
    for index, metric in enumerate(stage_metrics or [], start=1):
        evidence.append(_stage_evidence(sample, metric, index))
    return evidence


def _stage_evidence(sample: Sample, metric: dict[str, Any], index: int) -> dict[str, Any]:
    """Return a stable, self-contained evidence record for one downstream stage."""

    safe_stage = str(metric.get("stage", "stage")).replace(" ", "_")
    evidence_id = f"E-{sample.sample_id.replace(' ', '_')}-STAGE-{index:02d}-{safe_stage}"
    return {
        "evidence_id": evidence_id,
        "type": "stage_metric_snapshot",
        "source": "computed_stage_metric",
        "location": f"quantitative_metrics.stage_metrics[{index - 1}]",
        "content": dict(metric),
    }


def _text(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, str) and value != MISSING:
        return value
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, "", MISSING):
                return str(candidate)
    return None


class DeterministicRuleDiagnosticProvider:
    """Small rule provider suitable for offline CI and demo runs."""

    prompt_version = PROMPT_VERSION

    def diagnose(self, context: DiagnosticContext) -> DiagnosticResult:
        sample = context.sample
        cer_result = context.cer_result
        evidence = list(context.evidence)
        if not evidence:
            evidence = build_evidence_catalog(sample, cer_result, context.metrics)
        evidence_ids = [str(item["evidence_id"]) for item in evidence]
        if cer_result is None or not cer_result.applicable or cer_result.value is None:
            candidate_level = "证据不足"
            diagnosis = "缺少可用参考文本，无法计算CER；仅保留证据约束的人工复核入口。"
            impact = "量化结论不适用，任务质量需要人工复核。"
            actions = ["补充或确认参考文本/标注，再运行可复现指标；当前不判断原因。"]
        else:
            value = cer_result.value
            pass_max = float(context.thresholds.get("pass_max", 0.10))
            attention_max = float(context.thresholds.get("attention_max", 0.20))
            if value <= pass_max:
                candidate_level = "通过"
                diagnosis = f"CER={value:.6f}，未超过项目配置的通过阈值。"
                impact = "当前参考与系统输出在字符层面一致或差异较小。"
                actions = ["保留当前结果，继续按场景分层观察。"]
            elif value <= attention_max:
                candidate_level = "需关注"
                diagnosis = f"CER={value:.6f}，落在需关注区间，存在可定位字符差异。"
                impact = "局部识别差异可能影响可理解性或下游任务完成。"
                actions = ["按证据片段复核替换、删除或插入位置，并用同版本数据回归。"]
            else:
                candidate_level = "失败"
                diagnosis = f"CER={value:.6f}，超过项目配置的失败阈值。"
                impact = "字符差异较大，可能阻断语音识别或下游任务。"
                actions = ["定位替换、删除和插入类型，补充对应数据或调整链路后回归。"]

        stage_mismatches = [
            item for item in context.stage_metrics
            if item.get("applicable") is True and item.get("matched") is False
        ]
        if stage_mismatches:
            names = ", ".join(str(item.get("stage")) for item in stage_mismatches)
            details = "；".join(
                f"{item.get('stage')} reference={item.get('reference', MISSING)!r} "
                f"hypothesis={item.get('hypothesis', MISSING)!r}"
                for item in stage_mismatches
            )
            # A downstream mismatch is more specific than the CER band.  Put
            # it first so a passing ASR score cannot be read as a passing
            # translation/dialogue chain result.
            diagnosis = (
                f"声明的下游阶段候选失败：{names}；阶段证据显示参考与系统输出不一致"
                f"（{details}）。CER={cer_result.value:.6f} 仅反映语音识别字符差异，"
                "不能覆盖上述阶段。"
            )
            impact = "至少一个声明的下游阶段未通过，可能影响翻译或交互任务完成；需按阶段证据复核。"
            actions = [
                "逐字段核对阶段证据中的 reference/hypothesis，修正漏词或前缀差异后重跑该阶段。"
            ]
            candidate_level = "失败"
        elif context.stage_metrics and any(
            item.get("applicable") is False for item in context.stage_metrics
        ):
            missing_names = ", ".join(
                str(item.get("stage"))
                for item in context.stage_metrics
                if item.get("applicable") is False
            )
            diagnosis = (
                f"声明的下游阶段证据不足：{missing_names} 缺少可比较的参考或系统输出；"
                f"CER={cer_result.value:.6f} 仅反映语音识别，不能替代缺失阶段的评测。"
            )
            impact = "缺少下游阶段证据，相关任务影响暂不判定。"
            actions = ["补充对应阶段的参考标注和系统输出，再运行可复现指标；当前不判断原因。"]
            candidate_level = "证据不足"

        if context.threshold_effective:
            level = candidate_level
        else:
            level = "证据不足"
            diagnosis = (
                f"{diagnosis} 阈值版本 {context.threshold_version} 的审批状态为"
                f" {context.threshold_approval_status} 且未生效；以上仅为工程候选状态，"
                "不构成正式通过/失败判定。"
            )
            impact = f"{impact} 当前正式结论为证据不足。"
            actions = list(actions) + [
                "完成阈值审批并重新运行同版本证据；在此之前不判断正式质量等级及原因。"
            ]
        diagnosis_obj = {"text": diagnosis, "evidence_ids": evidence_ids}
        impact_obj = {"text": impact, "evidence_ids": evidence_ids}
        recommendations = [{"text": action, "evidence_ids": evidence_ids} for action in actions]
        conclusion = {"level": level, "basis": diagnosis, "evidence_ids": evidence_ids}
        return DiagnosticResult(diagnosis_obj, evidence, impact_obj, recommendations, conclusion)


class DiagnosticEngine:
    """Stable facade allowing a future LLM provider to be injected."""

    def __init__(self, provider: DiagnosticProvider | None = None) -> None:
        self.provider = provider or DeterministicRuleDiagnosticProvider()

    def diagnose(self, context: DiagnosticContext) -> DiagnosticResult:
        return self.provider.diagnose(context)


MockDiagnosticProvider = DeterministicRuleDiagnosticProvider

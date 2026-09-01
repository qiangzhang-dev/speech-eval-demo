"""Result validation and evidence-reference checks."""

from __future__ import annotations

from typing import Any, Iterable

from .models import CONCLUSIONS, EvaluationResult, MISSING, ValidationIssue


def _evidence_ids(value: Any) -> set[str]:
    if isinstance(value, dict):
        raw = value.get("evidence_ids", [])
        return {str(item) for item in raw} if isinstance(raw, list) else set()
    if isinstance(value, list):
        ids: set[str] = set()
        for item in value:
            ids |= _evidence_ids(item)
        return ids
    return set()


def validate_result(result: EvaluationResult) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    required = list(EvaluationResult.FIELD_MAP)
    for field_name in required:
        if getattr(result, field_name, None) is None:
            issues.append(ValidationIssue(None, "MISSING_RESULT_FIELD", f"结果字段为空: {field_name}", result.sample_id))
    evidence_items = result.evidence if isinstance(result.evidence, list) else []
    available = {str(item.get("evidence_id")) for item in evidence_items if isinstance(item, dict) and item.get("evidence_id")}
    for field_name in ("quality_diagnosis", "impact_assessment", "recommendations", "final_conclusion"):
        value = getattr(result, field_name)
        refs = _evidence_ids(value)
        if not refs:
            issues.append(ValidationIssue(None, "MISSING_EVIDENCE_REFERENCE", f"{field_name} 缺少evidence_ids", result.sample_id))
        unknown = refs - available
        if unknown:
            issues.append(ValidationIssue(None, "UNKNOWN_EVIDENCE_REFERENCE", f"{field_name} 引用了不存在证据: {sorted(unknown)}", result.sample_id))
    if isinstance(result.final_conclusion, dict):
        level = result.final_conclusion.get("level")
        if level not in CONCLUSIONS:
            issues.append(ValidationIssue(None, "INVALID_CONCLUSION", f"非法最终结论: {level}", result.sample_id))
    return issues


def validate_results(results: Iterable[EvaluationResult]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for result in results:
        if result.sample_id in seen:
            issues.append(ValidationIssue(None, "DUPLICATE_RESULT", "结果样例ID重复", result.sample_id))
        seen.add(result.sample_id)
        issues.extend(validate_result(result))
    return issues

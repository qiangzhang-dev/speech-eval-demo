"""Provider contracts, validation, retry policy, and failure outcomes.

The provider layer is deliberately independent from any one model vendor.  A
delegate may return a :class:`DiagnosticResult`, a mapping, or a JSON string;
the validating adapter always returns the core ``DiagnosticResult`` contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from ..diagnosis import DiagnosticContext, DiagnosticResult, PROMPT_VERSION
from ..models import CONCLUSIONS

COMPLETED = "COMPLETED"
AI_FAILED = "AI_FAILED"


class DiagnosticProviderError(Exception):
    """Base error with stable code and retry classification."""

    code = "PROVIDER_ERROR"
    retryable = False

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.cause = cause

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "cause_type": type(self.cause).__name__ if self.cause else None,
        }


class ProviderTimeoutError(DiagnosticProviderError):
    code = "PROVIDER_TIMEOUT"
    retryable = True


class InvalidJSONError(DiagnosticProviderError):
    code = "INVALID_JSON"
    retryable = True


class MissingFieldError(DiagnosticProviderError):
    code = "MISSING_FIELD"
    retryable = True

    def __init__(self, message: str, *, fields: list[str] | None = None) -> None:
        super().__init__(message)
        self.fields = fields or []

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "fields": list(self.fields)}


class InvalidEvidenceReferenceError(DiagnosticProviderError):
    code = "INVALID_EVIDENCE_REFERENCE"
    retryable = True

    def __init__(self, message: str, *, references: list[str] | None = None) -> None:
        super().__init__(message)
        self.references = references or []

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), "references": list(self.references)}


class InvalidStructureError(DiagnosticProviderError):
    code = "INVALID_STRUCTURE"
    retryable = True


class ProviderConfigurationError(DiagnosticProviderError):
    code = "PROVIDER_CONFIGURATION_ERROR"
    retryable = False


class ProviderTransportError(DiagnosticProviderError):
    code = "PROVIDER_TRANSPORT_ERROR"
    retryable = True


class ProviderFailedError(DiagnosticProviderError):
    """Raised by the DiagnosticProvider-compatible method after retry exhaustion."""

    code = AI_FAILED
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        final_error: DiagnosticProviderError,
        errors: list[DiagnosticProviderError],
    ) -> None:
        super().__init__(message, cause=final_error)
        self.attempts = attempts
        self.final_error = final_error
        self.errors = errors

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "attempts": self.attempts,
            "final_error": self.final_error.to_dict(),
            "errors": [error.to_dict() for error in self.errors],
        }


@runtime_checkable
class RawDiagnosticProvider(Protocol):
    """Minimal delegate protocol accepted by the provider adapter."""

    def diagnose(self, context: DiagnosticContext) -> DiagnosticResult | Mapping[str, Any] | str:
        ...


@runtime_checkable
class DiagnosticProvider(Protocol):
    """Core protocol consumed by ``DiagnosticEngine``."""

    def diagnose(self, context: DiagnosticContext) -> DiagnosticResult:
        ...


@dataclass(slots=True)
class ProviderExecutionResult:
    """Non-throwing execution outcome for batch orchestration."""

    status: str
    attempts: int
    result: DiagnosticResult | None = None
    error: DiagnosticProviderError | None = None
    errors: list[DiagnosticProviderError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == COMPLETED and self.result is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "attempts": self.attempts,
            "result": self.result.to_dict() if self.result else None,
            "error": self.error.to_dict() if self.error else None,
            "errors": [error.to_dict() for error in self.errors],
        }


_REQUIRED_TOP_LEVEL = {
    "quality_diagnosis",
    "evidence",
    "impact_assessment",
    "recommendations",
    "final_conclusion",
}
_ALLOWED_TOP_LEVEL = _REQUIRED_TOP_LEVEL | {"prompt_version"}

_CANONICAL_TO_INTERNAL = {
    "质量诊断": "quality_diagnosis",
    "影响评估": "impact_assessment",
    "优化建议": "recommendations",
    "最终结论": "final_conclusion",
}
_CANONICAL_TOP_LEVEL = set(_CANONICAL_TO_INTERNAL)
_CANONICAL_EVIDENCE_ID = re.compile(r"^E(?:VID)?-[A-Za-z0-9][A-Za-z0-9._-]*$")


def _require_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidStructureError(f"{field_name} must be an object")
    return value


def _require_text(value: Mapping[str, Any], field_name: str, names: tuple[str, ...]) -> None:
    if not any(isinstance(value.get(name), str) and value.get(name).strip() for name in names):
        raise MissingFieldError(
            f"{field_name} is missing a non-empty text field",
            fields=[f"{field_name}.{'|'.join(names)}"],
        )


def _require_exact_keys(
    value: Any,
    field_name: str,
    expected: set[str],
) -> Mapping[str, Any]:
    """Apply the canonical schema's required/additionalProperties contract."""

    mapping = _require_mapping(value, field_name)
    missing = sorted(expected - mapping.keys())
    if missing:
        raise MissingFieldError(
            f"{field_name} is missing required fields: {', '.join(missing)}",
            fields=[f"{field_name}.{name}" for name in missing],
        )
    extra = sorted(mapping.keys() - expected)
    if extra:
        raise InvalidStructureError(
            f"{field_name} contains unexpected fields: {', '.join(extra)}"
        )
    return mapping


def _validate_canonical_evidence_ids(value: Mapping[str, Any], field_name: str) -> None:
    raw = value.get("evidence_ids")
    if not isinstance(raw, list) or not raw:
        raise MissingFieldError(
            f"{field_name}.evidence_ids must be a non-empty string array",
            fields=[f"{field_name}.evidence_ids"],
        )
    if any(
        not isinstance(item, str) or not _CANONICAL_EVIDENCE_ID.fullmatch(item)
        for item in raw
    ):
        raise InvalidStructureError(
            f"{field_name}.evidence_ids does not match the canonical output schema"
        )
    if len(raw) != len(set(raw)):
        raise InvalidStructureError(
            f"{field_name}.evidence_ids must contain unique values"
        )


def _validate_canonical_shape(payload: Mapping[str, Any]) -> None:
    diagnosis = _require_exact_keys(
        payload["质量诊断"], "质量诊断", {"text", "evidence_ids"}
    )
    impact = _require_exact_keys(
        payload["影响评估"], "影响评估", {"text", "evidence_ids"}
    )
    for field_name, value in (("质量诊断", diagnosis), ("影响评估", impact)):
        _require_text(value, field_name, ("text",))
        _validate_canonical_evidence_ids(value, field_name)

    recommendations = payload["优化建议"]
    if not isinstance(recommendations, list) or not recommendations:
        raise MissingFieldError(
            "优化建议 must be a non-empty array",
            fields=["优化建议"],
        )
    for index, recommendation in enumerate(recommendations):
        field_name = f"优化建议[{index}]"
        mapping = _require_exact_keys(
            recommendation, field_name, {"text", "evidence_ids"}
        )
        _require_text(mapping, field_name, ("text",))
        _validate_canonical_evidence_ids(mapping, field_name)

    conclusion = _require_exact_keys(
        payload["最终结论"],
        "最终结论",
        {"level", "basis", "evidence_ids"},
    )
    _require_text(conclusion, "最终结论", ("basis",))
    _validate_canonical_evidence_ids(conclusion, "最终结论")


def _normalize_canonical_payload(
    payload: dict[str, Any],
    evidence_catalog: list[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Translate the manifest response into the internal result shape.

    The public schema intentionally does not let the model return evidence.
    Evidence is copied from the caller-owned catalog at this trust boundary.
    """

    if not (_CANONICAL_TOP_LEVEL & payload.keys()):
        return payload

    missing = sorted(_CANONICAL_TOP_LEVEL - payload.keys())
    if missing:
        raise MissingFieldError(
            f"provider response is missing required fields: {', '.join(missing)}",
            fields=missing,
        )
    extra = sorted(payload.keys() - _CANONICAL_TOP_LEVEL)
    if extra:
        raise InvalidStructureError(
            f"provider response contains unexpected fields: {', '.join(extra)}"
        )
    if not evidence_catalog:
        raise MissingFieldError(
            "canonical provider response requires the caller evidence catalog",
            fields=["evidence_catalog"],
        )

    _validate_canonical_shape(payload)
    normalized = {
        internal_name: payload[canonical_name]
        for canonical_name, internal_name in _CANONICAL_TO_INTERNAL.items()
    }
    normalized["evidence"] = list(evidence_catalog)
    return normalized


def _component_references(value: Any, field_name: str) -> set[str]:
    mapping = _require_mapping(value, field_name)
    raw = mapping.get("evidence_ids")
    if not isinstance(raw, list) or not raw or any(not isinstance(item, str) or not item for item in raw):
        raise MissingFieldError(
            f"{field_name}.evidence_ids must be a non-empty string array",
            fields=[f"{field_name}.evidence_ids"],
        )
    return set(raw)


def _coerce_payload(raw: DiagnosticResult | Mapping[str, Any] | str) -> dict[str, Any]:
    if isinstance(raw, DiagnosticResult):
        return raw.to_dict()
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise InvalidJSONError("provider response is not valid JSON", cause=exc) from exc
        if not isinstance(decoded, dict):
            raise InvalidStructureError("provider JSON root must be an object")
        return decoded
    if isinstance(raw, Mapping):
        return dict(raw)
    raise InvalidStructureError(
        f"provider returned unsupported type: {type(raw).__name__}"
    )


def validate_diagnostic_response(
    raw: DiagnosticResult | Mapping[str, Any] | str,
    *,
    default_prompt_version: str = PROMPT_VERSION,
    evidence_catalog: list[Mapping[str, Any]] | None = None,
) -> DiagnosticResult:
    """Validate shape and the complete evidence-reference closure.

    Canonical manifest responses use four Chinese top-level fields. They are
    normalized here while immutable evidence is supplied by the caller. The
    established English internal protocol remains accepted unchanged.
    """

    payload = _normalize_canonical_payload(_coerce_payload(raw), evidence_catalog)
    missing = sorted(_REQUIRED_TOP_LEVEL - payload.keys())
    if missing:
        raise MissingFieldError(
            f"provider response is missing required fields: {', '.join(missing)}",
            fields=missing,
        )
    extra = sorted(payload.keys() - _ALLOWED_TOP_LEVEL)
    if extra:
        raise InvalidStructureError(
            f"provider response contains unexpected fields: {', '.join(extra)}"
        )

    diagnosis = _require_mapping(payload["quality_diagnosis"], "quality_diagnosis")
    _require_text(diagnosis, "quality_diagnosis", ("text", "摘要"))
    impact = _require_mapping(payload["impact_assessment"], "impact_assessment")
    _require_text(impact, "impact_assessment", ("text", "摘要", "说明"))

    evidence = payload["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise MissingFieldError("evidence must be a non-empty array", fields=["evidence"])
    evidence_ids: set[str] = set()
    normalized_evidence: list[dict[str, Any]] = []
    for index, item in enumerate(evidence):
        mapping = _require_mapping(item, f"evidence[{index}]")
        required = [name for name in ("evidence_id", "source", "location", "content") if name not in mapping]
        if "type" not in mapping and "evidence_type" not in mapping:
            required.append("type")
        if required:
            raise MissingFieldError(
                f"evidence[{index}] is missing fields: {', '.join(required)}",
                fields=[f"evidence[{index}].{name}" for name in required],
            )
        evidence_id = mapping.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise InvalidStructureError(f"evidence[{index}].evidence_id must be non-empty")
        if evidence_id in evidence_ids:
            raise InvalidStructureError(f"duplicate evidence_id: {evidence_id}")
        evidence_ids.add(evidence_id)
        normalized_evidence.append(dict(mapping))

    recommendations = payload["recommendations"]
    if not isinstance(recommendations, list) or not recommendations:
        raise MissingFieldError(
            "recommendations must be a non-empty array",
            fields=["recommendations"],
        )

    references: dict[str, set[str]] = {
        "quality_diagnosis": _component_references(diagnosis, "quality_diagnosis"),
        "impact_assessment": _component_references(impact, "impact_assessment"),
    }
    normalized_recommendations: list[dict[str, Any]] = []
    for index, recommendation in enumerate(recommendations):
        mapping = _require_mapping(recommendation, f"recommendations[{index}]")
        _require_text(mapping, f"recommendations[{index}]", ("text", "摘要", "行动项"))
        references[f"recommendations[{index}]"] = _component_references(
            mapping, f"recommendations[{index}]"
        )
        normalized_recommendations.append(dict(mapping))

    conclusion = _require_mapping(payload["final_conclusion"], "final_conclusion")
    level = conclusion.get("level")
    if level not in CONCLUSIONS:
        raise InvalidStructureError(
            "final_conclusion.level must be one of 通过/需关注/失败/证据不足"
        )
    _require_text(conclusion, "final_conclusion", ("basis", "依据", "摘要"))
    references["final_conclusion"] = _component_references(
        conclusion, "final_conclusion"
    )

    invalid: list[str] = []
    for field_name, field_references in references.items():
        for evidence_id in sorted(field_references - evidence_ids):
            invalid.append(f"{field_name}:{evidence_id}")
    if invalid:
        raise InvalidEvidenceReferenceError(
            "provider response references evidence IDs that do not exist",
            references=invalid,
        )

    return DiagnosticResult(
        quality_diagnosis=dict(diagnosis),
        evidence=normalized_evidence,
        impact_assessment=dict(impact),
        recommendations=normalized_recommendations,
        final_conclusion=dict(conclusion),
        prompt_version=str(payload.get("prompt_version") or default_prompt_version),
    )


def classify_provider_error(exc: BaseException) -> DiagnosticProviderError:
    """Convert arbitrary delegate failures into stable provider categories."""

    if isinstance(exc, DiagnosticProviderError):
        return exc
    if isinstance(exc, TimeoutError):
        return ProviderTimeoutError("provider request timed out", cause=exc)
    if isinstance(exc, json.JSONDecodeError):
        return InvalidJSONError("provider response is not valid JSON", cause=exc)
    return ProviderTransportError(
        f"provider call failed: {type(exc).__name__}: {exc}", cause=exc
    )


class ValidatingDiagnosticProvider:
    """Adapt raw providers to the core validated provider protocol."""

    def __init__(self, delegate: RawDiagnosticProvider, *, prompt_version: str = PROMPT_VERSION) -> None:
        self.delegate = delegate
        self.prompt_version = prompt_version

    def diagnose(self, context: DiagnosticContext) -> DiagnosticResult:
        try:
            raw = self.delegate.diagnose(context)
            return validate_diagnostic_response(
                raw, default_prompt_version=self.prompt_version
            )
        except BaseException as exc:
            error = classify_provider_error(exc)
            if error is exc:
                raise
            raise error from exc


class RetryingDiagnosticProvider:
    """Finite retry adapter with both throwing and non-throwing APIs."""

    def __init__(
        self,
        delegate: RawDiagnosticProvider,
        *,
        max_attempts: int = 3,
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.delegate = delegate
        self.max_attempts = max_attempts
        self.validator = ValidatingDiagnosticProvider(
            delegate, prompt_version=prompt_version
        )

    def execute(self, context: DiagnosticContext) -> ProviderExecutionResult:
        errors: list[DiagnosticProviderError] = []
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = self.validator.diagnose(context)
                return ProviderExecutionResult(
                    status=COMPLETED,
                    attempts=attempt,
                    result=result,
                    errors=errors,
                )
            except BaseException as exc:
                error = classify_provider_error(exc)
                errors.append(error)
                if not error.retryable or attempt == self.max_attempts:
                    return ProviderExecutionResult(
                        status=AI_FAILED,
                        attempts=attempt,
                        error=error,
                        errors=errors,
                    )
        raise AssertionError("finite retry loop exhausted without outcome")

    def diagnose(self, context: DiagnosticContext) -> DiagnosticResult:
        outcome = self.execute(context)
        if outcome.ok and outcome.result is not None:
            return outcome.result
        assert outcome.error is not None
        raise ProviderFailedError(
            f"diagnostic provider failed after {outcome.attempts} attempt(s)",
            attempts=outcome.attempts,
            final_error=outcome.error,
            errors=outcome.errors,
        )


DiagnosticProviderAdapter = RetryingDiagnosticProvider

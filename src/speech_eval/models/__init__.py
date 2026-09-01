"""Authoritative P1 data contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

MISSING = "-"
CONCLUSIONS = {"通过", "需关注", "失败", "证据不足"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class ValidationIssue:
    row: int | None
    code: str
    message: str
    sample_id: str | None = None
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Sample:
    sample_id: str
    scene_type: str
    task_types: list[str]
    input_data: dict[str, Any] | str = field(default_factory=dict)
    audio_info: dict[str, Any] | str = MISSING
    reference_annotation: dict[str, Any] | str = MISSING
    system_output: dict[str, Any] | str = field(default_factory=dict)
    subscene_type: str = MISSING
    source_row: int | None = None
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Evidence:
    evidence_id: str
    evidence_type: str
    source: str
    location: str
    content: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Revision:
    revision_id: str
    sample_id: str
    field_name: str
    before: Any
    after: Any
    editor: str
    edited_at: str
    reason: str
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvaluationResult:
    """One result containing exactly the assignment's fourteen public fields."""

    sample_id: str
    scene_type: str
    task_types: list[str]
    input_data: dict[str, Any] | str
    audio_info: dict[str, Any] | str
    reference_annotation: dict[str, Any] | str
    system_output: dict[str, Any] | str
    quantitative_metrics: dict[str, Any] | str
    quality_diagnosis: dict[str, Any] | str
    evidence: list[dict[str, Any]] | str
    impact_assessment: dict[str, Any] | str
    recommendations: list[dict[str, Any]] | str
    human_revision: dict[str, Any] | str
    final_conclusion: dict[str, Any] | str
    subscene_type: str = MISSING
    run_id: str = ""
    batch_id: str = ""
    status: str = "COMPLETED"
    created_at: str = field(default_factory=utc_now)
    metric_version: str = "cer-v1"
    prompt_version: str = "offline-rules-v1"

    FIELD_MAP: ClassVar[dict[str, str]] = {
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

    def to_dict(self, *, chinese_fields: bool = True) -> dict[str, Any]:
        public = {key: getattr(self, key) for key in self.FIELD_MAP}
        if chinese_fields:
            return {self.FIELD_MAP[key]: value for key, value in public.items()}
        return {
            **public,
            "subscene_type": self.subscene_type,
            "run_id": self.run_id,
            "batch_id": self.batch_id,
            "status": self.status,
            "created_at": self.created_at,
            "metric_version": self.metric_version,
            "prompt_version": self.prompt_version,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EvaluationResult":
        reverse = {value: key for key, value in cls.FIELD_MAP.items()}
        normalized = {reverse.get(key, key): value for key, value in payload.items()}
        metadata = normalized.get("运行元数据", {})
        if isinstance(metadata, dict):
            normalized.update({key: value for key, value in metadata.items() if key not in normalized})
        return cls(
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
            subscene_type=str(normalized.get("subscene_type", MISSING)),
            run_id=str(normalized.get("run_id", "")),
            batch_id=str(normalized.get("batch_id", "")),
            status=str(normalized.get("status", "COMPLETED")),
            created_at=str(normalized.get("created_at", utc_now())),
            metric_version=str(normalized.get("metric_version", "cer-v1")),
            prompt_version=str(normalized.get("prompt_version", "offline-rules-v1")),
        )


@dataclass(slots=True)
class EvaluationRun:
    run_id: str
    batch_id: str
    started_at: str
    finished_at: str | None = None
    status: str = "RUNNING"
    total: int = 0
    completed: int = 0
    failed: int = 0


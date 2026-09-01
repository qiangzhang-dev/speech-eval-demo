"""Authoritative manifest reader facade."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..labeling import suggest_labels
from ..models import MISSING, Sample, ValidationIssue

class ManifestError(ValueError):
    pass

ALIASES = {
    "sample_id": ("sample_id", "id", "样例ID", "样例id"),
    "scene_type": ("scene_type", "scene", "场景类型"),
    "subscene_type": ("subscene_type", "subscene", "subscenario", "子场景", "子场景类型"),
    "task_types": ("task_types", "task_type", "tasks", "任务类型"),
    "input_data": ("input_data", "input", "输入数据"),
    "input_path": ("input_path", "path"),
    "audio_info": ("audio_info", "audio_metadata", "音频信息"),
    "reference_annotation": ("reference_annotation", "reference", "reference_text", "参考文本/标注", "参考标注"),
    "system_output": ("system_output", "output", "hypothesis", "系统输出"),
}

@dataclass(slots=True)
class ManifestValidation:
    samples: list[Sample] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)
    source_path: str | None = None
    @property
    def ok(self) -> bool: return not any(i.severity == "error" for i in self.issues)
    @property
    def errors(self) -> list[ValidationIssue]: return [i for i in self.issues if i.severity == "error"]
    def to_dict(self) -> dict[str, Any]: return {"ok": self.ok, "source_path": self.source_path, "count": len(self.samples), "samples": [s.to_dict() for s in self.samples], "issues": [i.to_dict() for i in self.issues]}

def _first(row: dict[str, Any], key: str, default: Any = None) -> Any:
    for alias in ALIASES[key]:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    return default

def _jsonish(value: Any, default: Any = MISSING) -> Any:
    if value in (None, "", "-", "—", "null", "None"): return default
    if isinstance(value, (dict, list, int, float, bool)): return value
    try: return json.loads(str(value))
    except json.JSONDecodeError: return str(value)

def _tasks(value: Any) -> list[str]:
    parsed = _jsonish(value, [])
    if isinstance(parsed, list): return [str(x).strip() for x in parsed if str(x).strip()]
    if isinstance(parsed, str): return [x.strip() for x in parsed.replace("；", ",").replace("、", ",").replace("|", ",").split(",") if x.strip()]
    return []


def _resolve_input_path(value: Any, base: Path) -> Path:
    candidate = Path(str(value))
    return (candidate if candidate.is_absolute() else base / candidate).resolve()


def _is_within_manifest_root(candidate: Path, base: Path) -> bool:
    try:
        candidate.relative_to(base)
    except ValueError:
        return False
    return True


def _label_consistency_issues(
    *, row_number: int, sample_id: str, subscene: Any,
    raw_input: Any, base: Path, check_paths: bool,
) -> list[ValidationIssue]:
    """Expose label conflicts as non-blocking human-review warnings."""

    issues: list[ValidationIssue] = []
    top_subscene = str(subscene or MISSING).strip()
    if not isinstance(raw_input, dict) or top_subscene == MISSING:
        return issues
    embedded = raw_input.get("subscene_type")
    if embedded not in (None, "", MISSING) and str(embedded).strip() != top_subscene:
        issues.append(ValidationIssue(
            row_number, "LABEL_METADATA_CONFLICT",
            f"清单子场景 {top_subscene} 与输入元数据子场景 {embedded} 不一致，需人工确认",
            sample_id, severity="warning",
        ))
    source_value = raw_input.get("path") or raw_input.get("input_path") or raw_input.get("audio_path")
    if check_paths and source_value:
        try:
            source_path = _resolve_input_path(source_value, base)
        except (OSError, RuntimeError, ValueError):
            return issues
        if not _is_within_manifest_root(source_path, base):
            return issues
        if source_path.is_file() and source_path.suffix.casefold() == ".json":
            try:
                source = json.loads(source_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, json.JSONDecodeError):
                source = None
            source_subscene = source.get("subscene_type") if isinstance(source, dict) else None
            if source_subscene not in (None, "", MISSING) and str(source_subscene).strip() != top_subscene:
                issues.append(ValidationIssue(
                    row_number, "LABEL_METADATA_CONFLICT",
                    f"清单子场景 {top_subscene} 与来源记录 {source_path.name} 的子场景 {source_subscene} 不一致，需人工确认",
                    sample_id, severity="warning",
                ))
    return issues

def _read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle: return [dict(row) for row in csv.DictReader(handle)]
    if path.suffix.lower() == ".jsonl": return [json.loads(x) for x in path.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig")); payload = payload.get("samples", payload.get("data", [payload])) if isinstance(payload, dict) else payload
        if not isinstance(payload, list): raise ManifestError("JSON manifest must contain an array")
        return [dict(x) for x in payload]
    raise ManifestError(f"unsupported manifest extension: {path.suffix}")

def validate_rows(rows: Iterable[dict[str, Any]], *, source_path: str | None = None, check_paths: bool = True) -> ManifestValidation:
    base = Path(source_path).resolve().parent if source_path else Path.cwd()
    report = ManifestValidation(source_path=source_path)
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        sample_id = _first(row, "sample_id")
        scene = _first(row, "scene_type")
        tasks = _tasks(_first(row, "task_types"))
        if sample_id is None or not str(sample_id).strip():
            report.issues.append(ValidationIssue(row_number, "MISSING_SAMPLE_ID", "样例ID不能为空"))
            continue
        sample_id = str(sample_id).strip()
        if sample_id in seen:
            report.issues.append(ValidationIssue(row_number, "DUPLICATE_SAMPLE_ID", "样例ID重复", sample_id))
            continue
        seen.add(sample_id)
        raw_input = _jsonish(_first(row, "input_data"), default={})
        if raw_input == {} and _first(row, "input_path"):
            raw_input = {"path": _first(row, "input_path")}
        missing_scene = scene is None or not str(scene).strip()
        missing_tasks = not tasks
        suggestion = suggest_labels(row) if missing_scene or missing_tasks else None
        if suggestion is not None:
            if missing_scene:
                scene = suggestion.scene_type
                report.issues.append(
                    ValidationIssue(
                        row_number,
                        "AUTO_LABELED_SCENE",
                        f"场景类型由系统建议为 {scene}，需人工确认",
                        sample_id,
                        severity="warning",
                    )
                )
            if missing_tasks:
                tasks = list(suggestion.task_types)
                report.issues.append(
                    ValidationIssue(
                        row_number,
                        "AUTO_LABELED_TASK",
                        f"任务类型由系统建议为 {', '.join(tasks)}，需人工确认",
                        sample_id,
                        severity="warning",
                    )
                )
            if not isinstance(raw_input, dict):
                raw_input = {"value": raw_input}
            raw_input = dict(raw_input)
            raw_input["labeling_provenance"] = suggestion.to_dict()
        if scene is None or not str(scene).strip():
            report.issues.append(
                ValidationIssue(
                    row_number,
                    "MISSING_SCENE",
                    "场景类型不能为空且无充分证据可自动建议",
                    sample_id,
                )
            )
        if not tasks:
            report.issues.append(
                ValidationIssue(
                    row_number,
                    "MISSING_TASK",
                    "任务类型至少包含一项且无充分证据可自动建议",
                    sample_id,
                )
            )
        if isinstance(raw_input, dict) and check_paths:
            for key in ("path", "audio_path", "input_path"):
                if raw_input.get(key):
                    try:
                        candidate = _resolve_input_path(raw_input[key], base)
                    except (OSError, RuntimeError, ValueError):
                        report.issues.append(
                            ValidationIssue(
                                row_number,
                                "INVALID_INPUT_PATH",
                                f"输入路径无效: {raw_input[key]}",
                                sample_id,
                            )
                        )
                        continue
                    if not _is_within_manifest_root(candidate, base):
                        report.issues.append(
                            ValidationIssue(
                                row_number,
                                "INPUT_PATH_OUTSIDE_MANIFEST_ROOT",
                                f"输入路径越出清单目录: {raw_input[key]}",
                                sample_id,
                            )
                        )
                        continue
                    if not candidate.exists():
                        report.issues.append(
                            ValidationIssue(
                                row_number,
                                "MISSING_INPUT_FILE",
                                f"输入文件不存在: {raw_input[key]}",
                                sample_id,
                            )
                        )
        report.issues.extend(
            _label_consistency_issues(
                row_number=row_number,
                sample_id=sample_id,
                subscene=_first(row, "subscene_type", MISSING),
                raw_input=raw_input,
                base=base,
                check_paths=check_paths,
            )
        )
        report.samples.append(
            Sample(
                sample_id=sample_id,
                scene_type=str(scene or MISSING).strip(),
                task_types=tasks,
                input_data=raw_input,
                audio_info=_jsonish(_first(row, "audio_info"), MISSING),
                reference_annotation=_jsonish(_first(row, "reference_annotation"), MISSING),
                system_output=_jsonish(_first(row, "system_output"), {}),
                subscene_type=str(_first(row, "subscene_type", MISSING) or MISSING).strip(),
                source_row=row_number,
                source_path=source_path,
            )
        )
    return report

def validate_manifest(path: str | Path, *, check_paths: bool = True) -> ManifestValidation:
    manifest_path = Path(path)
    if not manifest_path.exists(): return ManifestValidation(source_path=str(manifest_path), issues=[ValidationIssue(None, "MISSING_MANIFEST", f"清单不存在: {manifest_path}")])
    try: rows = _read_rows(manifest_path)
    except Exception as exc: return ManifestValidation(source_path=str(manifest_path), issues=[ValidationIssue(None, "READ_ERROR", str(exc))])
    return validate_rows(rows, source_path=str(manifest_path), check_paths=check_paths)

def load_manifest(path: str | Path, *, check_paths: bool = True) -> list[Sample]:
    report = validate_manifest(path, check_paths=check_paths)
    if not report.ok: raise ManifestError("; ".join(f"{i.code}: {i.message}" for i in report.errors[:10]))
    return report.samples

#!/usr/bin/env python3
"""Validate generated fixture files with only the Python standard library."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

FIELD_NAMES = [
    "样例ID", "场景类型", "任务类型", "输入数据", "音频信息", "参考文本/标注",
    "系统输出", "量化指标", "质量诊断", "证据片段", "影响评估", "优化建议",
    "人工修订", "最终结论",
]
EVIDENCE_REF_FIELDS = ("质量诊断", "影响评估", "优化建议", "最终结论")


def _schema_type_ok(value: Any, expected: str | list[str]) -> bool:
    expected_types = [expected] if isinstance(expected, str) else expected
    for kind in expected_types:
        if kind == "object" and isinstance(value, dict):
            return True
        if kind == "array" and isinstance(value, list):
            return True
        if kind == "string" and isinstance(value, str):
            return True
        if kind == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if kind == "boolean" and isinstance(value, bool):
            return True
        if kind == "null" and value is None:
            return True
    return False


def _schema_errors(value: Any, node: dict[str, Any], root: dict[str, Any], path: str) -> list[str]:
    if "$ref" in node:
        ref = node["$ref"]
        if ref.startswith("#/$defs/"):
            node = root["$defs"][ref.split("/")[-1]]
        else:
            return []
    if "oneOf" in node:
        branch_errors = [_schema_errors(value, branch, root, path) for branch in node["oneOf"]]
        if any(not errors for errors in branch_errors):
            return []
        return [f"{path}: no oneOf branch matched"]
    errors: list[str] = []
    if "const" in node and value != node["const"]:
        errors.append(f"{path}: expected const {node['const']!r}")
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{path}: value not in enum")
    if "type" in node and not _schema_type_ok(value, node["type"]):
        return errors + [f"{path}: wrong type"]
    if isinstance(value, str):
        if len(value) < node.get("minLength", 0):
            errors.append(f"{path}: shorter than minLength")
        if "maxLength" in node and len(value) > node["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
        if "pattern" in node and not re.fullmatch(node["pattern"], value):
            errors.append(f"{path}: pattern mismatch")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in node and value < node["minimum"]:
            errors.append(f"{path}: below minimum")
    if isinstance(value, list):
        if len(value) < node.get("minItems", 0):
            errors.append(f"{path}: fewer than minItems")
        if node.get("uniqueItems"):
            encoded = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: duplicate array items")
        if "items" in node:
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, node["items"], root, f"{path}[{index}]"))
    if isinstance(value, dict):
        if len(value) < node.get("minProperties", 0):
            errors.append(f"{path}: fewer than minProperties")
        required = set(node.get("required", []))
        missing = required - set(value)
        errors.extend(f"{path}: missing required property {key}" for key in sorted(missing))
        properties = node.get("properties", {})
        if node.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            errors.extend(f"{path}: unexpected property {key}" for key in sorted(extras))
        for key, child in properties.items():
            if key in value:
                errors.extend(_schema_errors(value[key], child, root, f"{path}.{key}"))
    return errors


def validate_result_shape(result: dict[str, Any], schema_path: Path | None = None) -> list[str]:
    """Validate the strict 14-field shape and, when supplied, the frozen schema."""

    errors: list[str] = []
    if set(result) != set(FIELD_NAMES):
        errors.append("$: expected exactly 14 top-level fields")
    if not isinstance(result.get("场景类型"), str) or not result.get("场景类型"):
        errors.append("$.场景类型: expected non-empty string")
    tasks = result.get("任务类型")
    if not isinstance(tasks, list) or not tasks or any(not isinstance(item, str) for item in tasks):
        errors.append("$.任务类型: expected non-empty string array")
    for field in ("输入数据", "音频信息", "参考文本/标注", "系统输出"):
        value = result.get(field)
        if value != "-" and not isinstance(value, dict):
            errors.append(f"$.{field}: expected object or '-'")
    metric = result.get("量化指标")
    if metric != "-" and (not isinstance(metric, dict) or not {"metric_name", "value", "metric_version"}.issubset(metric)):
        errors.append("$.量化指标: expected metric object or '-'")
    evidence = result.get("证据片段")
    evidence_ids: set[str] = set()
    if evidence != "-":
        if not isinstance(evidence, list) or not evidence:
            errors.append("$.证据片段: expected non-empty array or '-'")
        else:
            for index, item in enumerate(evidence):
                required = {"evidence_id", "type", "source", "location", "content"}
                if not isinstance(item, dict) or not required.issubset(item):
                    errors.append(f"$.证据片段[{index}]: missing required evidence keys")
                elif not re.fullmatch(r"E(?:VID)?-[A-Za-z0-9][A-Za-z0-9._-]*", item["evidence_id"]):
                    errors.append(f"$.证据片段[{index}]: invalid evidence_id")
                else:
                    evidence_ids.add(item["evidence_id"])
    references: list[str] = []
    for field in ("质量诊断", "影响评估"):
        value = result.get(field)
        if value != "-":
            if not isinstance(value, dict) or not isinstance(value.get("evidence_ids"), list):
                errors.append(f"$.{field}: evidence_ids is required")
            else:
                references.extend(value["evidence_ids"])
    recommendations = result.get("优化建议")
    if recommendations != "-":
        if not isinstance(recommendations, list) or not recommendations:
            errors.append("$.优化建议: expected non-empty array or '-'")
        else:
            for index, item in enumerate(recommendations):
                if not isinstance(item, dict) or not {"text", "evidence_ids"}.issubset(item):
                    errors.append(f"$.优化建议[{index}]: text/evidence_ids are required")
                else:
                    references.extend(item["evidence_ids"])
    revision = result.get("人工修订")
    if revision != "-":
        required_revision = {"revision_id", "sample_id", "field_name", "before", "after", "editor", "edited_at", "reason"}
        if not isinstance(revision, dict) or not required_revision.issubset(revision):
            errors.append("$.人工修订: complete before/after/editor/time/reason diff is required")
    conclusion = result.get("最终结论")
    if conclusion != "-":
        if not isinstance(conclusion, dict) or not {"level", "basis", "evidence_ids"}.issubset(conclusion):
            errors.append("$.最终结论: level/basis/evidence_ids are required")
        else:
            references.extend(conclusion["evidence_ids"])
    if evidence != "-":
        unresolved = sorted(set(references) - evidence_ids)
        errors.extend(f"$: unresolved evidence reference {item}" for item in unresolved)
    if schema_path is not None:
        schema_file = Path(schema_path)
        if not schema_file.exists():
            errors.append(f"$: schema file does not exist: {schema_file}")
        else:
            try:
                schema = json.loads(schema_file.read_text(encoding="utf-8"))
                errors.extend(_schema_errors(result, schema, schema, "$"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"$: schema could not be loaded: {exc}")
    return errors


def load_results(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data["results"] if isinstance(data, dict) else data


def validate_results(
    results: list[dict[str, Any]],
    minimum: int = 1,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    ids: list[str] = []
    evidence_ids: set[str] = set()
    references: list[str] = []
    for index, result in enumerate(results, 1):
        prefix = f"result[{index}]"
        errors.extend(f"{prefix}: {error}" for error in validate_result_shape(result, schema_path=schema_path))
        if set(result) != set(FIELD_NAMES):
            errors.append(f"{prefix}: expected exactly 14 fields")
        sample_id = result.get("样例ID")
        ids.append(sample_id)
        if not isinstance(sample_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]+", sample_id):
            errors.append(f"{prefix}: invalid sample ID")
        for evidence in result.get("证据片段", []) if isinstance(result.get("证据片段"), list) else []:
            evidence_id = evidence.get("evidence_id")
            if evidence_id:
                evidence_ids.add(evidence_id)
        for field in EVIDENCE_REF_FIELDS:
            value = result.get(field)
            if field == "优化建议" and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        references.extend(item.get("evidence_ids", []))
            elif value != "-" and isinstance(value, dict):
                references.extend(value.get("evidence_ids", []))
        revision = result.get("人工修订")
        if revision != "-" and isinstance(revision, dict):
            for key in ("before", "after", "editor", "edited_at", "reason"):
                if key not in revision:
                    errors.append(f"{prefix}: revision missing {key}")
    duplicate_ids = sorted({sample_id for sample_id in ids if ids.count(sample_id) > 1})
    if duplicate_ids:
        errors.append(f"duplicate sample IDs: {duplicate_ids}")
    unresolved = sorted(set(references) - evidence_ids)
    if unresolved:
        errors.append(f"unresolved evidence references: {unresolved}")
    scenarios = {r.get("场景类型") for r in results if isinstance(r.get("场景类型"), str)}
    subscenarios = {
        r.get("输入数据", {}).get("subscene_type")
        for r in results
        if isinstance(r.get("输入数据"), dict)
    }
    chains = {
        {"短语音/录音转写": "C01", "会议/办公语音翻译": "C02", "车载/语音助手交互": "C03"}.get(s)
        for s in scenarios
    } - {None}
    if len(results) < minimum:
        errors.append(f"count {len(results)} < minimum {minimum}")
    return {
        "ok": not errors,
        "errors": errors,
        "count": len(results),
        "unique_ids": len(set(ids)),
        "typical_scenario_count": len(scenarios - {None}),
        "subscenario_count": len(subscenarios - {None}),
        "capability_chain_count": len(chains),
        "evidence_count": len(evidence_ids),
        "reference_count": len(references),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--minimum", type=int, default=1)
    parser.add_argument("--schema", type=Path, default=Path("schemas/evaluation-result.schema.json"))
    args = parser.parse_args()
    if args.input.suffix.lower() == ".csv":
        with args.input.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        result = {"ok": len(rows) >= args.minimum, "count": len(rows), "errors": []}
        if len(rows) < args.minimum:
            result["errors"].append(f"count {len(rows)} < minimum {args.minimum}")
    else:
        result = validate_results(load_results(args.input), minimum=args.minimum, schema_path=args.schema)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()

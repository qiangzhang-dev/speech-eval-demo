#!/usr/bin/env python3
"""Audit the exact manifest used by the one-take experiment."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


def _jsonish(value):
    if isinstance(value, (dict, list)):
        return value
    if value in (None, "", "-", "—"):
        return None
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return value


def _present(value) -> bool:
    return value not in (None, "", "-", "—") and value != {} and value != []


def _tasks(value: str) -> set[str]:
    return {
        item.strip()
        for item in str(value or "").replace("；", "|").replace("、", "|").replace(",", "|").split("|")
        if item.strip()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--expected-count", type=int, default=110)
    parser.add_argument("--expected-scenes", type=int, default=3)
    parser.add_argument("--expected-subscenes", type=int, default=6)
    parser.add_argument("--expected-capabilities", type=int, default=3)
    args = parser.parse_args()

    with args.manifest.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    sample_ids = [row.get("sample_id", "").strip() for row in rows]
    scenes = Counter(row.get("scene_type", "").strip() for row in rows)
    subscenes = Counter(row.get("subscene_type", "").strip() for row in rows)
    capabilities = Counter(row.get("capability_chain", "").strip() for row in rows)
    example = next((row for row in rows if row.get("sample_id") == "SYN-0001"), None)
    errors: list[str] = []
    unique_references: set[str] = set()
    stage_ready = Counter()
    semantic_errors: list[str] = []
    label_warnings: list[str] = []
    manifest_root = args.manifest.resolve().parent
    for row in rows:
        sample_id = row.get("sample_id", "unknown")
        reference = _jsonish(row.get("reference_annotation"))
        output = _jsonish(row.get("system_output"))
        audio = _jsonish(row.get("audio_info"))
        tasks = _tasks(row.get("task_types", ""))
        subscene = row.get("subscene_type", "")
        if not isinstance(reference, dict) or not isinstance(output, dict):
            semantic_errors.append(f"{sample_id}: reference_annotation/system_output must be JSON objects")
            continue
        transcript = reference.get("transcript")
        if _present(transcript):
            unique_references.add(str(transcript))
        input_path = row.get("input_path", "")
        if not input_path or not (manifest_root / input_path).is_file():
            semantic_errors.append(f"{sample_id}: input_path is missing or not readable")
        embedded_input = _jsonish(row.get("input_data"))
        source_record = None
        if input_path and (manifest_root / input_path).is_file():
            try:
                source_record = json.loads((manifest_root / input_path).read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, json.JSONDecodeError):
                source_record = None
        label_values = {
            "manifest": row.get("subscene_type", "").strip(),
            "input_metadata": embedded_input.get("subscene_type") if isinstance(embedded_input, dict) else None,
            "source_record": source_record.get("subscene_type") if isinstance(source_record, dict) else None,
        }
        present_labels = {name: str(value).strip() for name, value in label_values.items() if _present(value)}
        if len(set(present_labels.values())) > 1:
            label_warnings.append(
                f"{sample_id}: label metadata conflict "
                + ", ".join(f"{name}={value}" for name, value in sorted(present_labels.items()))
                + "; requires human confirmation"
            )
        if subscene == "噪声/数字英文混合":
            has_mixed = any(
                character.isdigit() or (character.isascii() and character.isalpha())
                for character in str(transcript or "")
            )
            signal_conditions = audio.get("signal_conditions", []) if isinstance(audio, dict) else []
            if not has_mixed or "noise" not in signal_conditions:
                semantic_errors.append(f"{sample_id}: noise/mixed sample lacks alphanumeric text or noise metadata")
        if subscene == "长句/上下文翻译":
            if len(str(transcript or "")) < 30 or not _present(reference.get("translation_context")):
                semantic_errors.append(f"{sample_id}: long/context translation is not long or lacks context")
        if "机器翻译" in tasks:
            if _present(reference.get("translation")) and _present(output.get("translation")):
                stage_ready["machine_translation"] += 1
            else:
                semantic_errors.append(f"{sample_id}: machine translation reference/output is incomplete")
        if "语义理解" in tasks:
            if all(_present(reference.get(key)) for key in ("intent", "slots")) and all(
                _present(output.get(key)) for key in ("intent", "slots")
            ):
                stage_ready["semantic_understanding"] += 1
            else:
                semantic_errors.append(f"{sample_id}: intent/slot reference or output is incomplete")
        if "对话生成" in tasks:
            if all(_present(reference.get(key)) for key in ("expected_action", "expected_reply")) and all(
                _present(output.get(key)) for key in ("action", "reply")
            ):
                stage_ready["dialogue_generation"] += 1
            else:
                semantic_errors.append(f"{sample_id}: action/reply reference or output is incomplete")
    checks = {
        "import_completed": bool(rows),
        "parse_completed": all(sample_ids),
        "count": len(rows),
        "unique_count": len(set(sample_ids)),
        "unique_reference_count": len(unique_references),
        "scene_count": len(scenes),
        "subscene_count": len(subscenes),
        "capability_count": len(capabilities),
    }
    expected = {
        "count": args.expected_count,
        "unique_count": args.expected_count,
        "unique_reference_count": args.expected_count,
        "scene_count": args.expected_scenes,
        "subscene_count": args.expected_subscenes,
        "capability_count": args.expected_capabilities,
    }
    for name, value in expected.items():
        if checks[name] != value:
            errors.append(f"{name}: expected {value}, got {checks[name]}")
    if not checks["import_completed"] or not checks["parse_completed"]:
        errors.append("manifest import/parse did not complete")
    if example is None:
        errors.append("SYN-0001 not found")
    errors.extend(semantic_errors)

    payload = {
        "ok": not errors,
        "stage_status": {
            "manifest_import": "COMPLETED" if checks["import_completed"] else "FAILED",
            "row_parse": "COMPLETED" if checks["parse_completed"] else "FAILED",
            "scene_labeling": "COMPLETED" if scenes else "FAILED",
            "task_labeling": "COMPLETED" if capabilities else "FAILED",
        },
        **checks,
        "scene_distribution": dict(sorted(scenes.items())),
        "subscene_distribution": dict(sorted(subscenes.items())),
        "capability_distribution": dict(sorted(capabilities.items())),
        "stage_ready_counts": dict(sorted(stage_ready.items())),
        "semantic_error_count": len(semantic_errors),
        "label_warning_count": len(label_warnings),
        "label_warnings": label_warnings,
        "label_consistency": "REVIEW_REQUIRED" if label_warnings else "CONSISTENT",
        "SYN-0001": {
            "sample_id": example.get("sample_id"),
            "scene_type": example.get("scene_type"),
            "subscene_type": example.get("subscene_type"),
            "capability_chain": example.get("capability_chain"),
            "task_types": example.get("task_types"),
            "reference_annotation": _jsonish(example.get("reference_annotation")),
            "system_output": _jsonish(example.get("system_output")),
            "cer": example.get("cer"),
        } if example else None,
        "errors": errors,
        "data_classification": "synthetic_engineering_validation",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

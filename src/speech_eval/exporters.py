"""Deterministic JSONL and CSV result exports."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from .models import EvaluationResult


def export_jsonl(results: Iterable[EvaluationResult], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(chinese_fields=True), ensure_ascii=False, sort_keys=False, separators=(",", ":")))
            handle.write("\n")
    return target


def _csv_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def export_csv(results: Iterable[EvaluationResult], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [result.to_dict(chinese_fields=True) for result in results]
    fieldnames = list(EvaluationResult.FIELD_MAP.values())
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, "-")) for key in fieldnames})
    return target


def export_repository(repository: Any, output_dir: str | Path, *, batch_id: str | None = None, run_id: str | None = None) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    results = repository.list_results(batch_id=batch_id, run_id=run_id)
    jsonl_path = export_jsonl(results, output / "evaluation-results.jsonl")
    csv_path = export_csv(results, output / "evaluation-results.csv")
    return {"jsonl": str(jsonl_path), "csv": str(csv_path), "count": len(results)}


export_results_jsonl = export_jsonl
export_results_csv = export_csv

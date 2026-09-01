"""Generate a small, archivable summary report from stored results.

The evaluator's JSONL/CSV exports are row-oriented.  This module provides a
separate report artifact for the assignment's ``报告生成`` step while keeping
the same evidence boundary as the rest of the offline demo: a report created
from the bundled fixtures is explicitly synthetic and never claims formal
business acceptance.
"""

from __future__ import annotations

import html
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import EvaluationResult


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _refs(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        raw = value.get("evidence_ids")
        if isinstance(raw, list):
            found.update(str(item) for item in raw)
        for child in value.values():
            found.update(_refs(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_refs(child))
    return found


def _metric_value(value: Any) -> float | None:
    if isinstance(value, dict):
        raw = value.get("value")
        return float(raw) if isinstance(raw, (int, float)) else None
    if isinstance(value, list):
        for item in value:
            result = _metric_value(item)
            if result is not None:
                return result
    return None


def _stage_metric_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    raw = value.get("stage_metrics")
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        return [
            {"stage": stage, **item}
            for stage, item in raw.items()
            if isinstance(item, dict)
        ]
    return []


def _label_correction_invalidation(result: EvaluationResult) -> dict[str, Any] | None:
    """Return the explicit invalidation record, when a label was corrected.

    A completed execution row is still useful for operational auditing, but
    its derived metric must not be presented as a current valid measurement
    until a new run has recomputed it under the corrected labels.
    """

    evidence = getattr(result, "evidence", None)
    if not isinstance(evidence, list):
        return None
    for item in evidence:
        if isinstance(item, dict) and item.get("type") == "label_correction_invalidation":
            return item
    return None


def _stage_mismatch_cases(results: list[EvaluationResult]) -> list[dict[str, Any]]:
    """Extract concrete downstream mismatches for the report and follow-up."""

    cases: list[dict[str, Any]] = []
    for result in results:
        if _label_correction_invalidation(result) is not None:
            continue
        for metric in _stage_metric_rows(result.quantitative_metrics):
            if metric.get("applicable") is not True or metric.get("matched") is not False:
                continue
            cases.append(
                {
                    "sample_id": result.sample_id,
                    "stage": str(metric.get("stage", "-")),
                    "metric_name": str(metric.get("metric_name", "-")),
                    "reference": metric.get("reference", "-"),
                    "hypothesis": metric.get("hypothesis", "-"),
                    "recommendation": "逐字段核对 reference/hypothesis，修正漏词或前缀差异后重跑该阶段。",
                }
            )
    return cases


def build_summary(
    repository: Any,
    *,
    batch_id: str | None = None,
    run_id: str | None = None,
    threshold_config: dict[str, Any] | None = None,
    data_classification: str = "synthetic_engineering_validation",
) -> dict[str, Any]:
    """Build a deterministic summary from the selected repository results.

    ``formal_acceptance_claim`` is deliberately fixed to ``False``.  Changing
    that value requires a separate, signed external acceptance record and is
    outside the scope of an offline report generator.
    """

    if not data_classification.strip():
        raise ValueError("data_classification must not be empty")
    results: list[EvaluationResult] = repository.list_results(batch_id=batch_id, run_id=run_id)
    batch_ids = sorted({result.batch_id for result in results if result.batch_id})
    run_ids = sorted({result.run_id for result in results if result.run_id})
    selected_batch = batch_id or (batch_ids[0] if len(batch_ids) == 1 else "-")
    selected_run = run_id or (run_ids[0] if len(run_ids) == 1 else "-")

    status_counts = Counter(result.status for result in results)
    scene_counts = Counter(result.scene_type for result in results)
    subscene_counts = Counter(result.subscene_type for result in results if result.subscene_type != "-")
    task_counts = Counter(task for result in results for task in result.task_types)
    conclusion_counts = Counter(
        str(result.final_conclusion.get("level", ""))
        for result in results
        if isinstance(result.final_conclusion, dict)
    )
    invalidated_results = [
        result for result in results if _label_correction_invalidation(result) is not None
    ]
    current_results = [result for result in results if _label_correction_invalidation(result) is None]
    metric_values = [
        value
        for result in current_results
        if (value := _metric_value(result.quantitative_metrics)) is not None
    ]
    stage_rows = [
        row
        for result in current_results
        for row in _stage_metric_rows(result.quantitative_metrics)
    ]
    stage_metric_counts = Counter(
        str(row.get("stage", "-")) for row in stage_rows
    )
    stage_match_counts = Counter(
        "matched"
        if row.get("matched") is True
        else ("mismatched" if row.get("matched") is False else "not_applicable")
        for row in stage_rows
    )
    evidence_ids: set[str] = set()
    reference_ids: set[str] = set()
    for result in results:
        if isinstance(result.evidence, list):
            evidence_ids.update(
                str(item.get("evidence_id"))
                for item in result.evidence
                if isinstance(item, dict) and item.get("evidence_id")
            )
        for field_name in ("quality_diagnosis", "impact_assessment", "recommendations", "final_conclusion"):
            reference_ids.update(_refs(getattr(result, field_name)))

    revisions = repository.list_revisions()
    if run_id:
        revisions = [item for item in revisions if item.run_id == run_id]
    elif batch_id:
        selected_ids = {result.sample_id for result in results}
        revisions = [item for item in revisions if item.sample_id in selected_ids]
    logs = repository.list_logs(run_id=run_id) if run_id else repository.list_logs()
    if batch_id and not run_id:
        logs = [entry for entry in logs if entry.get("batch_id") == batch_id]

    threshold = threshold_config or {}
    mismatch_cases = _stage_mismatch_cases(results)
    invalidated_sample_ids = sorted(result.sample_id for result in invalidated_results)
    metric_summary: dict[str, Any] = {
        "name": "CER",
        "count": len(metric_values),
        "execution_count": len(results),
        "current_metric_count": len(metric_values),
        "invalidated_result_count": len(invalidated_results),
        "pending_recompute_count": len(invalidated_results),
        "pending_recompute_sample_ids": invalidated_sample_ids,
        "min": min(metric_values) if metric_values else None,
        "max": max(metric_values) if metric_values else None,
        "mean": (sum(metric_values) / len(metric_values)) if metric_values else None,
    }
    return {
        "report_version": "1.0.0",
        "generated_at": _now(),
        "batch_id": selected_batch,
        "run_id": selected_run,
        "data_classification": data_classification,
        "formal_acceptance_claim": False,
        "counts": {
            "results": len(results),
            "unique_sample_ids": len({result.sample_id for result in results}),
            "completed": sum(1 for result in results if result.status == "COMPLETED"),
            "failed": sum(1 for result in results if result.status != "COMPLETED"),
            "current_valid_metrics": len(metric_values),
            "invalidated_results": len(invalidated_results),
            "pending_recompute": len(invalidated_results),
            "evidence": len(evidence_ids),
            "evidence_references": len(reference_ids),
            "revisions": len(revisions),
            "logs": len(logs),
        },
        "status_counts": dict(sorted(status_counts.items())),
        "scene_counts": dict(sorted(scene_counts.items())),
        "subscene_counts": dict(sorted(subscene_counts.items())),
        "task_type_counts": dict(sorted(task_counts.items())),
        "conclusion_counts": dict(sorted(conclusion_counts.items())),
        "metric_summary": metric_summary,
        "stage_metric_counts": dict(sorted(stage_metric_counts.items())),
        "stage_match_counts": dict(sorted(stage_match_counts.items())),
        "stage_mismatch_cases": mismatch_cases,
        "threshold": {
            "version": threshold.get("threshold_version", "-"),
            "approval_status": threshold.get("approval_status", "-"),
            "effective": bool(threshold.get("effective", False)),
        },
    }


def render_html(summary: dict[str, Any]) -> str:
    """Render a self-contained HTML report with escaped JSON details."""

    def block(title: str, value: Any) -> str:
        body = html.escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))
        return f"<h2>{html.escape(title)}</h2><pre>{body}</pre>"

    disclaimer = (
        "本报告由离线工程链路生成。它只描述当前选定数据集的汇总，"
        "不代表真实业务效果或正式验收结论。"
    )
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>语音评测汇总报告</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;line-height:1.5}"
        "pre{background:#f5f7fa;border-radius:.5rem;padding:1rem;overflow:auto}"
        ".notice{border-left:.35rem solid #c2410c;background:#fff7ed;padding:1rem;font-weight:600}"
        "dt{font-weight:700}dd{margin:0 0 .5rem 0}</style></head><body>"
        "<h1>多场景语音智能体验评测汇总报告</h1>"
        f"<p class=\"notice\">{html.escape(disclaimer)}<br>"
        f"formal_acceptance_claim={html.escape(str(summary.get('formal_acceptance_claim', False)).lower())}</p>"
        "<dl>"
        f"<dt>批次</dt><dd>{html.escape(str(summary.get('batch_id', '-')))}</dd>"
        f"<dt>运行</dt><dd>{html.escape(str(summary.get('run_id', '-')))}</dd>"
        f"<dt>生成时间</dt><dd>{html.escape(str(summary.get('generated_at', '-')))}</dd>"
        f"<dt>数据分类</dt><dd>{html.escape(str(summary.get('data_classification', '-')))}</dd>"
        "</dl>"
        + block("数量", summary.get("counts", {}))
        + block("状态分布", summary.get("status_counts", {}))
        + block("场景分布", summary.get("scene_counts", {}))
        + block("子场景分布", summary.get("subscene_counts", {}))
        + block("任务能力分布", summary.get("task_type_counts", {}))
        + block("结论分布", summary.get("conclusion_counts", {}))
        + block("指标摘要", summary.get("metric_summary", {}))
        + block("下游阶段指标", summary.get("stage_metric_counts", {}))
        + block("下游阶段匹配结果", summary.get("stage_match_counts", {}))
        + block("下游阶段错配案例与复核建议", summary.get("stage_mismatch_cases", []))
        + block("阈值配置", summary.get("threshold", {}))
        + "</body></html>\n"
    )


def write_report(
    repository: Any,
    output: str | Path,
    *,
    batch_id: str | None = None,
    run_id: str | None = None,
    threshold_config: dict[str, Any] | None = None,
    data_classification: str = "synthetic_engineering_validation",
) -> Path:
    """Write JSON or HTML based on the output suffix and return its path."""

    target = Path(output)
    suffix = target.suffix.casefold()
    if suffix not in {".json", ".html", ".htm"}:
        raise ValueError("report output must end in .json, .html, or .htm")
    summary = build_summary(
        repository,
        batch_id=batch_id,
        run_id=run_id,
        threshold_config=threshold_config,
        data_classification=data_classification,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".json":
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        target.write_text(render_html(summary), encoding="utf-8")
    return target


__all__ = ["build_summary", "render_html", "write_report"]

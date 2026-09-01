#!/usr/bin/env python3
"""Run one evidence-preserving diagnosis through the local Codex CLI.

The bridge accepts only an existing database, manifest, sample id, run id and
session directory. It updates AI-owned fields only after strict validation.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from speech_eval.manifest import load_manifest  # noqa: E402
from speech_eval.models import EvaluationResult  # noqa: E402
from speech_eval.providers.base import validate_diagnostic_response  # noqa: E402
from speech_eval.repository import EvaluationRepository  # noqa: E402
from speech_eval.batch import _public_input_data, _public_text_object  # noqa: E402

PROVIDER = "codex-cli"
MODEL = "gpt-5.6-sol"
PROMPT_VERSION = "codex-cli-gpt-5.6-sol-v1"
SCHEMA_PATH = PROJECT_ROOT / "config" / "prompts" / "codex-diagnosis-output.schema.json"
AI_FIELDS = ("quality_diagnosis", "evidence", "impact_assessment", "recommendations", "final_conclusion", "prompt_version")
_SECRET = re.compile(r"(?i)(api[_-]?key|authorization|bearer|access[_-]?token|secret)([\"' :=]+)([^\s\"']+)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_line(value: str) -> str:
    return _SECRET.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one real, evidence-constrained Codex AI diagnosis")
    parser.add_argument("--database", type=Path, required=True, help="existing SQLite database")
    parser.add_argument("--manifest", type=Path, required=True, help="manifest used by this run")
    parser.add_argument("--sample-id", required=True, help="existing sample to diagnose")
    parser.add_argument("--run-id", required=True, help="existing run containing the result")
    parser.add_argument("--session-dir", type=Path, required=True, help="directory for call artifacts")
    return parser


def _sample_payload(sample: Any) -> dict[str, Any]:
    return {"sample_id": sample.sample_id, "scene_type": sample.scene_type, "subscene_type": sample.subscene_type,
            "task_types": list(sample.task_types), "input_data": sample.input_data, "audio_info": sample.audio_info,
            "reference_annotation": sample.reference_annotation, "system_output": sample.system_output}


def _result_input_payload(result: EvaluationResult) -> dict[str, Any]:
    return {"sample_id": result.sample_id, "scene_type": result.scene_type, "subscene_type": result.subscene_type,
            "task_types": list(result.task_types), "input_data": result.input_data, "audio_info": result.audio_info,
            "reference_annotation": result.reference_annotation, "system_output": result.system_output}


def _normalized_sample_payload(sample: Any) -> dict[str, Any]:
    payload = _sample_payload(sample)
    payload["input_data"] = _public_input_data(sample)
    payload["reference_annotation"] = _public_text_object(sample.reference_annotation, "transcript")
    payload["system_output"] = _public_text_object(sample.system_output, "transcript")
    return payload


def _load_context(database: Path, manifest: Path, sample_id: str, run_id: str) -> tuple[Any, EvaluationResult, str]:
    if not database.is_file():
        raise ValueError(f"database does not exist: {database}")
    if not manifest.is_file():
        raise ValueError(f"manifest does not exist: {manifest}")
    matches = [sample for sample in load_manifest(manifest, check_paths=False) if sample.sample_id == sample_id]
    if len(matches) != 1:
        raise ValueError(f"manifest must contain sample exactly once: {sample_id}")
    with EvaluationRepository(database) as repository:
        row = repository.connection.execute("SELECT payload_json FROM results WHERE sample_id=? AND run_id=?", (sample_id, run_id)).fetchone()
        if row is None:
            raise ValueError(f"result not found for sample={sample_id}, run={run_id}")
        original_payload = str(row["payload_json"])
        result = EvaluationResult.from_dict(json.loads(original_payload))
    if result.sample_id != sample_id or result.run_id != run_id:
        raise ValueError("stored result identity does not match requested sample/run")
    if _normalized_sample_payload(matches[0]) != _result_input_payload(result):
        raise ValueError("manifest sample does not exactly match stored result inputs")
    if not isinstance(result.evidence, list) or not result.evidence:
        raise ValueError("stored result has no evidence catalog")
    return matches[0], result, original_payload


def _build_prompt(sample: Any, result: EvaluationResult) -> str:
    allowed = {"sample": _sample_payload(sample), "quantitative_metrics": result.quantitative_metrics, "evidence": result.evidence}
    return ("你是语音评测中的证据约束诊断器。\n"
            "只能使用 ALLOWED_INPUT_JSON 中的事实，不读取或引用其他文件、知识或假设。\n"
            "evidence 必须逐项、逐字段、顺序和值完全原样返回，不得新增、删除、改写或重排。 特别是必须保留 evidence.content 中的 overall_candidate_status 及所有嵌套键。\n"
            "所有诊断、影响、建议和结论都必须用 evidence_ids 引用输入中的证据；缺失事实写证据不足。\n"
            "当 quantitative_metrics.threshold_effective=false 或审批状态不是 approved/effective 时，final_conclusion.level 必须为证据不足；candidate_status 只能作为工程线索。\n"
            "只输出符合 JSON Schema 的单个 JSON 对象，不输出 Markdown 或推理过程。\n"
            f"prompt_version 必须为 {PROMPT_VERSION}。\n\nALLOWED_INPUT_JSON:\n" +
            json.dumps(allowed, ensure_ascii=False, sort_keys=True, indent=2))


def _codex_command(raw_output: Path) -> list[str]:
    return ["codex", "exec", "--ignore-rules", "-m", MODEL,
            "-c", 'model_reasoning_effort="medium"', "--ephemeral", "--sandbox", "read-only",
            "--skip-git-repo-check", "--output-schema", str(SCHEMA_PATH), "--output-last-message",
            str(raw_output), "-"]


def _run_codex(prompt: str, raw_output: Path, transcript_path: Path) -> int:
    process = subprocess.Popen(_codex_command(raw_output), cwd=PROJECT_ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", bufsize=1)
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise RuntimeError("Codex CLI pipes were not created")
    process.stdin.write(prompt)
    process.stdin.close()
    with transcript_path.open("w", encoding="utf-8", newline="") as transcript:
        for line in process.stdout:
            safe = _safe_line(line)
            print(safe, end="", flush=True)
            transcript.write(safe)
            transcript.flush()
    return process.wait()


def _audit_message(summary: dict[str, Any]) -> str:
    keys = ("provider", "model", "ai_call_started_at", "ai_call_finished_at", "duration_seconds", "status", "exit_code", "evidence_preserved", "raw_model_output")
    payload = {key: summary.get(key) for key in keys}
    if summary.get("error"):
        payload["error"] = summary["error"]
    return _json_text(payload)


def _record_failure_audit(database: Path, result: EvaluationResult | None, sample_id: str, run_id: str, summary: dict[str, Any]) -> None:
    if result is None or not database.is_file():
        return
    try:
        with EvaluationRepository(database) as repository:
            repository.append_log(level="ERROR", stage="ai_diagnosis_live", status="AI_FAILED", message=_audit_message(summary),
                                  batch_id=result.batch_id, sample_id=sample_id, run_id=run_id,
                                  error_code="CODEX_AI_DIAGNOSIS_FAILED", version=f"{PROVIDER}/{MODEL}")
    except Exception:
        pass


def _commit_success(database: Path, original_payload: str, updated: EvaluationResult, summary: dict[str, Any]) -> None:
    payload = _json_text(updated.to_dict(chinese_fields=False))
    with EvaluationRepository(database) as repository:
        connection = repository.connection
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute("UPDATE results SET status=?, payload_json=?, created_at=? WHERE run_id=? AND sample_id=? AND payload_json=?",
                                   (updated.status, payload, updated.created_at, updated.run_id, updated.sample_id, original_payload))
        if cursor.rowcount != 1:
            connection.rollback()
            raise RuntimeError("result changed during AI call; refusing to overwrite it")
        connection.execute("INSERT INTO logs(timestamp,level,batch_id,sample_id,run_id,stage,status,error_code,message,retry_count,version) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (_utc_now(), "INFO", updated.batch_id, updated.sample_id, updated.run_id, "ai_diagnosis_live", "COMPLETED", None,
                            _audit_message(summary), 0, f"{PROVIDER}/{MODEL}"))
        connection.commit()


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    database, manifest, session_dir = args.database.resolve(), args.manifest.resolve(), args.session_dir.resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    raw_output, transcript_path = session_dir / "codex-last-message.json", session_dir / "codex-cli-transcript.log"
    prompt_path, summary_path = session_dir / "codex-prompt.txt", session_dir / "codex-call-summary.json"
    result: EvaluationResult | None = None
    started_at, started_clock = _utc_now(), time.monotonic()
    summary: dict[str, Any] = {"ok": False, "provider": PROVIDER, "model": MODEL, "sample_id": args.sample_id, "run_id": args.run_id,
                               "session_dir": str(session_dir), "raw_model_output": str(raw_output), "transcript": str(transcript_path),
                               "ai_call_started_at": started_at, "ai_call_finished_at": None, "duration_seconds": None,
                               "exit_code": None, "status": "STARTING", "evidence_preserved": False, "updated_same_run": False}
    try:
        sample, result, original_payload = _load_context(database, manifest, args.sample_id, args.run_id)
        prompt_path.write_text(_build_prompt(sample, result), encoding="utf-8")
        print(_json_text({"event": "ai_call_start", "provider": PROVIDER, "model": MODEL, "sample_id": args.sample_id, "run_id": args.run_id,
                          "session_dir": str(session_dir), "ai_call_started_at": started_at}), flush=True)
        summary["status"] = "RUNNING"
        exit_code = _run_codex(prompt_path.read_text(encoding="utf-8"), raw_output, transcript_path)
        summary["exit_code"] = exit_code
        if exit_code != 0:
            raise RuntimeError(f"Codex CLI exited with status {exit_code}")
        if not raw_output.is_file():
            raise RuntimeError("Codex CLI did not write its final response")
        diagnostic = validate_diagnostic_response(raw_output.read_text(encoding="utf-8-sig"), default_prompt_version=PROMPT_VERSION)
        if diagnostic.prompt_version != PROMPT_VERSION:
            raise ValueError(f"unexpected prompt_version: {diagnostic.prompt_version!r}")
        metrics = result.quantitative_metrics if isinstance(result.quantitative_metrics, dict) else {}
        if metrics.get("threshold_effective") is False and diagnostic.final_conclusion.get("level") != "证据不足":
            raise ValueError("pending threshold requires final_conclusion.level=证据不足")
        if diagnostic.evidence != copy.deepcopy(result.evidence):
            raise ValueError("model evidence differs from supplied evidence catalog; refusing update")
        summary["evidence_preserved"] = True
        updated = replace(result, quality_diagnosis=diagnostic.quality_diagnosis, evidence=diagnostic.evidence,
                          impact_assessment=diagnostic.impact_assessment, recommendations=diagnostic.recommendations,
                          final_conclusion=diagnostic.final_conclusion, prompt_version=diagnostic.prompt_version)
        if updated.run_id != result.run_id or updated.batch_id != result.batch_id:
            raise RuntimeError("AI update changed run or batch identity")
        summary.update({"ok": True, "status": "COMPLETED", "updated_same_run": True, "ai_call_finished_at": _utc_now(),
                        "duration_seconds": round(time.monotonic() - started_clock, 3)})
        _commit_success(database, original_payload, updated, summary)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0, summary
    except Exception as exc:
        summary.update({"ok": False, "status": "AI_FAILED", "error": f"{type(exc).__name__}: {exc}", "ai_call_finished_at": _utc_now(),
                        "duration_seconds": round(time.monotonic() - started_clock, 3)})
        _record_failure_audit(database, result, args.sample_id, args.run_id, summary)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1, summary


def main(argv: list[str] | None = None) -> int:
    code, summary = run(_parser().parse_args(argv))
    print(_json_text(summary), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run one fixed, fresh speech-eval experiment and expose its live transcript.

The HTTP surface is intentionally local-only and does not accept shell commands.
It exists solely to make a real, auditable CLI run readable in one continuous
browser recording.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "data" / "generated_verify3"
MANIFEST = DATASET_DIR / "manifest.csv"
SCHEMA = ROOT / "schemas" / "evaluation-result.schema.json"
DATE_DIR = Path(os.environ.get("SPEECH_EVAL_EVIDENCE_DIR", ROOT / "var" / "evidence"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def command_text(argv: list[str]) -> str:
    def quote(part: str) -> str:
        return json.dumps(part, ensure_ascii=False) if any(ch.isspace() for ch in part) else part

    return "PYTHONPATH=src PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 " + " ".join(
        quote(part) for part in argv
    )

def last_json_object(text: str) -> dict[str, Any]:
    """Return the final one-line JSON object from a streamed command log."""

    for line in reversed(text.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("command output did not end with a JSON object")


@dataclass
class Step:
    number: int
    title: str
    command: str
    status: str = "pending"
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_seconds: float | None = None
    exit_code: int | str | None = None
    output: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "command": self.command,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": self.elapsed_seconds,
            "exit_code": self.exit_code,
            "output": "".join(self.output),
        }


class Experiment:
    def __init__(self, experiment_id: str, port: int, workbench_port: int, min_step_seconds: float) -> None:
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{7,80}", experiment_id):
            raise ValueError("experiment-id must contain only uppercase letters, digits, and hyphens")
        self.experiment_id = experiment_id
        self.batch_id = f"BATCH-{experiment_id}"
        self.run_id = f"RUN-{experiment_id}"
        self.revision_marker = f"REV-{experiment_id}"
        self.label_marker = f"LABEL-{experiment_id}"
        self.total_steps = 13
        self.port = port
        self.workbench_port = workbench_port
        self.min_step_seconds = min_step_seconds
        self.run_dir = DATE_DIR / experiment_id
        self.manifest = MANIFEST
        self.manifest_review_seed: dict[str, Any] | None = None
        self.database = self.run_dir / "live-110.db"
        self.export_dir = self.run_dir / "exports"
        self.report_json = self.run_dir / "evaluation-summary.json"
        self.report_html = self.run_dir / "evaluation-summary.html"
        self.workbench_stop_file = self.run_dir / "workbench.stop"
        self.changes_file = self.run_dir / "revision-SYN-0001.json"
        self.transcript_path = self.run_dir / "experiment-transcript.json"
        self.summary_path = self.run_dir / "experiment-summary.json"
        self.hash_path = self.run_dir / "artifact-sha256.json"
        self.phase = "ready"
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error: str | None = None
        self.steps: list[Step] = []
        self.current_step = 0
        self.acceptance: dict[str, Any] | None = None
        self.multistage_audit: dict[str, Any] | None = None
        self.ai_metadata: dict[str, Any] | None = None
        self.label_correction: dict[str, Any] | None = None
        self.workbench_ready = False
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._workbench: subprocess.Popen[str] | None = None
        self._continue_event = threading.Event()

    def start(self) -> bool:
        with self._lock:
            if self.phase != "ready":
                return False
            self.phase = "running"
            self.started_at = utc_now()
            self._thread = threading.Thread(target=self._run, name="real-experiment", daemon=True)
            self._thread.start()
            return True

    def continue_after_revision(self) -> bool:
        with self._lock:
            if self.phase != "awaiting_revision" or self._continue_event.is_set():
                return False
            self.phase = "running"
            self._continue_event.set()
            return True

    def stop(self) -> None:
        process = self._workbench
        if process is not None and process.poll() is None:
            self.workbench_stop_file.write_text("shutdown\n", encoding="utf-8")
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total = max(len(self.steps), self.total_steps)
            completed = sum(step.status == "completed" for step in self.steps)
            progress = 100 if self.phase == "complete" else round(100 * completed / total)
            return {
                "phase": self.phase,
                "experiment_id": self.experiment_id,
                "batch_id": self.batch_id,
                "run_id": self.run_id,
                "revision_marker": self.revision_marker,
                "label_marker": self.label_marker,
                "total_steps": self.total_steps,
                "database": str(self.database.relative_to(ROOT)),
                "run_dir": str(self.run_dir.relative_to(ROOT)),
                "manifest": str(self.manifest.relative_to(ROOT)),
                "manifest_review_seed": self.manifest_review_seed,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "current_step": self.current_step,
                "progress": progress,
                "steps": [step.to_dict() for step in self.steps],
                "workbench_ready": self.workbench_ready,
                "workbench_url": f"http://127.0.0.1:{self.workbench_port}/",
                "report_url": self.report_html.as_uri() if self.report_html.exists() else None,
                "acceptance": self.acceptance,
                "multistage_audit": self.multistage_audit,
                "ai_metadata": self.ai_metadata,
                "label_correction": self.label_correction,
                "artifacts_ready": self.hash_path.is_file(),
                "hash_manifest": str(self.hash_path.relative_to(ROOT)) if self.hash_path.is_file() else None,
                "error": self.error,
            }

    def _append_step(self, title: str, argv: list[str]) -> Step:
        with self._lock:
            step = Step(number=len(self.steps) + 1, title=title, command=command_text(argv))
            self.steps.append(step)
            return step

    def _run_command(self, title: str, argv: list[str], *, minimum_seconds: float | None = None) -> str:
        captured: list[str] = []
        step = self._append_step(title, argv)
        started = time.monotonic()
        with self._lock:
            self.current_step = step.number
            step.status = "running"
            step.started_at = utc_now()
            step.output.append("[ENV] PYTHONPATH=src; PYTHONIOENCODING=utf-8; PYTHONUNBUFFERED=1\n")

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            argv,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            captured.append(line)
            with self._lock:
                step.output.append(line)
        exit_code = process.wait()
        elapsed = time.monotonic() - started
        min_duration = self.min_step_seconds if minimum_seconds is None else minimum_seconds
        if elapsed < min_duration:
            time.sleep(min_duration - elapsed)
            elapsed = time.monotonic() - started
        with self._lock:
            step.exit_code = exit_code
            step.elapsed_seconds = round(elapsed, 3)
            step.finished_at = utc_now()
            step.status = "completed" if exit_code == 0 else "failed"
        if exit_code != 0:
            raise RuntimeError(f"{title} failed with exit code {exit_code}")
        return "".join(captured)

    def _start_workbench(self) -> None:
        argv = [
            sys.executable,
            str(ROOT / "scripts" / "serve_until_stop_file.py"),
            "--database",
            str(self.database),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.workbench_port),
            "--stop-file",
            str(self.workbench_stop_file),
        ]
        step = self._append_step("启动本次新数据库工作台", argv)
        started = time.monotonic()
        with self._lock:
            self.current_step = step.number
            step.status = "running"
            step.started_at = utc_now()
            step.output.append("启动本地服务并等待 /api/health；正常关闭由本实验专属 stop file 触发。\n")
        if self.workbench_stop_file.exists():
            raise RuntimeError(f"workbench stop file already exists: {self.workbench_stop_file}")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        self._workbench = subprocess.Popen(
            argv,
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self._workbench.poll() is not None:
                raise RuntimeError(f"workbench exited early: {self._workbench.returncode}")
            try:
                with urlopen(f"http://127.0.0.1:{self.workbench_port}/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("workbench did not become ready")
        elapsed = time.monotonic() - started
        if elapsed < self.min_step_seconds:
            time.sleep(self.min_step_seconds - elapsed)
            elapsed = time.monotonic() - started
        with self._lock:
            self.workbench_ready = True
            step.output.append(f"工作台已就绪：http://127.0.0.1:{self.workbench_port}/\n")
            step.exit_code = "RUNNING"
            step.elapsed_seconds = round(elapsed, 3)
            step.finished_at = utc_now()
            step.status = "completed"

    def _stop_workbench(self) -> None:
        """Normally stop the long-running workbench and audit its raw exit code."""

        process = self._workbench
        argv = ["workbench-service", "signal", "shutdown", "wait"]
        step = self._append_step("正常关闭本次工作台长驻服务", argv)
        started = time.monotonic()
        with self._lock:
            self.current_step = step.number
            step.status = "running"
            step.started_at = utc_now()
            step.output.append(
                "工作台是长驻本地服务；启动阶段显示 RUNNING 属于预期。\n"
                "验收完成后写入本实验专属停止信号，服务调用 shutdown 并等待原始进程退出。\n"
            )
        if process is None:
            with self._lock:
                step.exit_code = 1
                step.status = "failed"
                step.finished_at = utc_now()
                step.elapsed_seconds = round(time.monotonic() - started, 3)
                step.output.append("原始进程不存在，无法执行正常关闭。\n")
            raise RuntimeError("workbench process is not available for normal shutdown")
        raw_exit_code = process.poll()
        if raw_exit_code is None:
            self.workbench_stop_file.write_text("shutdown\n", encoding="utf-8")
            try:
                raw_exit_code = process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                process.kill()
                killed_exit_code = process.wait(timeout=5)
                with self._lock:
                    step.output.append(f"shutdown 等待超时；kill 后原始退出码={killed_exit_code}\n")
                    step.exit_code = 1
                    step.status = "failed"
                    step.finished_at = utc_now()
                    step.elapsed_seconds = round(time.monotonic() - started, 3)
                raise RuntimeError("workbench did not stop after shutdown signal") from exc
        if raw_exit_code != 0:
            with self._lock:
                step.output.append(f"工作台原始进程非正常退出：{raw_exit_code}\n")
                step.exit_code = raw_exit_code
                step.status = "failed"
                step.finished_at = utc_now()
                step.elapsed_seconds = round(time.monotonic() - started, 3)
            raise RuntimeError(f"workbench exited with nonzero status {raw_exit_code}")
        with self._lock:
            self.workbench_ready = False
            self._workbench = None
            step.output.append(f"工作台原始进程退出码={raw_exit_code}；shutdown/wait 已完成。\n")
            step.exit_code = 0
            step.status = "completed"
            step.finished_at = utc_now()
            step.elapsed_seconds = round(time.monotonic() - started, 3)

    def _write_metadata(self) -> None:
        snapshot = self.snapshot()
        if self.ai_metadata:
            raw_value = self.ai_metadata.get("raw_model_output")
            raw_path = Path(str(raw_value)).resolve() if raw_value else None
            if raw_path and raw_path.is_file() and raw_path.is_relative_to(self.run_dir.resolve()):
                self.ai_metadata = {
                    **self.ai_metadata,
                    "raw_model_output_sha256": sha256(raw_path),
                }
        transcript = {
            "schema_version": "real-experiment-transcript-v1",
            "recording_mode": "single_continuous_browser_session",
            "synthetic_data_notice": True,
            **snapshot,
        }
        self.transcript_path.write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary = {
            "experiment_id": self.experiment_id,
            "batch_id": self.batch_id,
            "run_id": self.run_id,
            "revision_marker": self.revision_marker,
            "label_marker": self.label_marker,
            "manifest_review_seed": self.manifest_review_seed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "manifest": str(self.manifest.relative_to(ROOT)),
            "database": str(self.database.relative_to(ROOT)),
            "exports": {
                "jsonl": str((self.export_dir / "evaluation-results.jsonl").relative_to(ROOT)),
                "csv": str((self.export_dir / "evaluation-results.csv").relative_to(ROOT)),
            },
            "reports": {
                "json": str(self.report_json.relative_to(ROOT)),
                "html": str(self.report_html.relative_to(ROOT)),
            },
            "acceptance": self.acceptance,
            "multistage_audit": self.multistage_audit,
            "ai_diagnosis": self.ai_metadata,
            "label_correction": self.label_correction,
            "formal_acceptance_claim": False,
            "data_classification": "synthetic_engineering_validation",
        }
        self.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifact_paths = [
            self.manifest,
            self.database,
            self.export_dir / "evaluation-results.jsonl",
            self.export_dir / "evaluation-results.csv",
            self.report_json,
            self.report_html,
            self.transcript_path,
            self.summary_path,
        ]
        ai_dir = self.run_dir / "ai-diagnosis"
        if ai_dir.is_dir():
            artifact_paths.extend(
                path for path in sorted(ai_dir.rglob("*")) if path.is_file()
            )
        hashes = {
            "algorithm": "SHA-256",
            "experiment_id": self.experiment_id,
            "files": {
                path.relative_to(ROOT).as_posix(): sha256(path)
                for path in artifact_paths
                if path.is_file()
            },
        }
        self.hash_path.write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding="utf-8")

    def _prepare_manifest(self) -> None:
        """Create an explicit label-review fixture without changing source data."""

        self.manifest = MANIFEST
        if not re.search(r"-M\d+$", self.experiment_id):
            return
        target = DATASET_DIR / f"m3-review-{self.experiment_id}.csv"
        with MANIFEST.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
        changed = False
        for row in rows:
            if row.get("sample_id") == "SYN-0002":
                # The source JSON contains noise/mixed-alphanumeric metadata.
                # Only the manifest label is deliberately seeded as a review
                # discrepancy for this demonstration run.
                row["subscene_type"] = "普通话口述"
                changed = True
                break
        if not changed:
            raise RuntimeError("label-review seed sample SYN-0002 was not found")
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        self.manifest = target
        self.manifest_review_seed = {
            "sample_id": "SYN-0002",
            "field": "subscene_type",
            "seeded_manifest_value": "普通话口述",
            "source_json_value": "噪声/数字英文混合",
            "source_json": "data/generated_verify3/inputs/SYN-0002.json",
            "purpose": "synthetic label-correction workflow validation",
        }

    def _run(self) -> None:
        try:
            # Only the empty browser video/download directories may exist here.
            # The SQLite file must not exist before the recorded click.
            self.run_dir.mkdir(parents=True, exist_ok=True)
            if self.database.exists():
                raise RuntimeError(f"fresh database check failed: {self.database} already exists")
            if not MANIFEST.is_file():
                raise RuntimeError(f"manifest not found: {MANIFEST}")
            self._prepare_manifest()
            if not self.manifest.is_file():
                raise RuntimeError(f"selected manifest not found: {self.manifest}")

            py = sys.executable
            self._run_command(
                "校验 110 条输入清单",
                [py, "-m", "speech_eval", "validate", str(self.manifest)],
            )
            audit_output = self._run_command(
                "审计导入、解析、场景/任务覆盖与 SYN-0001 原始字段",
                [
                    py,
                    "scripts/live_manifest_audit.py",
                    str(self.manifest),
                    "--expected-count",
                    "110",
                    "--expected-scenes",
                    "3",
                    "--expected-subscenes",
                    "6",
                    "--expected-capabilities",
                    "3",
                ],
            )
            audit = json.loads(audit_output)
            if audit.get("ok") is not True:
                raise RuntimeError("manifest coverage audit did not return ok=true")

            self._run_command(
                "执行 110 条全链路评测（导入→标注→解析→指标→规则诊断）",
                [
                    py,
                    "-m",
                    "speech_eval",
                    "run",
                    str(self.manifest),
                    "--database",
                    str(self.database),
                    "--batch-id",
                    self.batch_id,
                    "--run-id",
                    self.run_id,
                    "--provider",
                    "rules",
                ],
            )

            multistage_output = self._run_command(
                "审计 C01/C02/C03 代表样例的阶段字段、指标与证据",
                [
                    py,
                    "scripts/audit_multistage_results.py",
                    "--database",
                    str(self.database),
                    "--run-id",
                    self.run_id,
                ],
                minimum_seconds=max(2.6, self.min_step_seconds),
            )
            self.multistage_audit = json.loads(multistage_output)
            if self.multistage_audit.get("ok") is not True:
                raise RuntimeError("multi-stage representative audit did not return ok=true")

            ai_script = ROOT / "scripts" / "run_codex_ai_diagnosis.py"
            if not ai_script.is_file():
                raise RuntimeError("real AI bridge script is not present")
            ai_output = self._run_command(
                "真实 AI 诊断 SYN-0001（Codex CLI · gpt-5.6-sol）",
                [
                    py,
                    str(ai_script),
                    "--database",
                    str(self.database),
                    "--manifest",
                    str(self.manifest),
                    "--sample-id",
                    "SYN-0001",
                    "--run-id",
                    self.run_id,
                    "--session-dir",
                    str(self.run_dir / "ai-diagnosis"),
                ],
                minimum_seconds=max(4.0, self.min_step_seconds),
            )
            self.ai_metadata = last_json_object(ai_output)
            if self.ai_metadata.get("ok") is not True:
                raise RuntimeError("real AI diagnosis did not return ok=true")

            self._start_workbench()
            with self._lock:
                self.phase = "awaiting_revision"
            if not self._continue_event.wait(timeout=300):
                raise RuntimeError("timed out waiting for the recorded Web revision")

            revision_output = self._run_command(
                "核验 Web 修订已写入本次数据库",
                [
                    py,
                    "scripts/verify_live_revision.py",
                    "--database",
                    str(self.database),
                    "--run-id",
                    self.run_id,
                    "--sample-id",
                    "SYN-0001",
                    "--marker",
                    self.revision_marker,
                ],
            )
            revision_check = json.loads(revision_output)
            if revision_check.get("ok") is not True:
                raise RuntimeError("recorded Web revision did not pass verification")

            label_output = self._run_command(
                "核验 Web 标签纠正、派生结果失效与来源标签同步",
                [
                    py,
                    "scripts/verify_live_label_correction.py",
                    "--database",
                    str(self.database),
                    "--run-id",
                    self.run_id,
                    "--sample-id",
                    "SYN-0002",
                    "--marker",
                    self.label_marker,
                    "--expected-subscene",
                    "噪声/数字英文混合",
                ],
            )
            self.label_correction = json.loads(label_output)
            if self.label_correction.get("ok") is not True:
                raise RuntimeError("recorded Web label correction did not pass verification")

            self._run_command(
                "导出本次 JSONL 与 CSV",
                [
                    py,
                    "-m",
                    "speech_eval",
                    "export",
                    "--database",
                    str(self.database),
                    "--output",
                    str(self.export_dir),
                    "--batch-id",
                    self.batch_id,
                    "--run-id",
                    self.run_id,
                ],
            )
            self._run_command(
                "生成本次 JSON 汇总报告",
                [
                    py,
                    "scripts/generate_report.py",
                    "--database",
                    str(self.database),
                    "--output",
                    str(self.report_json),
                    "--batch-id",
                    self.batch_id,
                    "--run-id",
                    self.run_id,
                ],
            )
            self._run_command(
                "生成本次 HTML 可视报告",
                [
                    py,
                    "scripts/generate_report.py",
                    "--database",
                    str(self.database),
                    "--output",
                    str(self.report_html),
                    "--batch-id",
                    self.batch_id,
                    "--run-id",
                    self.run_id,
                ],
            )
            acceptance_output = self._run_command(
                "执行跨 SQLite / JSONL / CSV 验收",
                [
                    py,
                    "scripts/acceptance_check.py",
                    "--db",
                    str(self.database),
                    "--export",
                    str(self.export_dir / "evaluation-results.jsonl"),
                    "--export",
                    str(self.export_dir / "evaluation-results.csv"),
                    "--schema",
                    str(SCHEMA),
                    "--batch-id",
                    self.batch_id,
                    "--run-id",
                    self.run_id,
                ],
                minimum_seconds=max(3.2, self.min_step_seconds),
            )
            self.acceptance = json.loads(acceptance_output)
            if self.acceptance.get("ok") is not True:
                raise RuntimeError("acceptance report did not return ok=true")
            if re.search(r"-M\d+$", self.experiment_id) and int(self.acceptance.get("revision_count", 0)) < 2:
                raise RuntimeError("final iteration acceptance must contain both field and label correction revisions")
            self._stop_workbench()
            with self._lock:
                self.phase = "complete"
                self.finished_at = utc_now()
            self._write_metadata()
        except Exception as exc:  # noqa: BLE001 - persisted for the audit transcript
            with self._lock:
                self.phase = "failed"
                self.error = f"{type(exc).__name__}: {exc}"
                self.finished_at = utc_now()
            self.run_dir.mkdir(parents=True, exist_ok=True)
            failure = {
                "schema_version": "real-experiment-failure-v1",
                **self.snapshot(),
                "note": "Incomplete attempts intentionally do not receive an artifact hash manifest.",
            }
            (self.run_dir / "experiment-failure.json").write_text(
                json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8"
            )

HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>真实实验连续录制台</title>
<style>
:root{color-scheme:dark;--bg:#07111f;--panel:#0c1a2d;--line:#1f3651;--ink:#edf6ff;--muted:#95acc6;--cyan:#55d7ff;--green:#57e39b;--yellow:#ffd166;--red:#ff7b86}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#163657 0,transparent 35%),var(--bg);color:var(--ink);font:16px/1.55 "Microsoft YaHei",sans-serif}
main{width:min(1360px,calc(100% - 40px));margin:22px auto}.top{display:grid;grid-template-columns:1.5fr 1fr;gap:18px}.card{background:rgba(12,26,45,.94);border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 20px 60px #0005}
.eyebrow{margin:0 0 4px;color:var(--cyan);font-weight:800;letter-spacing:.12em}.title{margin:0;font-size:32px}.copy{color:var(--muted);margin:8px 0 0}.notice{border-left:4px solid var(--yellow);padding-left:12px;color:#ffeca8}
.ids{display:grid;grid-template-columns:100px 1fr;gap:8px 12px;margin:0}.ids dt{color:var(--muted)}.ids dd{margin:0;font:700 14px/1.45 Consolas,monospace;word-break:break-all}
.progress-shell{margin:18px 0 14px;height:14px;background:#07101d;border:1px solid var(--line);border-radius:99px;overflow:hidden}.progress{height:100%;width:0;background:linear-gradient(90deg,#38bdf8,#4ade80);transition:width .25s}
.actions{display:flex;align-items:center;gap:14px}.button{border:0;border-radius:9px;padding:11px 18px;background:var(--cyan);color:#03111c;font-weight:900;cursor:pointer}.button:disabled{opacity:.4;cursor:not-allowed}.phase{font-weight:800;color:var(--yellow)}
.layout{display:grid;grid-template-columns:320px 1fr;gap:18px;margin-top:18px}.steps{height:548px;overflow:auto}.step{padding:10px 12px;margin-bottom:8px;border:1px solid var(--line);border-radius:10px;color:var(--muted)}.step.running{border-color:var(--yellow);color:var(--yellow)}.step.completed{border-color:#23704d;color:var(--green)}.step.failed{border-color:#873642;color:var(--red)}.step strong{display:block;color:inherit}.step small{display:block;margin-top:3px;color:#8297af}
.terminal{height:548px;display:flex;flex-direction:column}.terminal-head{display:flex;justify-content:space-between;align-items:center;padding-bottom:12px;border-bottom:1px solid var(--line)}.terminal-head strong{font:700 15px Consolas,monospace;color:var(--green)}
pre{flex:1;overflow:auto;margin:12px 0 0;padding:16px;border-radius:10px;background:#02070e;color:#d8e8f8;font:14px/1.48 Consolas,"Microsoft YaHei",monospace;white-space:pre-wrap;word-break:break-word}.pass{display:none;margin-top:16px;border:1px solid #287f59;background:#0b3c2a;color:#b8ffda}.pass h2{margin:0 0 8px}.pass strong{font-size:23px;color:var(--green)}
</style></head><body><main>
 <section class="top"><article class="card"><p class="eyebrow">ONE-TAKE · LIVE EXPERIMENT</p><h1 class="title">语音评测真实执行 · 连续录制</h1><p class="copy">从空数据库开始，现场执行校验、110 条评测、C01/C02/C03 阶段审计、Web 主观修订、独立标签纠正、导出、报告与跨格式验收。</p><p class="notice">数据为合成工程验证样例；本次演示额外展示一个来源标签与清单标签不一致的待纠正样例。本页证明操作与产物真实执行，不声称真实业务效果或正式验收。</p></article><article class="card"><dl class="ids"><dt>实验 ID</dt><dd id="experiment-id">-</dd><dt>批次 ID</dt><dd id="batch-id">-</dd><dt>运行 ID</dt><dd id="run-id">-</dd><dt>新数据库</dt><dd id="database">-</dd></dl></article></section>
 <section class="card" style="margin-top:18px"><div class="actions"><button id="start" class="button">开始真实实验</button><button id="continue" class="button" hidden>确认修订并继续导出/验收</button><span id="phase" class="phase">执行前：等待开始，新数据库尚不存在</span></div><div class="progress-shell"><div id="progress" class="progress"></div></div><div id="counter" class="copy">0 / 13 个固定步骤</div><p class="copy">诊断后端：整批为可复现 rules；典型样例 SYN-0001 另经真实 Codex CLI / gpt-5.6-sol 诊断。数据性质：合成工程验证。</p><p class="copy">工作台是本地长驻服务：启动步骤显示 RUNNING 属于预期；下载和验收完成后，第 13 步会写入专属停止信号并 shutdown/wait，原始进程必须以 0 退出。</p><p class="copy">评测命令内部按样例真实执行：清单导入 → 场景/任务标注 → 输出解析 → CER 与下游阶段指标 → 证据约束诊断 → SQLite 持久化。</p></section>
<section class="layout"><aside id="steps" class="card steps"></aside><article class="card terminal"><div class="terminal-head"><strong>LIVE COMMAND TRANSCRIPT</strong><span id="clock" class="copy"></span></div><pre id="terminal">[READY] 已锁定本次唯一实验标识。\n[READY] 点击“开始真实实验”后将真实创建新数据库并执行固定命令链。\n</pre></article></section>
 <section id="pass" class="card pass"><h2>本次实验已完整执行</h2><strong id="acceptance">ACCEPTANCE: ok=true</strong><p id="acceptance-detail"></p><p>工作台将展示 C01/C02/C03 三个代表样例的阶段字段、阶段指标和证据闭环，以及本次 Web 主观修订、独立标签纠正、实际下载和 HTML 报告。</p></section>
</main><script>
const $=s=>document.querySelector(s);let last="";
function esc(s){return String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}
function render(data){
  $("#experiment-id").textContent=data.experiment_id;$("#batch-id").textContent=data.batch_id;$("#run-id").textContent=data.run_id;$("#database").textContent=data.database;$("#progress").style.width=data.progress+"%";
  const done=data.steps.filter(x=>x.status==="completed").length;$("#counter").textContent=`${done} / ${data.total_steps} 个固定步骤 · ${data.progress}%`;
  const labels={ready:"执行前：等待开始，新数据库尚不存在",running:`正在执行第 ${data.current_step} 步`,awaiting_revision:`等待 Web 修订操作：${data.revision_marker}`,complete:"全部命令完成，验收通过，工作台已正常关闭，产物哈希已生成",failed:"实验失败"};$("#phase").textContent=labels[data.phase]||data.phase;$("#start").disabled=data.phase!=="ready";$("#continue").hidden=data.phase!=="awaiting_revision";document.body.dataset.status=data.phase;
  $("#steps").innerHTML=data.steps.map(s=>`<div class="step ${s.status}"><strong>${s.number}. ${esc(s.title)}</strong><small>${esc(s.command)}</small><small>状态 ${s.status} · 退出码 ${s.exit_code??"-"} · ${s.elapsed_seconds??"-"}s</small></div>`).join("")||'<div class="step">固定步骤将在这里逐项出现</div>';
  let text="";for(const s of data.steps){text+=`\n[STEP ${s.number}/${data.total_steps}] ${s.title}\n$ ${s.command}\n${s.output||""}`;if(s.exit_code!==null)text+=`[EXIT] ${s.exit_code} · ${s.status} · ${s.elapsed_seconds}s\n`;}
  if(data.error)text+=`\n[ERROR] ${data.error}\n`;if(text!==last){const t=$("#terminal");t.textContent=text||t.textContent;t.scrollTop=t.scrollHeight;last=text;}
  if(data.phase==="complete"){const a=data.acceptance||{};const m=data.multistage_audit||{};$("#pass").style.display="block";$("#acceptance-detail").textContent=`${a.count} 条执行记录 · 当前有效指标 ${a.current_valid_metric_count??"-"} · 待重算 ${a.pending_recompute_count??0} · COMPLETED ${a.status_counts?.COMPLETED||0} · ${a.scenario_count} 场景 · ${a.subscenario_count} 子场景 · ${a.capability_count} 能力 · ${a.evidence_count} 证据 · 阶段指标 ${m.stage_metric_count||0} · 阶段证据 ${m.stage_evidence_count||0} · ${a.revision_count} 条修订 · ${data.batch_id} / ${data.run_id} · 哈希清单 ${data.hash_manifest||"正在生成"}`;$("#pass").scrollIntoView({behavior:"smooth",block:"center"});}
  if(data.phase==="failed")document.body.dataset.status="failed";
}
async function poll(){try{const r=await fetch('/status',{cache:'no-store'});render(await r.json())}catch(e){}setTimeout(poll,250)}
$("#start").addEventListener("click",async()=>{await fetch('/start',{method:'POST'});});
$("#continue").addEventListener("click",async()=>{await fetch('/continue',{method:'POST'});});
setInterval(()=>$("#clock").textContent=new Date().toLocaleString('zh-CN',{hour12:false}),1000);poll();
</script></body></html>"""


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], experiment: Experiment) -> None:
        self.experiment = experiment
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            self._send(HTTPStatus.OK, HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/status":
            body = json.dumps(self.server.experiment.snapshot(), ensure_ascii=False).encode("utf-8")
            self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
        elif self.path == "/health":
            self._send(HTTPStatus.OK, b'{"status":"ok"}', "application/json")
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/start":
            started = self.server.experiment.start()
            payload = json.dumps({"started": started}).encode("utf-8")
            self._send(HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT, payload, "application/json")
        elif self.path == "/continue":
            continued = self.server.experiment.continue_after_revision()
            payload = json.dumps({"continued": continued}).encode("utf-8")
            self._send(HTTPStatus.ACCEPTED if continued else HTTPStatus.CONFLICT, payload, "application/json")
        elif self.path == "/shutdown":
            self.server.experiment.stop()
            self._send(HTTPStatus.OK, b'{"stopped":true}', "application/json")
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18766)
    parser.add_argument("--workbench-port", type=int, default=18767)
    parser.add_argument("--min-step-seconds", type=float, default=2.4)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        raise SystemExit("live experiment server may bind only to 127.0.0.1")
    experiment = Experiment(args.experiment_id, args.port, args.workbench_port, args.min_step_seconds)
    server = Server((args.host, args.port), experiment)
    print(
        json.dumps(
            {
                "ready": True,
                "url": f"http://{args.host}:{args.port}/",
                "experiment_id": experiment.experiment_id,
                "run_dir": str(experiment.run_dir),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        experiment.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

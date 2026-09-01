#!/usr/bin/env python3
"""Capture reproducible local engineering-validation evidence.

This helper runs only the repository's offline tests and acceptance checker,
then stores their exact outputs, exit codes, environment metadata, and SHA-256
hashes of the inputs/outputs.  It never changes the validation data and never
sets a formal-acceptance claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def project_path(root: Path, value: Path, *, label: str) -> Path:
    """Resolve a path and require it to stay inside the project root."""
    resolved = value.resolve() if value.is_absolute() else (root / value).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must be inside project root: {value}") from exc
    return resolved


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> dict[str, object]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "argv": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def freeze_reproducibility_inputs(
    root: Path, output: Path, *, manifest: Path
) -> list[dict[str, str]]:
    """Copy the final source and run inputs next to the validation evidence."""

    snapshot = output / "frozen-inputs"
    # Rebuild from a clean directory so removed or renamed source files cannot survive as stale snapshots.
    if snapshot.exists():
        shutil.rmtree(snapshot)
    directory_inputs = [
        Path("src"),
        Path("tests"),
        Path("web"),
        Path("scripts"),
        Path("config"),
        Path("schemas"),
        Path("data/generated_verify3/inputs"),
    ]
    file_inputs = [manifest, Path("pyproject.toml")]
    for relative in directory_inputs:
        source = project_path(root, relative, label="freeze input")
        if not source.is_dir():
            raise FileNotFoundError(f"freeze input directory not found: {relative}")
        shutil.copytree(
            source,
            snapshot / relative,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    for relative in file_inputs:
        source = project_path(root, relative, label="freeze input")
        if not source.is_file():
            raise FileNotFoundError(f"freeze input file not found: {relative}")
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    rows: list[dict[str, str]] = []
    for path in sorted(item for item in snapshot.rglob("*") if item.is_file()):
        rows.append(
            {
                "source_path": str(path.relative_to(snapshot)),
                "snapshot_path": str(path.relative_to(output)),
                "sha256": sha256(path),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("var/validation"),
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("var/evaluation.db"),
    )
    parser.add_argument(
        "--jsonl",
        type=Path,
        default=Path("exports/evaluation-results.jsonl"),
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("exports/evaluation-results.csv"),
    )
    parser.add_argument("--schema", type=Path, default=Path("schemas/evaluation-result.schema.json"))
    parser.add_argument("--manifest", type=Path, default=Path("data/generated_verify3/manifest.csv"))
    parser.add_argument("--thresholds", type=Path, default=Path("config/thresholds.json"))
    parser.add_argument("--batch-id", default="BATCH-SYNTHETIC")
    parser.add_argument("--run-id", default="RUN-SYNTHETIC")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = project_path(root, args.output, label="--output")
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    tests = run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=root,
        env=env,
    )
    acceptance = run(
        [
            sys.executable,
            "scripts/acceptance_check.py",
            "--db",
            str(project_path(root, args.db, label="--db").relative_to(root)),
            "--export",
            str(project_path(root, args.jsonl, label="--jsonl").relative_to(root)),
            "--export",
            str(project_path(root, args.csv, label="--csv").relative_to(root)),
            "--schema",
            str(project_path(root, args.schema, label="--schema").relative_to(root)),
            "--batch-id",
            args.batch_id,
            "--run-id",
            args.run_id,
        ],
        cwd=root,
        env=env,
    )
    acceptance_summary: dict[str, object] | None = None
    if acceptance["returncode"] == 0:
        try:
            parsed = json.loads(str(acceptance["stdout"]))
        except json.JSONDecodeError:
            acceptance["returncode"] = 1
            acceptance["stderr"] = str(acceptance["stderr"]) + "\nacceptance output was not valid JSON"
        else:
            if (
                not isinstance(parsed, dict)
                or parsed.get("ok") is not True
                or parsed.get("data_classification") != "synthetic_engineering_validation"
                or parsed.get("formal_acceptance_claim") is not False
            ):
                acceptance["returncode"] = 1
                acceptance["stderr"] = str(acceptance["stderr"]) + "\nacceptance output failed synthetic/non-formal boundary checks"
            else:
                acceptance_summary = parsed
    test_summary: dict[str, object] = {}
    test_output = str(tests["stdout"]) + "\n" + str(tests["stderr"])
    match = re.search(r"Ran (\d+) tests? in ([0-9.]+)s", test_output)
    if match:
        test_summary = {"count": int(match.group(1)), "duration_seconds": float(match.group(2))}
    test_summary["passed"] = tests["returncode"] == 0 and "OK" in test_output
    captured_at = datetime.now(timezone.utc).isoformat()
    evidence = {
        "captured_at_utc": captured_at,
        "project": "speech-eval-demo",
        "data_classification": "synthetic_engineering_validation",
        "formal_acceptance_claim": False,
        "batch_id": args.batch_id,
        "run_id": args.run_id,
        "environment": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "tests": tests,
        "test_summary": test_summary,
        "acceptance": acceptance,
        "acceptance_summary": acceptance_summary,
    }
    (output / "validation-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    files = [args.db, args.jsonl, args.csv, args.schema, args.manifest, args.thresholds]
    hash_rows: list[dict[str, str]] = []
    hash_errors: list[str] = []
    for candidate in files:
        path = project_path(root, candidate, label="input")
        if path.is_file():
            hash_rows.append({"path": str(path.relative_to(root)), "sha256": sha256(path)})
        else:
            hash_errors.append(str(path.relative_to(root)))
    (output / "SHA256SUMS.json").write_text(
        json.dumps({"captured_at_utc": captured_at, "files": hash_rows, "missing": hash_errors}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    commands = {"tests": tests["argv"], "acceptance": acceptance["argv"]}
    (output / "validation-commands.json").write_text(
        json.dumps(commands, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    frozen_rows = freeze_reproducibility_inputs(root, output, manifest=args.manifest)
    (output / "FROZEN_INPUTS_SHA256.json").write_text(
        json.dumps(
            {
                "captured_at_utc": captured_at,
                "files": frozen_rows,
                "note": "Final source/input snapshot validated against the frozen experiment artifacts.",
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    (output / "README.md").write_text(
        "# 本地工程验证证据\n\n"
        "本目录由 `scripts/capture_validation_evidence.py` 生成，记录离线测试、验收器原始输出和输入产物哈希。\n\n"
        "这些证据只证明合成工程链路可复现；`formal_acceptance_claim` 固定为 `false`，不替代真实数据、人工抽检、阈值审批、在线权限检查或签字。\n\n"
        "- `validation-evidence.json`：命令、退出码、标准输出和环境信息。\n"
        "- `SHA256SUMS.json`：数据库、JSONL、CSV、Schema、manifest 和阈值配置的 SHA-256。\n"
        "- `validation-commands.json`：本次测试与验收命令。\n"
        f"- `frozen-inputs/`：最终源码、测试、Web、脚本、配置、Schema、实际运行 manifest（{args.manifest}）与 110 个输入 JSON 副本。\n"
        "- `FROZEN_INPUTS_SHA256.json`：上述冻结副本的逐文件 SHA-256。\n",
        encoding="utf-8",
    )
    return 0 if tests["returncode"] == 0 and acceptance["returncode"] == 0 and not hash_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

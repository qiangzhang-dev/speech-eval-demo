from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "speech_eval", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


class ExtendedCliTests(unittest.TestCase):
    def test_validate_is_summary_by_default_and_verbose_on_request(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "data"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "generate_samples.py"),
                    "--count",
                    "2",
                    "--output",
                    str(data),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            summary = run_cli("validate", str(data / "manifest.csv"))
            self.assertEqual(summary.returncode, 0, summary.stderr)
            payload = json.loads(summary.stdout)
            self.assertEqual(payload["count"], 2)
            self.assertNotIn("samples", payload)
            verbose = run_cli("validate", str(data / "manifest.csv"), "--verbose")
            self.assertEqual(verbose.returncode, 0, verbose.stderr)
            self.assertEqual(len(json.loads(verbose.stdout)["samples"]), 2)

    def test_run_records_threshold_version_and_query_revision_work(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            data = work / "data"
            db = work / "evaluation.db"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "generate_samples.py"),
                    "--count",
                    "1",
                    "--output",
                    str(data),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            run = run_cli(
                "run",
                str(data / "manifest.csv"),
                "--database",
                str(db),
                "--batch-id",
                "CLI-BATCH",
                "--run-id",
                "CLI-RUN",
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            run_payload = json.loads(run.stdout)
            self.assertEqual(run_payload["threshold_version"], "1.0.0-candidate.1")
            self.assertFalse(run_payload["threshold_effective"])
            query = run_cli("query", "--database", str(db), "--run-id", "CLI-RUN", "--limit", "1")
            self.assertEqual(query.returncode, 0, query.stderr)
            self.assertEqual(json.loads(query.stdout)["count"], 1)
            evidence_id = json.loads(query.stdout)["items"][0]["证据片段"][0]["evidence_id"]
            changes = json.dumps(
                {"质量诊断": {"text": "人工CLI复核", "evidence_ids": [evidence_id]}},
                ensure_ascii=False,
            )
            revise = run_cli(
                "revise",
                "--database",
                str(db),
                "--sample-id",
                "SYN-0001",
                "--run-id",
                "CLI-RUN",
                "--editor",
                "tester",
                "--reason",
                "CLI audit test",
                "--changes",
                changes,
            )
            self.assertEqual(revise.returncode, 0, revise.stderr)
            self.assertEqual(json.loads(revise.stdout)["revision_count"], 1)

    def test_revise_rejects_source_fact_without_changing_result(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            data = work / "data"
            db = work / "evaluation.db"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "generate_samples.py"),
                    "--count",
                    "1",
                    "--output",
                    str(data),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            run = run_cli(
                "run",
                str(data / "manifest.csv"),
                "--database",
                str(db),
                "--batch-id",
                "CLI-GUARD-BATCH",
                "--run-id",
                "CLI-GUARD-RUN",
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            before = run_cli(
                "query",
                "--database",
                str(db),
                "--run-id",
                "CLI-GUARD-RUN",
                "--limit",
                "1",
            )
            self.assertEqual(before.returncode, 0, before.stderr)
            before_item = json.loads(before.stdout)["items"][0]

            revise = run_cli(
                "revise",
                "--database",
                str(db),
                "--sample-id",
                "SYN-0001",
                "--run-id",
                "CLI-GUARD-RUN",
                "--editor",
                "cli-reviewer",
                "--reason",
                "attempt source-fact rewrite",
                "--changes",
                json.dumps({"系统输出": {"transcript": "篡改输出"}}, ensure_ascii=False),
            )
            self.assertNotEqual(revise.returncode, 0)
            self.assertIn("field is not editable", revise.stderr)

            after = run_cli(
                "query",
                "--database",
                str(db),
                "--run-id",
                "CLI-GUARD-RUN",
                "--limit",
                "1",
            )
            self.assertEqual(after.returncode, 0, after.stderr)
            after_item = json.loads(after.stdout)["items"][0]
            self.assertEqual(after_item["系统输出"], before_item["系统输出"])
            self.assertEqual(after_item["人工修订"], "-")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReportGenerationTests(unittest.TestCase):
    def test_json_and_html_reports_are_archivable_and_non_formal(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            data = work / "data"
            database = work / "evaluation.db"
            json_report = work / "report.json"
            html_report = work / "report.html"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "generate_samples.py"), "--count", "4", "--output", str(data)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            env["PYTHONIOENCODING"] = "utf-8"
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "speech_eval",
                    "run",
                    str(data / "manifest.csv"),
                    "--database",
                    str(database),
                    "--batch-id",
                    "REPORT-BATCH",
                    "--run-id",
                    "REPORT-RUN",
                ],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            for output in (json_report, html_report):
                generated = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "scripts" / "generate_report.py"),
                        "--database",
                        str(database),
                        "--run-id",
                        "REPORT-RUN",
                        "--output",
                        str(output),
                    ],
                    cwd=ROOT,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                self.assertEqual(generated.returncode, 0, generated.stderr)
                self.assertTrue(output.is_file())
            payload = json.loads(json_report.read_text(encoding="utf-8"))
            self.assertEqual(payload["counts"]["results"], 4)
            self.assertEqual(payload["status_counts"], {"COMPLETED": 4})
            self.assertFalse(payload["formal_acceptance_claim"])
            html = html_report.read_text(encoding="utf-8")
            self.assertIn("生成时间", html)
            self.assertRegex(html, r"<dt>生成时间</dt><dd>\d{4}-\d{2}-\d{2}T")
            self.assertIn("synthetic_engineering_validation", html)
            self.assertIn("formal_acceptance_claim=false", html)

    def test_summary_excludes_label_invalidated_metric_from_current_count(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            data = work / "data"
            database = work / "evaluation.db"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "generate_samples.py"), "--count", "4", "--output", str(data)],
                cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
            )
            from speech_eval.batch import BatchProcessor
            from speech_eval.manifest import load_manifest
            from speech_eval.repository import EvaluationRepository
            from speech_eval.revisions import apply_label_correction
            from speech_eval.reporting import build_summary

            with EvaluationRepository(database) as repository:
                run = BatchProcessor(repository).run(
                    load_manifest(data / "manifest.csv"),
                    batch_id="SUMMARY-BATCH", run_id="SUMMARY-RUN",
                )
                result = repository.get_result("SYN-0001", run_id=run.run_id)
                self.assertIsNotNone(result)
                apply_label_correction(
                    result,
                    {"subscene_type": "噪声/数字英文混合"},
                    editor="test-label-reviewer",
                    reason="test invalidation accounting",
                    repository=repository,
                )
                summary = build_summary(repository, run_id=run.run_id)

            self.assertEqual(summary["counts"]["results"], 4)
            self.assertEqual(summary["counts"]["completed"], 4)
            self.assertEqual(summary["counts"]["current_valid_metrics"], 3)
            self.assertEqual(summary["counts"]["pending_recompute"], 1)
            self.assertEqual(summary["metric_summary"]["current_metric_count"], 3)
            self.assertEqual(summary["metric_summary"]["pending_recompute_sample_ids"], ["SYN-0001"])


if __name__ == "__main__":
    unittest.main()

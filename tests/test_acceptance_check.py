from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from speech_eval.batch import BatchProcessor
from speech_eval.manifest import load_manifest
from speech_eval.repository import EvaluationRepository
from speech_eval.revisions import apply_human_revision

ROOT = Path(__file__).resolve().parents[1]


class AcceptanceCheckTests(unittest.TestCase):
    def test_sqlite_jsonl_csv_consistency_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            data = work / "data"
            database = work / "evaluation.db"
            output = work / "exports"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "generate_samples.py"), "--count", "6", "--output", str(data)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            samples = load_manifest(data / "manifest.csv")
            with EvaluationRepository(database) as repository:
                summary = BatchProcessor(repository).run(
                    samples,
                    batch_id="ACCEPT-BATCH",
                    run_id="ACCEPT-RUN",
                )
                first = repository.get_result("SYN-0001", run_id=summary.run_id)
                diagnosis = dict(first.quality_diagnosis)
                diagnosis["text"] = "人工验收复核"
                apply_human_revision(
                    first,
                    {"quality_diagnosis": diagnosis},
                    editor="acceptance-tester",
                    reason="verify revision persistence",
                    repository=repository,
                )
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src")
            export = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "speech_eval",
                    "export",
                    "--database",
                    str(database),
                    "--output",
                    str(output),
                    "--batch-id",
                    "ACCEPT-BATCH",
                    "--run-id",
                    "ACCEPT-RUN",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(export.returncode, 0, export.stderr)
            check = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "acceptance_check.py"),
                    "--db",
                    str(database),
                    "--export",
                    str(output / "evaluation-results.jsonl"),
                    "--export",
                    str(output / "evaluation-results.csv"),
                    "--schema",
                    str(ROOT / "schemas" / "evaluation-result.schema.json"),
                    "--batch-id",
                    "ACCEPT-BATCH",
                    "--run-id",
                    "ACCEPT-RUN",
                    "--min-count",
                    "6",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(check.returncode, 0, check.stderr + check.stdout)
            payload = json.loads(check.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["count"], 6)
            self.assertEqual(payload["revision_count"], 1)

    def test_checker_detects_changed_csv_value(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            data = work / "data"
            database = work / "evaluation.db"
            output = work / "exports"
            subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "generate_samples.py"), "--count", "6", "--output", str(data)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            with EvaluationRepository(database) as repository:
                BatchProcessor(repository).run(
                    load_manifest(data / "manifest.csv"),
                    batch_id="TAMPER-BATCH",
                    run_id="TAMPER-RUN",
                )
                from speech_eval.exporters import export_repository
                export_repository(repository, output, batch_id="TAMPER-BATCH", run_id="TAMPER-RUN")
            csv_path = output / "evaluation-results.csv"
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["场景类型"] = "被篡改场景"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            check = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "acceptance_check.py"),
                    "--db",
                    str(database),
                    "--export",
                    str(output / "evaluation-results.jsonl"),
                    "--export",
                    str(csv_path),
                    "--schema",
                    str(ROOT / "schemas" / "evaluation-result.schema.json"),
                    "--batch-id",
                    "TAMPER-BATCH",
                    "--run-id",
                    "TAMPER-RUN",
                    "--min-count",
                    "6",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(check.returncode, 1)
            self.assertIn("differs", check.stdout)



    def test_checker_rejects_extra_public_top_level_field(self):
        """Malformed exports must not be normalized into passing 14-field rows."""

        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            data = work / "data"
            database = work / "evaluation.db"
            output = work / "exports"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "generate_samples.py"),
                    "--count",
                    "6",
                    "--output",
                    str(data),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            with EvaluationRepository(database) as repository:
                BatchProcessor(repository).run(
                    load_manifest(data / "manifest.csv"),
                    batch_id="EXTRA-FIELD-BATCH",
                    run_id="EXTRA-FIELD-RUN",
                )
                from speech_eval.exporters import export_repository

                export_repository(
                    repository,
                    output,
                    batch_id="EXTRA-FIELD-BATCH",
                    run_id="EXTRA-FIELD-RUN",
                )

            jsonl_path = output / "evaluation-results.jsonl"
            rows = [
                json.loads(line)
                for line in jsonl_path.read_text(encoding="utf-8").splitlines()
            ]
            rows[0]["未声明顶层字段"] = "must fail"
            jsonl_path.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )
            check = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "acceptance_check.py"),
                    "--db",
                    str(database),
                    "--export",
                    str(jsonl_path),
                    "--export",
                    str(output / "evaluation-results.csv"),
                    "--schema",
                    str(ROOT / "schemas" / "evaluation-result.schema.json"),
                    "--batch-id",
                    "EXTRA-FIELD-BATCH",
                    "--run-id",
                    "EXTRA-FIELD-RUN",
                    "--min-count",
                    "6",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
            self.assertIn("expected exactly 14 top-level fields", check.stdout)
if __name__ == "__main__":
    unittest.main()


from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.audit_multistage_results import audit_database
from speech_eval.serialization import result_from_dict


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_RESULTS = ROOT / "data" / "generated_verify3" / "results.jsonl"
RUN_ID = "RUN-AUDIT-TEST"


class MultiStageAuditTests(unittest.TestCase):
    def _database(self, *, break_translation: bool = False) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        database = Path(temporary.name) / "audit.db"
        wanted = {"SYN-0001", "SYN-0003", "SYN-0005"}
        rows: list[dict] = []
        for line in FIXTURE_RESULTS.read_text(encoding="utf-8").splitlines():
            public = json.loads(line)
            if public.get("样例ID") not in wanted:
                continue
            result = result_from_dict(public)
            result.run_id = RUN_ID
            result.batch_id = "BATCH-AUDIT-TEST"
            payload = result.to_dict(chinese_fields=False)
            if break_translation and result.sample_id == "SYN-0003":
                payload["system_output"]["translation"] = "-"
            rows.append(payload)
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "CREATE TABLE results(run_id TEXT,sample_id TEXT,status TEXT,payload_json TEXT)"
            )
            connection.executemany(
                "INSERT INTO results VALUES (?,?,?,?)",
                [
                    (RUN_ID, row["sample_id"], "COMPLETED", json.dumps(row, ensure_ascii=False))
                    for row in rows
                ],
            )
            connection.commit()
        finally:
            connection.close()
        return temporary, database

    def test_representative_chains_pass(self) -> None:
        temporary, database = self._database()
        self.addCleanup(temporary.cleanup)
        report = audit_database(database, RUN_ID)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["capability_chains"], ["C01", "C02", "C03"])
        self.assertEqual(report["stage_metric_count"], 5)
        self.assertEqual(report["stage_evidence_count"], 5)

    def test_missing_translation_output_fails(self) -> None:
        temporary, database = self._database(break_translation=True)
        self.addCleanup(temporary.cleanup)
        report = audit_database(database, RUN_ID)
        self.assertFalse(report["ok"])
        self.assertTrue(
            any("system output field translation is missing" in error for error in report["errors"]),
            report["errors"],
        )


if __name__ == "__main__":
    unittest.main()

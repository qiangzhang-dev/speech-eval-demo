from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.capture_validation_evidence import main, project_path, sha256


ROOT = Path(__file__).resolve().parents[1]


class CaptureValidationEvidenceTests(unittest.TestCase):
    def test_project_path_rejects_escape(self):
        self.assertEqual(project_path(ROOT, Path("docs/evidence"), label="test"), ROOT / "docs" / "evidence")
        with self.assertRaisesRegex(ValueError, "inside project root"):
            project_path(ROOT, Path("../outside"), label="test")

    def test_snapshot_has_hashes_summaries_and_non_formal_boundary(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            work = Path(temp)
            source = work / "input.txt"
            source.write_text("frozen", encoding="utf-8")
            output = work / "evidence"
            acceptance_payload = {
                "ok": True,
                "count": 110,
                "data_classification": "synthetic_engineering_validation",
                "formal_acceptance_claim": False,
            }
            responses = [
                {"argv": ["tests"], "returncode": 0, "stdout": "", "stderr": "Ran 39 tests in 1.250s\n\nOK\n"},
                {"argv": ["acceptance"], "returncode": 0, "stdout": json.dumps(acceptance_payload), "stderr": ""},
            ]
            argv = [
                "capture_validation_evidence.py",
                "--output", str(output.relative_to(ROOT)),
                "--db", str(source.relative_to(ROOT)),
                "--jsonl", str(source.relative_to(ROOT)),
                "--csv", str(source.relative_to(ROOT)),
                "--schema", str(source.relative_to(ROOT)),
                "--manifest", str(source.relative_to(ROOT)),
                "--thresholds", str(source.relative_to(ROOT)),
            ]
            with patch.object(sys, "argv", argv), patch(
                "scripts.capture_validation_evidence.run", side_effect=responses
            ):
                self.assertEqual(main(), 0)
            evidence = json.loads((output / "validation-evidence.json").read_text(encoding="utf-8"))
            hashes = json.loads((output / "SHA256SUMS.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["test_summary"]["count"], 39)
            self.assertTrue(evidence["test_summary"]["passed"])
            self.assertEqual(evidence["acceptance_summary"], acceptance_payload)
            self.assertFalse(evidence["formal_acceptance_claim"])
            self.assertEqual(len(hashes["files"]), 6)
            self.assertTrue(all(item["sha256"] == sha256(source) for item in hashes["files"]))


if __name__ == "__main__":
    unittest.main()

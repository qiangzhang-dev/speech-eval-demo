from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.generate_samples import generate  # noqa: E402


class GeneratedDataTests(unittest.TestCase):
    def test_compact_manifest_contract_and_coverage(self):
        expected = {
            "sample_id", "scene_type", "subscene_type", "task_types", "input_path",
            "reference_text", "system_output", "language", "metadata",
        }
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            generate(110, output)
            with (output / "manifest.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 110)
            self.assertTrue(expected.issubset(rows[0]))
            self.assertEqual(len({row["sample_id"] for row in rows}), 110)
            self.assertEqual({row["scene_type"] for row in rows}, {"短语音/录音转写", "会议/办公语音翻译", "车载/语音助手交互"})
            self.assertGreaterEqual(len({row["subscene_type"] for row in rows}), 2)
            self.assertTrue(all((output / row["input_path"]).exists() for row in rows))
            self.assertTrue(all(json.loads(row["metadata"])["synthetic"] is True for row in rows))

    def test_evaluation_plan_matches_generated_quota_and_candidate_scope(self):
        plan = json.loads((ROOT / "config" / "evaluation-plan.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            results = generate(110, output)

            planned_scenarios = {
                scenario["name"]: scenario["sample_count"] for scenario in plan["scenarios"]
            }
            planned_subscenarios = {
                subscenario["name"]: subscenario["sample_count"]
                for scenario in plan["scenarios"]
                for subscenario in scenario["subscenarios"]
            }
            self.assertEqual(Counter(item["场景类型"] for item in results), planned_scenarios)
            self.assertEqual(
                Counter(item["输入数据"]["subscene_type"] for item in results),
                planned_subscenarios,
            )
            self.assertEqual(plan["dataset_policy"]["planned_sample_count"], 110)
            self.assertEqual(plan["status"], "synthetic_engineering_validation_only")
            self.assertEqual(
                plan["data_classification"], "synthetic_engineering_validation"
            )
            self.assertFalse(plan["formal_acceptance_claim"])
            self.assertIn("仅用于工程链路验证", plan["description"])

            metric = plan["metric_baseline"]
            self.assertEqual(metric["threshold_version"], "1.0.0-candidate.1")
            self.assertEqual(metric["threshold_approval_status"], "pending_approval")
            self.assertFalse(metric["threshold_effective"])
            self.assertEqual(metric["formal_metric_status_when_threshold_ineffective"], "不判定")
            batch = json.loads((output / "batch.json").read_text(encoding="utf-8"))
            self.assertEqual(batch["configuration"]["plan_version"], plan["plan_version"])


if __name__ == "__main__":
    unittest.main()

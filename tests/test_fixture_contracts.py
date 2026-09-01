from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.generate_samples import FIELD_NAMES, cer, cer_status, generate  # noqa: E402
from scripts.validate_fixture import validate_result_shape, validate_results  # noqa: E402


class FixtureContractTests(unittest.TestCase):
    def test_cer_normalization_and_boundaries(self):
        self.assertEqual(cer("ＡＢＣ", "abc"), 0.0)
        self.assertEqual(cer_status(0.10), "通过")
        self.assertEqual(cer_status(0.100001), "需关注")
        self.assertEqual(cer_status(0.20), "需关注")
        self.assertEqual(cer_status(0.200001), "失败")
        with self.assertRaises(ValueError):
            cer("，。", "x")

    def test_small_fixture_is_deterministic_and_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            a = generate(6, first)
            b = generate(6, second)
            self.assertEqual(a, b)
            self.assertEqual(first.joinpath("results.jsonl").read_bytes(), second.joinpath("results.jsonl").read_bytes())
            self.assertEqual(first.joinpath("batch.json").read_bytes(), second.joinpath("batch.json").read_bytes())
            self.assertEqual(set(a[0]), set(FIELD_NAMES))
            report = validate_results(a, minimum=6)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["typical_scenario_count"], 3)
            self.assertEqual(report["subscenario_count"], 6)
            self.assertEqual(report["capability_chain_count"], 3)

    def test_generated_candidate_meets_formal_count_and_references(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            results = generate(110, output)
            report = validate_results(results, minimum=100)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["count"], 110)
            self.assertTrue(all(result["人工修订"] == "-" for result in results))
            self.assertGreaterEqual(report["typical_scenario_count"], 3)
            self.assertGreaterEqual(report["subscenario_count"], 2)
            self.assertGreaterEqual(report["capability_chain_count"], 2)
            batch = json.loads(output.joinpath("batch.json").read_text(encoding="utf-8"))
            self.assertEqual(batch["schema_version"], "1.0.0")
            self.assertEqual(len(batch["results"]), 110)

    def test_duplicate_id_and_missing_field_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            results = generate(2, Path(temp))
            results[1]["样例ID"] = results[0]["样例ID"]
            report = validate_results(results, minimum=2)
            self.assertFalse(report["ok"])
            self.assertTrue(any("duplicate" in error for error in report["errors"]))
            del results[0]["最终结论"]
            report = validate_results(results, minimum=2)
            self.assertFalse(report["ok"])
            self.assertTrue(any("exactly 14" in error for error in report["errors"]))

    def test_frozen_schema_rejects_extra_top_level_and_wrong_nested_shapes(self):
        with tempfile.TemporaryDirectory() as temp:
            result = generate(1, Path(temp))[0]
            self.assertEqual(validate_result_shape(result), [])
            result["额外字段"] = "must fail"
            self.assertTrue(validate_result_shape(result))
            del result["额外字段"]
            result["量化指标"] = {"CER": 0.0}
            self.assertTrue(validate_result_shape(result))
            result["量化指标"] = [{"指标ID": "broken"}]
            self.assertTrue(validate_result_shape(result))


if __name__ == "__main__":
    unittest.main()

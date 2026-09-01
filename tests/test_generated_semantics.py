from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.generate_samples import generate  # noqa: E402


class GeneratedSemanticFixtureTests(unittest.TestCase):
    """Regression gates for the assignment's six semantic sub-scenarios."""

    def test_default_batch_has_unique_source_text_and_grounded_noise_long_cases(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            results = generate(110, output)

            self.assertEqual(len(results), 110)
            transcripts = [item["参考文本/标注"]["transcript"] for item in results]
            self.assertEqual(len(set(transcripts)), 110)

            noisy = [item for item in results if item["输入数据"]["subscene_type"] == "噪声/数字英文混合"]
            self.assertEqual(len(noisy), 19)
            for item in noisy:
                text = item["参考文本/标注"]["transcript"]
                self.assertTrue(any(ch.isdigit() or (ch.isascii() and ch.isalpha()) for ch in text), text)
                self.assertIn("noise", item["音频信息"]["signal_conditions"])

            long_translation = [item for item in results if item["输入数据"]["subscene_type"] == "长句/上下文翻译"]
            self.assertEqual(len(long_translation), 18)
            for item in long_translation:
                ref = item["参考文本/标注"]
                self.assertGreaterEqual(len(ref["transcript"]), 30)
                self.assertNotEqual(ref["translation_context"], "-")
                self.assertNotEqual(ref["translation"], "-")

    def test_multitask_annotations_are_carried_into_manifest_and_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "generated"
            results = generate(110, output)
            by_id = {item["样例ID"]: item for item in results}

            with (output / "manifest.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 110)
            self.assertIn("reference_annotation", rows[0])
            self.assertIn("system_output", rows[0])

            for row in rows:
                result = by_id[row["sample_id"]]
                reference = json.loads(row["reference_annotation"])
                system_output = json.loads(row["system_output"])
                self.assertEqual(reference["transcript"], result["参考文本/标注"]["transcript"])
                self.assertEqual(system_output["transcript"], result["系统输出"]["transcript"])
                fixture_path = output / row["input_path"]
                self.assertTrue(fixture_path.exists(), fixture_path)
                fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
                self.assertEqual(fixture["data_classification"], "synthetic_engineering_validation")
                self.assertEqual(fixture["reference_annotation"], result["参考文本/标注"])
                self.assertEqual(fixture["system_output"], result["系统输出"])

                if row["scene_type"] == "会议/办公语音翻译":
                    self.assertNotEqual(reference["translation"], "-")
                    self.assertNotEqual(system_output["translation"], "-")
                elif row["scene_type"] == "车载/语音助手交互":
                    for key in ("intent", "slots", "expected_action", "expected_reply"):
                        self.assertNotEqual(reference[key], "-", (row["sample_id"], key))
                    for key in ("intent", "slots", "action", "reply"):
                        self.assertNotEqual(system_output[key], "-", (row["sample_id"], key))

    def test_no_cross_case_fixed_translation_or_slot_fact(self):
        with tempfile.TemporaryDirectory() as temp:
            results = generate(110, Path(temp) / "generated")
            for result in results:
                if result["场景类型"] == "会议/办公语音翻译":
                    ref = result["参考文本/标注"]
                    out = result["系统输出"]
                    self.assertNotIn("living room air conditioner", ref["translation"].lower())
                    self.assertNotIn("living room air conditioner", out["translation"].lower())
                if result["场景类型"] == "车载/语音助手交互":
                    ref = result["参考文本/标注"]
                    out = result["系统输出"]
                    self.assertEqual(ref["slots"], out["slots"])
                    location = ref["slots"].get("位置") or ref["slots"].get("地点")
                    self.assertIsNotNone(location)
                    self.assertIn(location, ref["transcript"])


if __name__ == "__main__":
    unittest.main()

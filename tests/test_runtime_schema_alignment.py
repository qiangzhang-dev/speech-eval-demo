from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from speech_eval.batch import BatchProcessor
from speech_eval.models import Sample
from speech_eval.repository import EvaluationRepository
from speech_eval.revisions import apply_human_revision


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_fixture import _schema_errors, validate_result_shape  # noqa: E402


class RuntimeSchemaAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(
            (ROOT / "schemas" / "evaluation-result.schema.json").read_text(encoding="utf-8")
        )

    def assert_schema_valid(self, payload):
        self.assertEqual(validate_result_shape(payload), [])
        self.assertEqual(_schema_errors(payload, self.schema, self.schema, "$"), [])

    def test_runtime_result_and_human_revision_match_frozen_schema(self):
        sample = Sample(
            sample_id="SCHEMA-0001",
            scene_type="短语音/录音转写",
            subscene_type="普通话口述",
            task_types=["语音识别"],
            input_data={"type": "text", "synthetic": True},
            audio_info="-",
            reference_annotation={"transcript": "打开空调"},
            system_output={"transcript": "打开空调"},
        )
        with tempfile.TemporaryDirectory() as temp:
            with EvaluationRepository(Path(temp) / "evaluation.db") as repository:
                summary = BatchProcessor(repository).run([sample], batch_id="schema-batch")
                result = repository.get_result(sample.sample_id, run_id=summary.run_id)
                self.assert_schema_valid(result.to_dict())

                diagnosis = dict(result.quality_diagnosis)
                diagnosis["text"] = "人工复核：合成样例转写一致。"
                updated, _ = apply_human_revision(
                    result,
                    {"quality_diagnosis": diagnosis},
                    editor="schema-reviewer",
                    reason="验证人工修订公开结构",
                    repository=repository,
                )
                self.assert_schema_valid(updated.to_dict())

    def test_empty_reference_uses_not_applicable_metric_without_fabricating_value(self):
        sample = Sample(
            sample_id="SCHEMA-EMPTY-0001",
            scene_type="短语音/录音转写",
            subscene_type="普通话口述",
            task_types=["语音识别"],
            input_data={"type": "text", "synthetic": True},
            audio_info="-",
            reference_annotation="-",
            system_output={"transcript": "待复核文本"},
        )
        with tempfile.TemporaryDirectory() as temp:
            with EvaluationRepository(Path(temp) / "evaluation.db") as repository:
                summary = BatchProcessor(repository).run([sample])
                result = repository.get_result(sample.sample_id, run_id=summary.run_id)
                self.assertEqual(result.quantitative_metrics, "-")
                self.assert_schema_valid(result.to_dict())


if __name__ == "__main__":
    unittest.main()

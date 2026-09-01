from __future__ import annotations

import unittest
from pathlib import Path

from scripts.acceptance_check import result_errors
from speech_eval.batch import BatchProcessor
from speech_eval.models import Sample


ROOT = Path(__file__).resolve().parents[1]


class AcceptanceSemanticEvidenceTests(unittest.TestCase):
    def _exact_match_result(self):
        sample = Sample(
            sample_id="SEMANTIC-001",
            scene_type="短语音/录音转写",
            subscene_type="普通话口述",
            task_types=["语音识别"],
            input_data={"type": "text", "capability_chain": "C01"},
            reference_annotation={"transcript": "打开客厅空调"},
            system_output={"transcript": "打开客厅空调"},
        )
        return BatchProcessor(object()).evaluate_sample(
            sample, batch_id="SEMANTIC-BATCH", run_id="SEMANTIC-RUN"
        ).to_dict(chinese_fields=True)

    def test_exact_match_metric_snapshot_passes_semantic_gate(self):
        errors, _ = result_errors(
            [self._exact_match_result()],
            schema=ROOT / "schemas" / "evaluation-result.schema.json",
        )
        self.assertEqual(errors, [])

    def test_sample_id_only_evidence_cannot_support_cer_conclusion(self):
        result = self._exact_match_result()
        evidence_id = "E-SEMANTIC-001-INPUT"
        result["证据片段"] = [
            {
                "evidence_id": evidence_id,
                "type": "input_presence",
                "source": "sample",
                "location": "sample_id",
                "content": "SEMANTIC-001",
            }
        ]
        for field in ("质量诊断", "影响评估", "最终结论"):
            result[field]["evidence_ids"] = [evidence_id]
        for item in result["优化建议"]:
            item["evidence_ids"] = [evidence_id]
        errors, _ = result_errors(
            [result],
            schema=ROOT / "schemas" / "evaluation-result.schema.json",
        )
        self.assertTrue(
            any("lacks matching metric_snapshot evidence" in error for error in errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()

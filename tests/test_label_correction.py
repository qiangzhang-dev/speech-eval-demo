from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from speech_eval.batch import BatchProcessor
from speech_eval.models import Sample
from speech_eval.repository import EvaluationRepository
from speech_eval.revisions import apply_human_revision, apply_label_correction
from speech_eval.validation import validate_result


def _sample(sample_id: str = "LABEL-0001") -> Sample:
    return Sample(
        sample_id=sample_id,
        scene_type="短语音/录音转写",
        subscene_type="普通话口述",
        task_types=["语音识别"],
        input_data={"type": "synthetic", "subscene_type": "普通话口述", "capability_chain": "C01"},
        audio_info={"uri": f"synthetic://{sample_id}.wav"},
        reference_annotation={"transcript": "打开空调"},
        system_output={"transcript": "打开空调"},
    )


class LabelCorrectionTests(unittest.TestCase):
    def test_label_correction_is_separate_and_invalidates_derived_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            with EvaluationRepository(Path(temp) / "evaluation.db") as repository:
                summary = BatchProcessor(repository).run([_sample()], batch_id="LABEL-BATCH")
                result = repository.get_result("LABEL-0001", run_id=summary.run_id)
                assert result is not None
                old_evidence_ids = {
                    item["evidence_id"] for item in result.evidence if isinstance(item, dict)
                }

                updated, revisions = apply_label_correction(
                    result,
                    {"子场景": "噪声/数字英文混合"},
                    editor="label-reviewer",
                    reason="清单复核后纠正子场景标签",
                    repository=repository,
                )

                self.assertEqual(len(revisions), 1)
                self.assertEqual(revisions[0].field_name, "subscene_type")
                self.assertEqual(updated.subscene_type, "噪声/数字英文混合")
                self.assertEqual(updated.input_data["subscene_type"], "噪声/数字英文混合")
                self.assertEqual(updated.quantitative_metrics["status"], "不判定")
                self.assertIn(updated.quantitative_metrics["candidate_status"], {"通过", "需关注", "失败"})
                self.assertEqual(updated.final_conclusion["level"], "证据不足")
                self.assertEqual(updated.human_revision["field_name"], "subscene_type")
                correction_evidence = [
                    item for item in updated.evidence
                    if isinstance(item, dict) and item.get("type") == "label_correction_invalidation"
                ]
                self.assertEqual(len(correction_evidence), 1)
                self.assertTrue(correction_evidence[0]["content"]["invalidated_fields"])
                self.assertEqual(validate_result(updated), [])
                self.assertTrue(old_evidence_ids.issubset({
                    item["evidence_id"] for item in updated.evidence if isinstance(item, dict)
                }))
                self.assertTrue(
                    all(
                        set(item) == {"evidence_id", "type", "source", "location", "content"}
                        for item in updated.evidence
                        if isinstance(item, dict) and item["evidence_id"] in old_evidence_ids
                    )
                )
                stored = repository.get_result("LABEL-0001", run_id=summary.run_id)
                assert stored is not None
                self.assertEqual(stored.subscene_type, updated.subscene_type)
                self.assertEqual(len(repository.list_revisions("LABEL-0001")), 1)

    def test_generic_revision_still_rejects_labels_and_invalid_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with EvaluationRepository(Path(temp) / "evaluation.db") as repository:
                summary = BatchProcessor(repository).run([_sample()], batch_id="LABEL-BATCH")
                result = repository.get_result("LABEL-0001", run_id=summary.run_id)
                assert result is not None
                with self.assertRaisesRegex(ValueError, "field is not editable"):
                    apply_human_revision(
                        result,
                        {"scene_type": "车载/语音助手交互"},
                        editor="reviewer",
                        reason="must use label correction endpoint",
                    )
                with self.assertRaisesRegex(ValueError, "unknown scene_type"):
                    apply_label_correction(
                        result,
                        {"scene_type": "未定义场景"},
                        editor="reviewer",
                        reason="invalid label",
                    )
                with self.assertRaisesRegex(ValueError, "task_types must be"):
                    apply_label_correction(
                        result,
                        {"task_types": "语音识别"},
                        editor="reviewer",
                        reason="invalid label",
                    )


if __name__ == "__main__":
    unittest.main()

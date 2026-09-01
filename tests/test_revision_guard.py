from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from speech_eval.batch import BatchProcessor
from speech_eval.models import Sample
from speech_eval.repository import EvaluationRepository
from speech_eval.revisions import apply_human_revision
from speech_eval.validation import validate_result


def _sample(sample_id: str = "GUARD-0001") -> Sample:
    return Sample(
        sample_id=sample_id,
        scene_type="短语音/录音转写",
        subscene_type="普通话口述",
        task_types=["语音识别"],
        input_data={"type": "text", "synthetic": True},
        audio_info="-",
        reference_annotation={"transcript": "打开空调"},
        system_output={"transcript": "打开空调"},
    )


class RevisionGuardTests(unittest.TestCase):
    def test_source_facts_and_conclusion_are_rejected_before_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            with EvaluationRepository(Path(temp) / "evaluation.db") as repository:
                summary = BatchProcessor(repository).run([_sample()], batch_id="GUARD-BATCH")
                result = repository.get_result("GUARD-0001", run_id=summary.run_id)
                assert result is not None
                original = result.to_dict(chinese_fields=False)
                replacements = {
                    "scene_type": "changed",
                    "task_types": ["changed"],
                    "input_data": {"changed": True},
                    "audio_info": {"changed": True},
                    "reference_annotation": {"transcript": "篡改参考"},
                    "system_output": {"transcript": "篡改输出"},
                    "quantitative_metrics": {"value": 0.0},
                    "evidence": [],
                    "final_conclusion": {"level": "通过", "evidence_ids": []},
                }
                for field_name, replacement in replacements.items():
                    with self.subTest(field_name=field_name):
                        with self.assertRaisesRegex(ValueError, "field is not editable"):
                            apply_human_revision(
                                result,
                                {field_name: replacement},
                                editor="guard-test",
                                reason="attempt objective-field rewrite",
                                repository=repository,
                            )
                        persisted = repository.get_result("GUARD-0001", run_id=summary.run_id)
                        self.assertIsNotNone(persisted)
                        self.assertEqual(persisted.to_dict(chinese_fields=False), original)
                        self.assertEqual(repository.list_revisions("GUARD-0001"), [])

    def test_allowed_revision_requires_closed_evidence_and_keeps_complete_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            with EvaluationRepository(Path(temp) / "evaluation.db") as repository:
                summary = BatchProcessor(repository).run([_sample()], batch_id="GUARD-BATCH")
                result = repository.get_result("GUARD-0001", run_id=summary.run_id)
                assert result is not None
                evidence_ids = [item["evidence_id"] for item in result.evidence]

                invalid = dict(result.quality_diagnosis)
                invalid["evidence_ids"] = ["EVIDENCE-DOES-NOT-EXIST"]
                with self.assertRaisesRegex(ValueError, "evidence-reference closure"):
                    apply_human_revision(
                        result,
                        {"quality_diagnosis": invalid},
                        editor="guard-test",
                        reason="attempt dangling evidence reference",
                        repository=repository,
                    )
                self.assertEqual(repository.list_revisions("GUARD-0001"), [])

                revised = dict(result.impact_assessment)
                revised["text"] = "人工复核后的影响评估"
                updated, revisions = apply_human_revision(
                    result,
                    {"impact_assessment": revised},
                    editor="guard-test",
                    reason="grounded impact review",
                    repository=repository,
                )
                self.assertEqual(len(revisions), 1)
                self.assertEqual(revisions[0].field_name, "impact_assessment")
                self.assertEqual(revisions[0].before, result.impact_assessment)
                self.assertEqual(revisions[0].after, revised)
                self.assertEqual(revisions[0].run_id, summary.run_id)
                self.assertEqual(updated.impact_assessment, revised)
                self.assertEqual(updated.human_revision["revision_id"], revisions[0].revision_id)
                self.assertEqual(updated.human_revision["field_name"], "impact_assessment")
                self.assertEqual(validate_result(updated), [])

                persisted = repository.get_result("GUARD-0001", run_id=summary.run_id)
                self.assertIsNotNone(persisted)
                self.assertEqual(persisted.impact_assessment, revised)
                audit = repository.list_revisions("GUARD-0001")
                self.assertEqual(len(audit), 1)
                self.assertEqual(audit[0].to_dict(), revisions[0].to_dict())
                self.assertEqual(
                    set(persisted.impact_assessment["evidence_ids"]),
                    set(evidence_ids),
                )


if __name__ == "__main__":
    unittest.main()

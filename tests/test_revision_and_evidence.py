from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from speech_eval.batch import BatchProcessor
from speech_eval.diagnosis import DiagnosticEngine
from speech_eval.models import Sample
from speech_eval.repository import EvaluationRepository
from speech_eval.revisions import apply_human_revision
from speech_eval.validation import validate_result


def sample(sample_id: str = "REV-0001") -> Sample:
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


class RevisionAndEvidenceTests(unittest.TestCase):
    def test_evidence_references_are_closed_and_revision_keeps_run_id(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "evaluation.db"
            with EvaluationRepository(database) as repository:
                summary = BatchProcessor(repository).run([sample()], batch_id="batch-revision")
                result = repository.get_result("REV-0001", run_id=summary.run_id)
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(validate_result(result), [])

                new_diagnosis = dict(result.quality_diagnosis)
                new_diagnosis["摘要"] = "人工复核：当前合成样例转写一致。"
                updated, revisions = apply_human_revision(
                    result,
                    {"quality_diagnosis": new_diagnosis},
                    editor="reviewer-01",
                    reason="补充人工复核结论",
                    repository=repository,
                )

                self.assertEqual(len(revisions), 1)
                self.assertEqual(revisions[0].run_id, summary.run_id)
                stored = repository.list_revisions("REV-0001")
                self.assertEqual(len(stored), 1)
                self.assertEqual(stored[0].run_id, summary.run_id)
                self.assertEqual(stored[0].before, result.quality_diagnosis)
                self.assertEqual(stored[0].after, new_diagnosis)
                self.assertEqual(repository.get_result("REV-0001", run_id=summary.run_id).quality_diagnosis, updated.quality_diagnosis)

    def test_one_provider_failure_does_not_drop_other_results(self):
        class SelectiveProvider:
            def diagnose(self, context):
                if context.sample.sample_id == "BAD-0001":
                    raise RuntimeError("injected provider failure")
                from speech_eval.diagnosis import DeterministicRuleDiagnosticProvider

                return DeterministicRuleDiagnosticProvider().diagnose(context)

        with tempfile.TemporaryDirectory() as temp:
            with EvaluationRepository(Path(temp) / "evaluation.db") as repository:
                processor = BatchProcessor(
                    repository,
                    diagnostic_engine=DiagnosticEngine(SelectiveProvider()),
                )
                summary = processor.run([sample("GOOD-0001"), sample("BAD-0001")])
                self.assertEqual(summary.total, 2)
                self.assertEqual(summary.completed, 1)
                self.assertEqual(summary.failed, 1)
                self.assertEqual(summary.status, "PARTIAL_FAILED")
                self.assertEqual(repository.count_results(run_id=summary.run_id), 2)
                failed = repository.get_result("BAD-0001", run_id=summary.run_id)
                self.assertEqual(failed.status, "FAILED")
                self.assertEqual(validate_result(failed), [])


if __name__ == "__main__":
    unittest.main()

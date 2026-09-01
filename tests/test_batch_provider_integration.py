from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from speech_eval.batch import BatchProcessor
from speech_eval.models import Sample
from speech_eval.providers.mock import SequenceDiagnosticProvider
from speech_eval.repository import EvaluationRepository
from speech_eval.validation import validate_result


def make_sample(sample_id: str) -> Sample:
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


class BatchProviderIntegrationTests(unittest.TestCase):
    def test_default_batch_uses_validated_provider_path(self):
        with tempfile.TemporaryDirectory() as temp:
            with EvaluationRepository(Path(temp) / "evaluation.db") as repository:
                summary = BatchProcessor(repository).run(
                    [make_sample("PROVIDER-OK-0001")],
                    batch_id="provider-ok-batch",
                    run_id="provider-ok-run",
                )
                result = repository.get_result("PROVIDER-OK-0001", run_id=summary.run_id)
                self.assertEqual(summary.completed, 1)
                self.assertEqual(result.status, "COMPLETED")
                self.assertEqual(validate_result(result), [])
                self.assertEqual(result.prompt_version, "offline-rules-v1")

    def test_provider_exhaustion_persists_ai_failed_and_retry_log(self):
        with tempfile.TemporaryDirectory() as temp:
            with EvaluationRepository(Path(temp) / "evaluation.db") as repository:
                provider = SequenceDiagnosticProvider([ValueError("bad provider")])
                summary = BatchProcessor(
                    repository,
                    provider=provider,
                    provider_max_attempts=2,
                ).run(
                    [make_sample("PROVIDER-BAD-0001")],
                    batch_id="provider-bad-batch",
                    run_id="provider-bad-run",
                )
                result = repository.get_result("PROVIDER-BAD-0001", run_id=summary.run_id)
                self.assertEqual(summary.completed, 0)
                self.assertEqual(summary.failed, 1)
                self.assertEqual(summary.status, "PARTIAL_FAILED")
                self.assertEqual(result.status, "AI_FAILED")
                self.assertEqual(validate_result(result), [])
                logs = repository.list_logs(sample_id="PROVIDER-BAD-0001", run_id=summary.run_id)
                failure_logs = [entry for entry in logs if entry["status"] == "AI_FAILED"]
                self.assertEqual(len(failure_logs), 1)
                self.assertEqual(failure_logs[0]["retry_count"], 2)
                self.assertEqual(provider.calls, 2)


if __name__ == "__main__":
    unittest.main()


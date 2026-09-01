from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import run_codex_ai_diagnosis as bridge
from speech_eval.batch import BatchProcessor
from speech_eval.models import Sample
from speech_eval.repository import EvaluationRepository


def _sample() -> Sample:
    return Sample(sample_id="SYN-0001", scene_type="短语音/录音转写", subscene_type="普通话口述", task_types=["语音识别"],
                   input_data={"type": "text", "synthetic": True}, audio_info="-",
                   reference_annotation={"transcript": "打开空调"}, system_output={"transcript": "打开空调"})


class _FakePopen:
    response: dict[str, object] = {}
    return_code = 0
    calls: list[list[str]] = []

    def __init__(self, command: list[str], **_: object) -> None:
        type(self).calls.append(command)
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(json.dumps(type(self).response, ensure_ascii=False), encoding="utf-8")
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("codex-live-event: model running\n")

    def wait(self) -> int:
        return type(self).return_code

    def kill(self) -> None:
        return None


class CodexAiDiagnosisBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakePopen.calls = []
        _FakePopen.return_code = 0

    def _fixture(self, root: Path) -> tuple[Path, Path, dict[str, object]]:
        sample = _sample()
        manifest = root / "manifest.jsonl"
        manifest.write_text(json.dumps(sample.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
        database = root / "evaluation.db"
        with EvaluationRepository(database) as repository:
            BatchProcessor(repository).run([sample], batch_id="REAL-BATCH", run_id="REAL-RUN")
            result = repository.get_result(sample.sample_id, run_id="REAL-RUN")
            assert result is not None
            before = result.to_dict(chinese_fields=False)
        evidence = before["evidence"]
        ids = [item["evidence_id"] for item in evidence]
        valid = {"quality_diagnosis": {"text": "真实模型诊断", "evidence_ids": ids}, "evidence": evidence,
                 "impact_assessment": {"text": "基于现有证据的影响", "evidence_ids": ids},
                 "recommendations": [{"text": "继续观察同场景样例", "evidence_ids": ids}],
                 "final_conclusion": {"level": "证据不足", "basis": "候选阈值尚未审批生效", "evidence_ids": ids},
                 "prompt_version": bridge.PROMPT_VERSION}
        return database, manifest, {"before": before, "valid": valid}

    def _invoke(self, database: Path, manifest: Path, root: Path) -> tuple[int, str]:
        out = io.StringIO()
        with patch.object(bridge.subprocess, "Popen", _FakePopen), redirect_stdout(out):
            code = bridge.main(["--database", str(database), "--manifest", str(manifest), "--sample-id", "SYN-0001",
                                "--run-id", "REAL-RUN", "--session-dir", str(root / "session")])
        return code, out.getvalue()

    def test_valid_response_updates_only_ai_fields_and_records_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database, manifest, fixture = self._fixture(root)
            _FakePopen.response = fixture["valid"]
            code, stdout = self._invoke(database, manifest, root)
            self.assertEqual(code, 0, stdout)
            self.assertIn("codex-live-event: model running", stdout)
            command = _FakePopen.calls[0]
            for required in ("--ignore-rules", "--ephemeral", "--sandbox", "read-only", "--skip-git-repo-check", "--output-schema", "--output-last-message"):
                self.assertIn(required, command)
            self.assertEqual(command[command.index("-m") + 1], "gpt-5.6-sol")
            with EvaluationRepository(database) as repository:
                result = repository.get_result("SYN-0001", run_id="REAL-RUN")
                assert result is not None
                after = result.to_dict(chinese_fields=False)
                logs = repository.list_logs(sample_id="SYN-0001", run_id="REAL-RUN")
            for key, value in fixture["before"].items():
                if key not in bridge.AI_FIELDS:
                    self.assertEqual(after[key], value, key)
            self.assertEqual(after["evidence"], fixture["before"]["evidence"])
            self.assertEqual(after["quality_diagnosis"]["text"], "真实模型诊断")
            audit = json.loads(logs[-1]["message"])
            self.assertEqual((audit["provider"], audit["model"], audit["status"]), ("codex-cli", "gpt-5.6-sol", "COMPLETED"))
            self.assertTrue(audit["evidence_preserved"])

    def test_changed_evidence_returns_nonzero_without_overwriting_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            database, manifest, fixture = self._fixture(root)
            invalid = json.loads(json.dumps(fixture["valid"], ensure_ascii=False))
            invalid["evidence"][0]["location"] = "invented-location"
            _FakePopen.response = invalid
            code, stdout = self._invoke(database, manifest, root)
            self.assertNotEqual(code, 0)
            with EvaluationRepository(database) as repository:
                result = repository.get_result("SYN-0001", run_id="REAL-RUN")
                assert result is not None
                self.assertEqual(result.to_dict(chinese_fields=False), fixture["before"])
                logs = repository.list_logs(sample_id="SYN-0001", run_id="REAL-RUN")
            self.assertEqual(logs[-1]["status"], "AI_FAILED")
            summary = json.loads(stdout.splitlines()[-1])
            self.assertFalse(summary["ok"])
            self.assertFalse(summary["updated_same_run"])


if __name__ == "__main__":
    unittest.main()

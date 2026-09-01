from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from speech_eval.diagnosis import DiagnosticContext, DiagnosticResult  # noqa: E402
from speech_eval.models import Sample  # noqa: E402
from speech_eval.providers import (  # noqa: E402
    AI_FAILED,
    COMPLETED,
    InvalidEvidenceReferenceError,
    InvalidJSONError,
    InvalidStructureError,
    MissingFieldError,
    OpenAICompatibleDiagnosticProvider,
    ProviderConfigurationError,
    ProviderFailedError,
    ProviderTimeoutError,
    RetryingDiagnosticProvider,
    SequenceDiagnosticProvider,
    StaticDiagnosticProvider,
    validate_diagnostic_response,
)


def valid_payload() -> dict:
    return {
        "quality_diagnosis": {
            "text": "识别文本存在一个字符差异。",
            "evidence_ids": ["E-001"],
        },
        "evidence": [
            {
                "evidence_id": "E-001",
                "type": "text_diff",
                "source": "reference_vs_output",
                "location": "reference_index=1",
                "content": {"reference": "b", "hypothesis": "x"},
            }
        ],
        "impact_assessment": {
            "text": "字符差异可能影响可理解性。",
            "evidence_ids": ["E-001"],
        },
        "recommendations": [
            {
                "text": "复核该字符及相同场景样例。",
                "evidence_ids": ["E-001"],
            }
        ],
        "final_conclusion": {
            "level": "需关注",
            "basis": "存在有证据支持的字符差异。",
            "evidence_ids": ["E-001"],
        },
        "prompt_version": "test-provider-v1",
    }


def canonical_payload() -> dict:
    payload = valid_payload()
    return {
        "质量诊断": payload["quality_diagnosis"],
        "影响评估": payload["impact_assessment"],
        "优化建议": payload["recommendations"],
        "最终结论": payload["final_conclusion"],
    }


def context() -> DiagnosticContext:
    return DiagnosticContext(
        sample=Sample(
            sample_id="SYN-0001",
            scene_type="短语音转写",
            task_types=["语音识别"],
            input_data={"text": "abc"},
            audio_info="-",
            reference_annotation={"transcript": "abc"},
            system_output={"transcript": "axc"},
        ),
        metrics={"name": "CER", "value": 1 / 3},
        evidence=valid_payload()["evidence"],
    )


class ProviderContractTests(unittest.TestCase):
    def test_mapping_and_json_are_adapted_to_core_protocol(self):
        mapping_result = validate_diagnostic_response(valid_payload())
        json_result = validate_diagnostic_response(
            json.dumps(valid_payload(), ensure_ascii=False)
        )
        self.assertIsInstance(mapping_result, DiagnosticResult)
        self.assertEqual(mapping_result.to_dict(), json_result.to_dict())
        self.assertEqual(mapping_result.final_conclusion["level"], "需关注")

    def test_canonical_chinese_response_is_normalized_at_provider_boundary(self):
        supplied_evidence = valid_payload()["evidence"]
        result = validate_diagnostic_response(
            canonical_payload(),
            default_prompt_version="manifest-contract-v1",
            evidence_catalog=supplied_evidence,
        )
        self.assertEqual(result.quality_diagnosis, valid_payload()["quality_diagnosis"])
        self.assertEqual(result.evidence, supplied_evidence)
        self.assertEqual(result.prompt_version, "manifest-contract-v1")

    def test_canonical_chinese_response_rejects_schema_extra_fields(self):
        payload = canonical_payload()
        payload["质量诊断"]["unexpected"] = True
        with self.assertRaises(InvalidStructureError):
            validate_diagnostic_response(
                payload,
                evidence_catalog=valid_payload()["evidence"],
            )

    def test_timeout_and_invalid_json_are_retried_finitely(self):
        delegate = SequenceDiagnosticProvider(
            [TimeoutError("slow"), "not-json", valid_payload()]
        )
        provider = RetryingDiagnosticProvider(delegate, max_attempts=3)
        outcome = provider.execute(context())
        self.assertEqual(outcome.status, COMPLETED)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.attempts, 3)
        self.assertEqual(delegate.calls, 3)
        self.assertEqual(
            [error.code for error in outcome.errors],
            [ProviderTimeoutError.code, InvalidJSONError.code],
        )

    def test_missing_fields_return_ai_failed_and_throw_recognizable_error(self):
        delegate = StaticDiagnosticProvider({"evidence": []})
        provider = RetryingDiagnosticProvider(delegate, max_attempts=2)
        outcome = provider.execute(context())
        self.assertEqual(outcome.status, AI_FAILED)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.attempts, 2)
        self.assertIsInstance(outcome.error, MissingFieldError)
        self.assertEqual(delegate.calls, 2)
        with self.assertRaises(ProviderFailedError) as caught:
            provider.diagnose(context())
        self.assertEqual(caught.exception.code, AI_FAILED)
        self.assertEqual(caught.exception.final_error.code, MissingFieldError.code)

    def test_invalid_evidence_reference_has_specific_classification(self):
        payload = valid_payload()
        payload["final_conclusion"]["evidence_ids"] = ["E-DOES-NOT-EXIST"]
        with self.assertRaises(InvalidEvidenceReferenceError) as caught:
            validate_diagnostic_response(payload)
        self.assertEqual(
            caught.exception.references,
            ["final_conclusion:E-DOES-NOT-EXIST"],
        )

    def test_every_diagnostic_component_requires_evidence_ids(self):
        payload = valid_payload()
        del payload["recommendations"][0]["evidence_ids"]
        with self.assertRaises(MissingFieldError) as caught:
            validate_diagnostic_response(payload)
        self.assertIn("recommendations[0].evidence_ids", caught.exception.fields)

    def test_openai_compatible_provider_is_offline_by_default(self):
        calls = []

        def transport(request, timeout):
            calls.append((request, timeout))
            raise AssertionError("transport must not be called")

        provider = OpenAICompatibleDiagnosticProvider(
            model="local-model",
            api_key=None,
            transport=transport,
        )
        with self.assertRaises(ProviderConfigurationError):
            provider.diagnose(context())
        self.assertEqual(calls, [])
        outcome = RetryingDiagnosticProvider(provider, max_attempts=3).execute(context())
        self.assertEqual(outcome.status, AI_FAILED)
        self.assertEqual(outcome.attempts, 1)
        self.assertIsInstance(outcome.error, ProviderConfigurationError)

    def test_openai_compatible_transport_can_be_injected_without_network(self):
        captured = {}
        supplied_context = context()

        def transport(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["authorization"] = request.get_header("Authorization")
            captured["request"] = json.loads(request.data.decode("utf-8"))
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    canonical_payload(), ensure_ascii=False
                                )
                            }
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode("utf-8")

        provider = OpenAICompatibleDiagnosticProvider(
            model="local-model",
            base_url="http://127.0.0.1:9999/v1",
            api_key=None,
            timeout=2.5,
            allow_network=True,
            transport=transport,
        )
        result = provider.diagnose(supplied_context)
        self.assertEqual(result.final_conclusion["level"], "需关注")
        self.assertEqual(captured["url"], "http://127.0.0.1:9999/v1/chat/completions")
        self.assertEqual(captured["timeout"], 2.5)
        self.assertIsNone(captured["authorization"])
        self.assertEqual(captured["request"]["model"], "local-model")
        prompt_dir = ROOT / "config" / "prompts"
        expected_system = (prompt_dir / "system.md").read_text(encoding="utf-8")
        expected_schema = json.loads(
            (prompt_dir / "analysis-output.schema.json").read_text(encoding="utf-8")
        )
        messages = captured["request"]["messages"]
        self.assertEqual(messages[0]["content"], expected_system)
        user_prompt = messages[1]["content"]
        self.assertNotIn("{{", user_prompt)
        for expected in (
            '"SYN-0001"',
            '"scene_type":"短语音转写"',
            '["语音识别"]',
            '"text":"abc"',
            '"transcript":"axc"',
            '"name":"CER"',
            '"threshold_version":"runtime-default-v1"',
            '"evidence_id":"E-001"',
        ):
            self.assertIn(expected, user_prompt)
        response_format = captured["request"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(response_format["json_schema"]["schema"], expected_schema)
        self.assertEqual(result.evidence, supplied_context.evidence)
        self.assertEqual(result.prompt_version, "readme-contract-v1.0.0")

    def test_openai_compatible_provider_rejects_rewritten_evidence(self):
        supplied = valid_payload()["evidence"]
        supplied_context = context()
        supplied_context.evidence = supplied
        rewritten = valid_payload()
        rewritten["evidence"][0]["content"] = {
            "reference": "伪造的参考文本",
            "hypothesis": "伪造的输出文本",
        }

        def transport(request, timeout):
            del request, timeout
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(rewritten, ensure_ascii=False)
                        }
                    }
                ]
            }

        provider = OpenAICompatibleDiagnosticProvider(
            model="local-model",
            allow_network=True,
            transport=transport,
        )
        with self.assertRaises(InvalidStructureError):
            provider.diagnose(supplied_context)

        request_payload = provider._request_payload(supplied_context)
        system_prompt = request_payload["messages"][0]["content"]
        self.assertIn("evidence_catalog 中明确提供的事实", system_prompt)
        self.assertIn("不得写成正式验收结果", system_prompt)


if __name__ == "__main__":
    unittest.main()

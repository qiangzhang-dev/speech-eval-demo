from __future__ import annotations

import copy
import unittest

from scripts.acceptance_check import (
    _multistage_errors,
    _threshold_errors,
)


def _row() -> dict:
    stages = [
        ("machine_translation", "translation_exact_match", "你好", "Hello", True),
        ("intent_understanding", "intent_exact_match", "查询天气", "查询天气", True),
        ("slot_filling", "slot_exact_match", {"城市": "北京"}, {"城市": "北京"}, True),
        ("action_execution", "action_exact_match", "查询天气", "查询天气", True),
        ("response_generation", "response_exact_match", "北京今天晴", "北京今天晴", True),
    ]
    stage_metrics = [
        {
            "stage": stage,
            "metric_name": metric_name,
            "value": 1.0 if matched else 0.0,
            "metric_version": "stage-exact-v1",
            "reference": reference,
            "hypothesis": hypothesis,
            "matched": matched,
            "reproducible": True,
        }
        for stage, metric_name, reference, hypothesis, matched in stages
    ]
    evidence = {}
    refs = []
    for index, metric in enumerate(stage_metrics, 1):
        evidence_id = f"EVID-STAGE-{index}"
        refs.append(evidence_id)
        evidence[evidence_id] = {
            "evidence_id": evidence_id,
            "type": "stage_metric_snapshot",
            "source": "computed_stage_metric",
            "location": f"quantitative_metrics.stage_metrics.{index - 1}",
            "content": dict(metric),
        }
    return {
        "样例ID": "MULTI-0001",
        "任务类型": ["语音识别", "机器翻译", "语义理解", "对话生成"],
        "参考文本/标注": {
            "transcript": "你好",
            "translation": "Hello",
            "intent": "查询天气",
            "slots": {"城市": "北京"},
            "expected_action": "查询天气",
            "expected_reply": "北京今天晴",
        },
        "系统输出": {
            "transcript": "你好",
            "translation": "Hello",
            "intent": "查询天气",
            "slots": {"城市": "北京"},
            "action": "查询天气",
            "reply": "北京今天晴",
        },
        "量化指标": {
            "metric_name": "CER",
            "value": 0.0,
            "metric_version": "cer-v1",
            "status": "不判定",
            "candidate_status": "通过",
            "overall_candidate_status": "通过",
            "threshold_version": "1.0.0-candidate.1",
            "threshold_approval_status": "pending_approval",
            "threshold_effective": False,
            "stage_metrics": stage_metrics,
        },
        "最终结论": {"level": "证据不足", "basis": "候选阈值未生效", "evidence_ids": refs},
        "证据片段": list(evidence.values()),
    }


class MultiStageAcceptanceTests(unittest.TestCase):
    def test_declared_stages_require_complete_chain(self) -> None:
        row = _row()
        errors, summary = _multistage_errors(
            row,
            evidence_by_id={item["evidence_id"]: item for item in row["证据片段"]},
            references=set(row["最终结论"]["evidence_ids"]),
        )
        self.assertEqual(errors, [])
        self.assertEqual(summary["declared"], 5)
        self.assertEqual(summary["stage_metrics"], 5)
        self.assertEqual(summary["stage_evidence"], 5)

    def test_label_only_translation_is_rejected(self) -> None:
        row = _row()
        row["参考文本/标注"].pop("translation")
        errors, _ = _multistage_errors(
            row,
            evidence_by_id={item["evidence_id"]: item for item in row["证据片段"]},
            references=set(row["最终结论"]["evidence_ids"]),
        )
        self.assertTrue(any("reference field translation" in error for error in errors))

    def test_stage_evidence_must_match_metric(self) -> None:
        row = _row()
        row["证据片段"][0]["content"]["value"] = 0.0
        errors, _ = _multistage_errors(
            row,
            evidence_by_id={item["evidence_id"]: item for item in row["证据片段"]},
            references=set(row["最终结论"]["evidence_ids"]),
        )
        self.assertTrue(any("machine_translation lacks matching" in error for error in errors))

    def test_pending_threshold_cannot_be_final_decision(self) -> None:
        row = _row()
        row["量化指标"]["status"] = "通过"
        errors, summary = _threshold_errors(row)
        self.assertTrue(any("status='不判定'" in error for error in errors))
        self.assertEqual(summary["pending"], 1)

    def test_effective_threshold_allows_candidate_status(self) -> None:
        row = copy.deepcopy(_row())
        row["量化指标"].update(
            threshold_approval_status="approved",
            threshold_effective=True,
            status="通过",
        )
        row["最终结论"]["level"] = "通过"
        errors, summary = _threshold_errors(row)
        self.assertEqual(errors, [])
        self.assertEqual(summary["pending"], 0)

    def test_overall_candidate_status_cannot_hide_failed_stage(self) -> None:
        row = copy.deepcopy(_row())
        row["量化指标"]["stage_metrics"][0]["candidate_status"] = "失败"
        errors, _ = _threshold_errors(row)
        self.assertTrue(any("overall_candidate_status" in error for error in errors))

        row["量化指标"]["overall_candidate_status"] = "失败"
        errors, _ = _threshold_errors(row)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()

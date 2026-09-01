from __future__ import annotations

import unittest

from speech_eval.batch import BatchProcessor
from speech_eval.manifest import validate_rows
from speech_eval.models import Sample


class LabelingAndMetricEvidenceTests(unittest.TestCase):
    def test_missing_labels_are_suggested_from_explicit_chain(self):
        report = validate_rows(
            [
                {
                    "sample_id": "AUTO-001",
                    "capability_chain": "C02",
                    "input_data": {"value": "把会议内容翻译成英文"},
                    "reference_text": "把会议内容翻译成英文",
                    "system_output": "把会议内容翻译成英文",
                }
            ],
            check_paths=False,
        )
        self.assertTrue(report.ok, report.to_dict())
        sample = report.samples[0]
        self.assertEqual(sample.scene_type, "会议/办公语音翻译")
        self.assertEqual(sample.task_types, ["语音识别", "机器翻译"])
        provenance = sample.input_data["labeling_provenance"]
        self.assertEqual(provenance["source"], "explicit_capability_chain")
        self.assertTrue(provenance["requires_human_confirmation"])
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"AUTO_LABELED_SCENE", "AUTO_LABELED_TASK"},
        )

    def test_ambiguous_unlabeled_sample_stays_invalid(self):
        report = validate_rows(
            [{"sample_id": "AUTO-002", "input_data": {"value": "没有分类信号"}}],
            check_paths=False,
        )
        self.assertFalse(report.ok)
        self.assertEqual(
            {issue.code for issue in report.errors},
            {"MISSING_SCENE", "MISSING_TASK"},
        )

    def test_exact_match_has_metric_snapshot_evidence(self):
        sample = Sample(
            sample_id="MATCH-001",
            scene_type="短语音/录音转写",
            subscene_type="普通话口述",
            task_types=["语音识别"],
            input_data={"type": "text"},
            reference_annotation={"transcript": "打开客厅空调"},
            system_output={"transcript": "打开客厅空调"},
        )
        result = BatchProcessor(object()).evaluate_sample(
            sample, batch_id="MATCH-BATCH", run_id="MATCH-RUN"
        )
        self.assertEqual(result.quantitative_metrics["value"], 0.0)
        self.assertEqual(result.quantitative_metrics["candidate_status"], "通过")
        self.assertEqual(result.quantitative_metrics["overall_candidate_status"], "通过")
        metric_evidence = [
            item for item in result.evidence if item["type"] == "metric_snapshot"
        ]
        self.assertEqual(len(metric_evidence), 1)
        content = metric_evidence[0]["content"]
        self.assertEqual(content["value"], 0.0)
        self.assertEqual(content["overall_candidate_status"], "通过")
        self.assertEqual(content["normalized_reference"], content["normalized_hypothesis"])
        self.assertEqual(len(content["input_summary_sha256"]), 64)
        evidence_id = metric_evidence[0]["evidence_id"]
        self.assertIn(evidence_id, result.quality_diagnosis["evidence_ids"])
        self.assertIn(evidence_id, result.final_conclusion["evidence_ids"])


    def test_stage_failure_is_prioritized_over_cer_pass(self):
        sample = Sample(
            sample_id="STAGE-FAIL-001",
            scene_type="会议/办公语音翻译",
            subscene_type="短句语翻",
            task_types=["语音识别", "机器翻译"],
            input_data={"type": "synthetic"},
            reference_annotation={
                "transcript": "请暂停录音",
                "translation": "Please pause the recording",
            },
            system_output={
                "transcript": "请暂停录音",
                "translation": "pause the recording",
            },
        )
        result = BatchProcessor(
            object(),
            threshold_approval_status="approved",
            threshold_effective=True,
        ).evaluate_sample(sample, batch_id="STAGE-BATCH", run_id="STAGE-RUN")
        self.assertEqual(result.quantitative_metrics["candidate_status"], "通过")
        self.assertEqual(
            result.quantitative_metrics["overall_candidate_status"], "失败"
        )
        self.assertTrue(
            result.quality_diagnosis["text"].startswith("声明的下游阶段候选失败")
        )
        self.assertIn("不能覆盖上述阶段", result.quality_diagnosis["text"])
        self.assertIn("下游阶段未通过", result.impact_assessment["text"])
        self.assertEqual(result.final_conclusion["level"], "失败")
if __name__ == "__main__":
    unittest.main()

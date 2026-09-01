from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from speech_eval.batch import BatchProcessor
from speech_eval.models import Sample
from speech_eval.repository import EvaluationRepository


class CoveragePreservationTests(unittest.TestCase):
    def test_subscene_and_capability_chain_survive_persistence_and_export_shape(self):
        samples = [
            Sample("COV-0001", "短语音/录音转写", ["语音识别"], {"synthetic": True}, "-", {"transcript": "甲"}, {"transcript": "甲"}, "普通话口述"),
            Sample("COV-0002", "会议/办公语音翻译", ["语音识别", "机器翻译"], {"synthetic": True}, "-", {"transcript": "乙"}, {"transcript": "乙"}, "短句语翻"),
            Sample("COV-0003", "车载/语音助手交互", ["语音识别", "语义理解"], {"synthetic": True}, "-", {"transcript": "丙"}, {"transcript": "丙"}, "设备控制"),
        ]
        with tempfile.TemporaryDirectory() as temp:
            with EvaluationRepository(Path(temp) / "evaluation.db") as repository:
                summary = BatchProcessor(repository).run(samples)
                results = repository.list_results(run_id=summary.run_id)
                self.assertEqual(
                    {result.input_data["subscene_type"] for result in results},
                    {"普通话口述", "短句语翻", "设备控制"},
                )
                self.assertEqual(
                    {result.input_data["capability_chain"] for result in results},
                    {"C01", "C02", "C03"},
                )


if __name__ == "__main__":
    unittest.main()

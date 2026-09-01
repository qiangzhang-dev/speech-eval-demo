from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from speech_eval.batch import BatchProcessor
from speech_eval.manifest import load_manifest
from speech_eval.repository import EvaluationRepository
from speech_eval.web import create_server

ROOT = Path(__file__).resolve().parents[1]


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.work = Path(self.temp.name)
        self.database = self.work / "web.db"
        samples = load_manifest(ROOT / "data" / "fixtures" / "manifest.csv")
        with EvaluationRepository(self.database) as repository:
            self.summary = BatchProcessor(repository).run(
                samples,
                batch_id="WEB-BATCH",
                run_id="WEB-RUN",
            )
        self.server = create_server(
            self.database,
            host="127.0.0.1",
            port=0,
            static_dir=ROOT / "web",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, path: str, *, method: str = "GET", payload=None, headers=None):
        data = None
        merged = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            merged.setdefault("Content-Type", "application/json")
        request = Request(self.base + path, data=data, headers=merged, method=method)
        with urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()

    def json_request(self, path: str, **kwargs):
        status, headers, body = self.request(path, **kwargs)
        return status, headers, json.loads(body.decode("utf-8"))

    def test_health_stats_list_detail_and_static_assets(self):
        status, _, health = self.json_request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["offline"])

        _, _, stats = self.json_request("/api/stats")
        self.assertEqual(stats["total_results"], 6)
        self.assertEqual(stats["status_counts"]["COMPLETED"], 6)

        _, _, listing = self.json_request(
            "/api/results?limit=2&scene_type="
            "%E7%9F%AD%E8%AF%AD%E9%9F%B3%2F%E5%BD%95%E9%9F%B3%E8%BD%AC%E5%86%99"
        )
        self.assertEqual(listing["limit"], 2)
        self.assertGreaterEqual(listing["total"], 1)
        sample_id = listing["items"][0]["样例ID"]

        _, _, detail = self.json_request(f"/api/results/{sample_id}?run_id=WEB-RUN")
        self.assertEqual(detail["result"]["样例ID"], sample_id)
        self.assertEqual(detail["metadata"]["run_id"], "WEB-RUN")
        self.assertTrue(detail["logs"])

        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("语音智能体验评测工作台", body.decode("utf-8"))
        self.assertIn("本地合成工程验证页面", body.decode("utf-8"))
        self.assertIn("formal_acceptance_claim=false", body.decode("utf-8"))
        self.assertIn("不代表真实业务效果或正式验收结论", body.decode("utf-8"))
        status, _, body = self.request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("downloadResults", body.decode("utf-8"))

    def test_filtered_json_and_csv_downloads(self):
        status, headers, body = self.request("/api/export?format=json&run_id=WEB-RUN")
        self.assertEqual(status, 200)
        self.assertIn("attachment;", headers["Content-Disposition"])
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["count"], 6)
        self.assertEqual(set(payload["items"][0]), {
            "样例ID", "场景类型", "任务类型", "输入数据", "音频信息",
            "参考文本/标注", "系统输出", "量化指标", "质量诊断",
            "证据片段", "影响评估", "优化建议", "人工修订", "最终结论",
        })

        status, headers, body = self.request("/api/results/download?format=csv&run_id=WEB-RUN")
        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        self.assertIn("evaluation-results.csv", headers["Content-Disposition"])

        status, headers, body = self.request(
            "/api/results/SYN-0001/download?format=json&run_id=WEB-RUN"
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode("utf-8"))["count"], 1)
        self.assertIn("SYN-0001.json", headers["Content-Disposition"])

    def test_revision_updates_result_and_preserves_audit(self):
        _, _, detail = self.json_request("/api/results/SYN-0001?run_id=WEB-RUN")
        self.assertEqual(detail["result"]["人工修订"], "-")
        evidence_id = detail["result"]["证据片段"][0]["evidence_id"]
        diagnosis = {"text": "Web人工复核", "evidence_ids": [evidence_id]}
        status, _, response = self.json_request(
            "/api/revisions",
            method="POST",
            payload={
                "sample_id": "SYN-0001",
                "run_id": "WEB-RUN",
                "editor": "web-reviewer",
                "reason": "Web API audit test",
                "changes": {"质量诊断": diagnosis},
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["revision_count"], 1)
        self.assertEqual(response["result"]["质量诊断"], diagnosis)
        self.assertEqual(response["result"]["人工修订"]["editor"], "web-reviewer")
        self.assertEqual(response["result"]["人工修订"]["before"], detail["result"]["质量诊断"])
        self.assertEqual(response["result"]["人工修订"]["after"], diagnosis)

        _, _, refreshed = self.json_request("/api/results/SYN-0001?run_id=WEB-RUN")
        self.assertEqual(refreshed["result"]["质量诊断"], diagnosis)
        self.assertEqual(len(refreshed["revisions"]), 1)
        self.assertEqual(refreshed["revisions"][0]["editor"], "web-reviewer")
        self.assertTrue(any(item["stage"] == "HUMAN_REVISION" for item in refreshed["logs"]))

    def test_revision_rejects_objective_fields_and_dangling_evidence(self):
        for field_name, value in (
            ("参考文本/标注", {"transcript": "篡改参考"}),
            ("系统输出", {"transcript": "篡改输出"}),
            ("量化指标", {"value": 0.0}),
            ("证据片段", []),
            ("最终结论", {"level": "通过", "evidence_ids": []}),
        ):
            with self.subTest(field_name=field_name), self.assertRaises(HTTPError) as caught:
                self.request(
                    "/api/revisions",
                    method="POST",
                    payload={
                        "sample_id": "SYN-0001",
                        "run_id": "WEB-RUN",
                        "editor": "web-reviewer",
                        "reason": "attempt objective-field rewrite",
                        "changes": {field_name: value},
                    },
                )
            self.assertEqual(caught.exception.code, 400)
            error = json.loads(caught.exception.read().decode("utf-8"))
            self.assertEqual(error["error"]["code"], "invalid_request")

        _, _, detail = self.json_request("/api/results/SYN-0001?run_id=WEB-RUN")
        invalid_diagnosis = {
            "text": "悬空证据引用",
            "evidence_ids": ["EVIDENCE-DOES-NOT-EXIST"],
        }
        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/api/revisions",
                method="POST",
                payload={
                    "sample_id": "SYN-0001",
                    "run_id": "WEB-RUN",
                    "editor": "web-reviewer",
                    "reason": "attempt dangling evidence reference",
                    "changes": {"质量诊断": invalid_diagnosis},
                },
            )
        self.assertEqual(caught.exception.code, 400)
        refreshed = self.json_request("/api/results/SYN-0001?run_id=WEB-RUN")[2]
        self.assertEqual(refreshed["result"], detail["result"])
        self.assertEqual(refreshed["revisions"], [])

    def test_label_correction_has_independent_endpoint_and_invalidates_derived_result(self):
        _, _, detail = self.json_request("/api/results/SYN-0001?run_id=WEB-RUN")
        original_subscene = detail["result"]["输入数据"]["subscene_type"]
        status, _, response = self.json_request(
            "/api/label-corrections",
            method="POST",
            payload={
                "sample_id": "SYN-0001",
                "run_id": "WEB-RUN",
                "editor": "web-label-reviewer",
                "reason": "纠正子场景标签并要求重跑派生结果",
                "changes": {"子场景": "噪声/数字英文混合"},
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(response["label_correction"])
        self.assertTrue(response["derived_invalidated"])
        self.assertEqual(response["revision_count"], 1)
        self.assertEqual(response["result"]["输入数据"]["subscene_type"], "噪声/数字英文混合")
        self.assertEqual(response["result"]["最终结论"]["level"], "证据不足")
        self.assertEqual(response["result"]["量化指标"]["status"], "不判定")
        self.assertIn(response["result"]["量化指标"]["candidate_status"], {"通过", "需关注", "失败"})
        self.assertEqual(response["revisions"][0]["field_name"], "subscene_type")
        self.assertEqual(response["revisions"][0]["before"], original_subscene)
        self.assertEqual(response["revisions"][0]["after"], "噪声/数字英文混合")

        with self.assertRaises(HTTPError) as caught:
            self.request(
                "/api/revisions",
                method="POST",
                payload={
                    "sample_id": "SYN-0001",
                    "run_id": "WEB-RUN",
                    "editor": "web-label-reviewer",
                    "reason": "must use independent endpoint",
                    "changes": {"场景类型": "车载/语音助手交互"},
                },
            )
        self.assertEqual(caught.exception.code, 400)

    def test_invalid_requests_have_stable_errors(self):
        with self.assertRaises(HTTPError) as caught:
            self.request("/api/export?format=xlsx")
        self.assertEqual(caught.exception.code, 400)
        error = json.loads(caught.exception.read().decode("utf-8"))
        self.assertEqual(error["error"]["code"], "invalid_request")

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/results", method="POST", payload={})
        self.assertEqual(caught.exception.code, 405)

        with self.assertRaises(HTTPError) as caught:
            self.request("/api/results/DOES-NOT-EXIST")
        self.assertEqual(caught.exception.code, 404)


if __name__ == "__main__":
    unittest.main()


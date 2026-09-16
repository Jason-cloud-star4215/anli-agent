"""任务恢复：检查点里要存一份前端视图，重启服务后还能取回上次结果。"""

import json
import tempfile
import unittest
from pathlib import Path

import anli.web as web
from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def run_task(task_id: str = "resume-1"):
    orchestrator = Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(DATA / "cases.seed.json"),
        state_store=StateStore(),
    )
    record = orchestrator.run(
        task_id=task_id,
        role=Role.CLAIMANT,
        profile=car_accident_profile(),
        expectation=car_accident_expectation(),
    )
    return record, orchestrator.render_report(record)


class ViewSnapshotTest(unittest.TestCase):
    def test_view_is_json_serializable(self):
        record, report = run_task()
        view = web.build_view(record, report)
        text = json.dumps(view, ensure_ascii=False)
        self.assertIn("assessment", view)
        self.assertIn("understanding", view)
        self.assertEqual(view["role"], "claimant")
        self.assertTrue(text)

    def test_checkpoint_roundtrip_after_restart(self):
        """模拟服务重启：内存里没有任务，但检查点能取回完整视图。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record, report = run_task("resume-2")
            view = web.build_view(record, report)
            StateStore(root / "state").save(
                "resume-2", record.state, {"view": view}
            )

            original_root = web.ROOT
            web.ROOT = root
            try:
                restored = web._load_checkpoint("resume-2")
            finally:
                web.ROOT = original_root

            self.assertIsNotNone(restored)
            self.assertEqual(restored["state"], record.state.value)
            self.assertEqual(restored["assessment"]["sample_size"], record.assessment.sample_size)
            self.assertIn("一句话结论", restored["summary"])
            self.assertTrue(restored["report"])

    def test_missing_checkpoint_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_root = web.ROOT
            web.ROOT = Path(tmp)
            try:
                self.assertIsNone(web._load_checkpoint("nope"))
            finally:
                web.ROOT = original_root

    def test_task_id_must_be_safe(self):
        """任务号会被拼进文件路径，必须挡掉穿越字符。"""

        self.assertTrue(web.TASK_ID_PATTERN.match("a1b2c3d4e5f6"))
        for bad in ["../../etc/passwd", "..\\win.ini", "a/b", "", "x" * 41]:
            self.assertIsNone(web.TASK_ID_PATTERN.match(bad), bad)

    def test_view_exposes_llm_status(self):
        """掉线提示要能传到前端：视图必须带 llm_status 与说明文字。"""

        record, report = run_task("resume-llm")
        record.extra["llm_status"] = "offline"
        record.extra["llm_status_text"] = "未连接大模型（未配置密钥），本次为规则版分析"
        view = web.build_view(record, report)
        self.assertEqual(view["llm_status"], "offline")
        self.assertIn("规则版", view["llm_status_text"])
        record.extra["llm_status"] = "online"
        self.assertEqual(web.build_view(record, report)["llm_status"], "online")

    def test_view_exposes_rounds_for_the_chat_area(self):
        """对话区靠 rounds 渲染：视图必须带轮次记录与上限。"""

        record, report = run_task("resume-rounds")
        record.extra["rounds"] = [
            {"round": 1, "message": "案情描述", "files": ["医疗费发票.jpg"], "at": "2026-09-16T10:00:00+00:00"},
            {"round": 2, "message": "补交误工证明", "files": [], "at": "2026-09-16T10:05:00+00:00"},
        ]
        view = web.build_view(record, report)
        self.assertEqual(len(view["rounds"]), 2)
        self.assertEqual(view["rounds"][1]["round"], 2)
        self.assertEqual(view["max_rounds"], web.MAX_ROUNDS)


class LlmStatusTest(unittest.TestCase):
    """模型状态判定：不能把「提前结束没调模型」说成「调用全部失败」。"""

    def test_offline_when_not_configured(self):
        status, text = web.llm_status_for(False, 0, 0, False)
        self.assertEqual(status, "offline")
        self.assertIn("未连接大模型", text)

    def test_skipped_when_flow_ends_before_calling(self):
        """刑事案件、信息不足：一次都没调过模型，是 skipped 而不是 degraded。"""

        for stopped in (True, False):
            status, text = web.llm_status_for(True, 0, 0, stopped)
            self.assertEqual(status, "skipped")
            self.assertIn("未使用大模型", text)

    def test_degraded_only_when_calls_actually_failed(self):
        status, text = web.llm_status_for(True, 0, 5, False)
        self.assertEqual(status, "degraded")
        self.assertIn("全部失败", text)
        status, text = web.llm_status_for(True, 5, 2, False)
        self.assertEqual(status, "degraded")
        self.assertIn("2 次调用失败", text)

    def test_online_when_all_calls_succeed(self):
        status, text = web.llm_status_for(True, 8, 0, False)
        self.assertEqual(status, "online")
        self.assertIn("调用 8 次", text)

    def test_health_prefers_last_run_result(self):
        """最近一次真实运行失败后，健康检查不能还报「已连接」。"""

        original = dict(web.LAST_LLM_RUN)
        try:
            web.LAST_LLM_RUN.clear()
            web.LAST_LLM_RUN.update({"status": "degraded", "text": "调用失败"})
            payload = web.health_payload()
            self.assertEqual(payload["status"], "degraded")
            self.assertTrue(payload["configured"])
            web.LAST_LLM_RUN.update({"status": "skipped", "text": "提前结束"})
            self.assertEqual(web.health_payload()["status"], "skipped")
        finally:
            web.LAST_LLM_RUN.clear()
            web.LAST_LLM_RUN.update(original)


if __name__ == "__main__":
    unittest.main()

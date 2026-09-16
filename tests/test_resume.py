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


if __name__ == "__main__":
    unittest.main()

"""过程可见性：事件粒度、终止事件，以及「缺金额」与「缺证据」的分流。

对应 PROGRESS.md 的 R1（检索过程可见）与 R2（辩论过程逐条呈现）：
过程流能不能做出来，取决于后端有没有把「正在查什么、命中什么、核验结果如何」
拆成独立事件推出去，而不是只推一个「检索完成」。
"""

import unittest
from pathlib import Path

from anli.intake import build_profile
from anli.models import Role, TaskState
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_description
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def run_task(profile, expectation: float = 12000.0, task_id: str = "stream"):
    orchestrator = Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(DATA / "cases.seed.json"),
        state_store=StateStore(),
    )
    events = []
    orchestrator.subscribe(events.append)
    record = orchestrator.run(
        task_id=task_id, role=Role.CLAIMANT, profile=profile, expectation=expectation
    )
    return record, events


class StreamVisibilityTest(unittest.TestCase):
    def test_retrieval_is_pushed_item_by_item(self):
        """检索不能只推一个「完成」，要能看出在查什么、命中了哪几条。"""

        profile = build_profile(
            car_accident_description(), ["事故认定书.jpg", "医疗费发票.jpg"]
        )
        _, events = run_task(profile)
        kinds = [event.type for event in events]
        self.assertIn("retrieval_query", kinds)
        self.assertIn("retrieval_hit", kinds)

        queries = [event for event in events if event.type == "retrieval_query"]
        self.assertTrue(all(event.payload.get("query") for event in queries))

        hits = [event for event in events if event.type == "retrieval_hit"]
        self.assertTrue(hits)
        self.assertTrue(all(event.payload.get("label") for event in hits))
        # 法条与类案两条检索都要有命中记录，前端才能分别显示来源
        self.assertEqual({event.payload.get("scope") for event in hits}, {"law", "case"})

    def test_debate_agents_emit_incremental_messages(self):
        """主张方与抗辩方要逐条推发言，而不是只在结束时给一句摘要。"""

        profile = build_profile(
            car_accident_description(), ["事故认定书.jpg", "医疗费发票.jpg"]
        )
        _, events = run_task(profile)
        deltas = [event for event in events if event.type == "agent_delta"]
        self.assertTrue(deltas)
        speakers = {event.payload.get("agent") for event in deltas}
        self.assertIn("claimant", speakers)
        self.assertIn("defendant", speakers)
        self.assertIn("judge", speakers)
        self.assertTrue(all(event.payload.get("text") for event in deltas))

    def test_every_event_carries_task_id_and_citations_verified_once(self):
        """事件必须挂在任务上；同一条引用只核验一次。"""

        profile = build_profile(car_accident_description(), ["事故认定书.jpg"])
        record, events = run_task(profile)
        self.assertEqual([event.type for event in events if not event.task_id], [])
        verified = [event for event in events if event.type == "citation_verified"]
        self.assertEqual(len(verified), len(record.citations))
        self.assertTrue(all("grade" in event.payload for event in verified))

    def test_needs_info_run_still_signals_completion(self):
        """走「需要补充信息」分支时也必须发终止事件，否则前端一直转圈。"""

        profile = build_profile("我被人撞了，还没想好要多少钱。")
        record, events = run_task(profile, expectation=0)
        self.assertEqual(record.state, TaskState.NEEDS_INFO)
        self.assertIn("task_completed", [event.type for event in events])

    def test_amounts_without_evidence_go_to_evidence_chain(self):
        """金额写了但一项证据都没有时，要列补证清单，而不是回头要金额。"""

        profile = build_profile(
            "6月10号我骑电动车被小轿车撞了，交警认定对方主要责任。"
            "门诊医疗费3200元，误工费1800元。我想要对方赔我12000元。"
        )
        record, _ = run_task(profile)
        self.assertEqual(record.state, TaskState.NEEDS_EVIDENCE)
        self.assertTrue(record.extra.get("evidence_request"))
        # 不能把「缺证据」错报成「没写金额」
        self.assertNotIn(
            "还没有识别到任何具体的损失金额", record.extra.get("summary", "")
        )
        self.assertIn("一句话结论", record.extra.get("summary", ""))


if __name__ == "__main__":
    unittest.main()

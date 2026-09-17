"""规划决策测试：模型可以参与，但不能越权；失败必须能回退。"""

import unittest
from pathlib import Path

from anli.events import EventBus
from anli.models import Role, Severity, CriticFinding
from anli.orchestrator import Orchestrator
from anli.planner import (
    ACTION_KEYS,
    allowed_keywords,
    plan_research,
    rank_actions,
    render_actions,
    rule_keywords,
    should_continue,
)
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


class _FakeLLM:
    available = True
    calls = 0
    failures = 0

    def __init__(self, payload: dict):
        self.payload = payload
        self.prompts: list[tuple[str, str]] = []

    def chat_json(self, system: str, user: str, max_tokens: int = 900) -> dict:
        self.prompts.append((system, user))
        return dict(self.payload)


class _BrokenLLM(_FakeLLM):
    def chat_json(self, system: str, user: str, max_tokens: int = 900) -> dict:
        raise RuntimeError("模型挂了")


def _finding(severity: Severity, detail: str = "证据缺口", resolved: bool = False) -> CriticFinding:
    return CriticFinding(
        category="证据缺口",
        severity=severity,
        detail=detail,
        basis="测试",
        resolved=resolved,
    )


class PlanResearchTest(unittest.TestCase):
    def setUp(self):
        self.profile = car_accident_profile()

    def test_rules_when_model_missing(self):
        decision = plan_research(self.profile, llm=None)
        self.assertEqual(decision.source, "rules")
        self.assertEqual(decision.payload["keywords"], rule_keywords(self.profile))

    def test_llm_keywords_are_used(self):
        decision = plan_research(self.profile, llm=_FakeLLM({"keywords": ["医疗费", "误工费"]}))
        self.assertEqual(decision.source, "llm")
        self.assertIn("医疗费", decision.payload["keywords"])
        self.assertIn("误工费", decision.payload["keywords"])

    def test_out_of_scope_keywords_are_dropped(self):
        decision = plan_research(
            self.profile,
            llm=_FakeLLM({"keywords": ["合同诈骗", "医疗费", "刑法第二百六十六条"]}),
        )
        self.assertNotIn("合同诈骗", decision.payload["keywords"])
        self.assertNotIn("刑法第二百六十六条", decision.payload["keywords"])
        self.assertEqual(len(decision.payload["dropped"]), 2)
        self.assertIn("越界丢弃", decision.detail)

    def test_cause_always_present(self):
        decision = plan_research(self.profile, llm=_FakeLLM({"keywords": ["完全不相关的词"]}))
        self.assertIn(self.profile.cause, decision.payload["keywords"])

    def test_keyword_count_is_capped(self):
        many = list(allowed_keywords(self.profile))[:20]
        decision = plan_research(self.profile, llm=_FakeLLM({"keywords": many}))
        self.assertLessEqual(len(decision.payload["keywords"]), 6)

    def test_broken_llm_falls_back(self):
        decision = plan_research(self.profile, llm=_BrokenLLM({}))
        self.assertEqual(decision.source, "rules")
        self.assertIn("回退", decision.detail)

    def test_allowed_keywords_are_traceable(self):
        """白名单里的词必须能在案由、争议焦点、赔偿项目或证据词表里找到出处。"""

        allowed = allowed_keywords(self.profile)
        self.assertIn(self.profile.cause, allowed)
        self.assertIn("医疗费票据", allowed)
        self.assertIn("医疗费", allowed)


class ShouldContinueTest(unittest.TestCase):
    def test_no_open_findings_stops(self):
        decision = should_continue([], 1, 2, llm=_FakeLLM({"continue": True}))
        self.assertFalse(decision.payload["continue"])

    def test_resolved_findings_do_not_continue(self):
        findings = [_finding(Severity.HIGH, resolved=True)]
        decision = should_continue(findings, 1, 2)
        self.assertFalse(decision.payload["continue"])

    def test_round_limit_is_hard(self):
        findings = [_finding(Severity.HIGH)]
        decision = should_continue(findings, 2, 2, llm=_FakeLLM({"continue": True}))
        self.assertFalse(decision.payload["continue"])
        self.assertIn("上限", decision.detail)

    def test_rules_continue_when_open_items(self):
        decision = should_continue([_finding(Severity.MEDIUM)], 1, 2)
        self.assertTrue(decision.payload["continue"])
        self.assertEqual(decision.source, "rules")

    def test_llm_can_stop_on_medium_only(self):
        decision = should_continue(
            [_finding(Severity.MEDIUM)],
            1,
            2,
            llm=_FakeLLM({"continue": False, "reason": "措辞问题不影响结论"}),
        )
        self.assertFalse(decision.payload["continue"])
        self.assertEqual(decision.source, "llm")

    def test_llm_cannot_stop_while_high_issue_open(self):
        """安全侧收口：高危问题没解决，不接受模型提前结束。"""

        decision = should_continue(
            [_finding(Severity.HIGH), _finding(Severity.MEDIUM, "另一个问题")],
            1,
            2,
            llm=_FakeLLM({"continue": False, "reason": "我觉得可以了"}),
        )
        self.assertTrue(decision.payload["continue"])
        self.assertEqual(decision.source, "rules")
        self.assertIn("高危", decision.detail)

    def test_broken_llm_keeps_rules_behaviour(self):
        decision = should_continue([_finding(Severity.HIGH)], 1, 2, llm=_BrokenLLM({}))
        self.assertTrue(decision.payload["continue"])
        self.assertEqual(decision.source, "rules")


class RankActionsTest(unittest.TestCase):
    def setUp(self):
        self.profile = car_accident_profile()

    def test_rules_use_default_order(self):
        decision = rank_actions(self.profile, None)
        self.assertEqual(decision.source, "rules")
        self.assertEqual(decision.payload["order"], ACTION_KEYS)

    def test_llm_order_is_respected(self):
        decision = rank_actions(
            self.profile, None, llm=_FakeLLM({"order": ["litigation", "negotiate", "complaint"]})
        )
        self.assertEqual(decision.payload["order"], ["litigation", "negotiate", "complaint"])
        self.assertIn("仲裁/诉讼 → 协商", decision.detail)

    def test_llm_cannot_add_new_paths(self):
        decision = rank_actions(
            self.profile,
            None,
            llm=_FakeLLM({"order": ["negotiate", "上访", "litigation"]}),
        )
        self.assertNotIn("上访", decision.payload["order"])
        self.assertEqual(sorted(decision.payload["order"]), sorted(ACTION_KEYS))

    def test_incomplete_order_is_completed(self):
        decision = rank_actions(self.profile, None, llm=_FakeLLM({"order": ["litigation"]}))
        self.assertEqual(len(decision.payload["order"]), len(ACTION_KEYS))
        self.assertEqual(decision.payload["order"][0], "litigation")

    def test_render_keeps_the_same_sentences(self):
        body = render_actions(rank_actions(self.profile, None, llm=_FakeLLM({"order": ACTION_KEYS[::-1]})))
        self.assertIn("协商：成本最低", body)
        self.assertIn("投诉/调解", body)
        self.assertIn("仲裁/诉讼", body)
        self.assertIn("不构成法律意见", body)
        # 顺序变了，但条数与编号连续
        self.assertIn("1. 仲裁/诉讼", body)
        self.assertIn("3. 协商", body)

    def test_broken_llm_falls_back_to_default(self):
        decision = rank_actions(self.profile, None, llm=_BrokenLLM({}))
        self.assertEqual(decision.source, "rules")
        self.assertEqual(decision.payload["order"], ACTION_KEYS)


class PlanningEventTest(unittest.TestCase):
    """决策必须留痕：既进交付物，也进前端事件流。"""

    def _run(self):
        process = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
            bus=EventBus(),
        )
        events = []
        process.subscribe(events.append)
        record = process.run(
            task_id="plan-1",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        return process, record, events

    def test_plan_decided_events_are_emitted(self):
        _, _, events = self._run()
        kinds = [event.payload["kind"] for event in events if event.type == "plan_decided"]
        self.assertIn("research_keywords", kinds)
        self.assertIn("action_order", kinds)
        for event in events:
            if event.type == "plan_decided":
                self.assertTrue(event.task_id)
                self.assertIn(event.payload["source"], {"llm", "rules"})

    def test_decisions_are_recorded_in_record(self):
        _, record, _ = self._run()
        decisions = record.extra["planning_decisions"]
        self.assertTrue(decisions)
        self.assertTrue(all("detail" in item for item in decisions))

    def test_decisions_are_written_into_artifacts(self):
        _, record, _ = self._run()
        bodies = {artifact.kind: artifact.body for artifact in record.artifacts}
        self.assertIn("检索决策留痕", bodies["research"])
        self.assertIn("轮次决策留痕", bodies["revision"])
        self.assertIn("排序依据", bodies["actions"])

    def test_no_llm_means_all_decisions_are_rules(self):
        _, record, _ = self._run()
        self.assertTrue(
            all(item["source"] == "rules" for item in record.extra["planning_decisions"])
        )


if __name__ == "__main__":
    unittest.main()

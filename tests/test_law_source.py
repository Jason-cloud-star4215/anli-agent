"""官方库核对：真实外部工具调用的接线、缓存、降级与红队拦截。

这些用例全部用假的抓取函数，**不打网络**，保证离线与 CI 下可复现。
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from anli.models import LawSourceCheck, Role
from anli.orchestrator import Orchestrator
from anli.retrieval_flk import LawSourceChecker, normalize_title, pick_best_match
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def fake_rows(title="中华人民共和国民法典", publish="2020-05-28", effective="2021-01-01"):
    return [
        {
            "bbbs": "abc123",
            "title": title,
            "publish_date": publish,
            "effective_date": effective,
            "organ": "全国人民代表大会",
            "law_type": "法律",
            "detail_url": "https://flk.npc.gov.cn/detail2.html?abc123",
        }
    ]


class FakeChecker:
    """鸭子类型的核对器：给编排层测试用，可控且不发网络请求。"""

    def __init__(self, found: bool = True):
        self.found = found
        self.names: list[str] = []

    def check(self, law_name: str) -> LawSourceCheck:
        self.names.append(law_name)
        if not self.found:
            return LawSourceCheck(law_name=law_name)
        return LawSourceCheck(
            law_name=law_name,
            found=True,
            official_title=law_name,
            publish_date="2020-05-28",
            effective_date="2021-01-01",
            organ="全国人民代表大会",
            detail_url="https://flk.npc.gov.cn/detail2.html?abc123",
            fetched_at="2026-09-16T00:00:00+00:00",
        )


class LawSourceCheckerTest(unittest.TestCase):
    def test_title_normalization_and_matching(self):
        self.assertEqual(normalize_title("《中华人民共和国民法典》"), "民法典")
        rows = fake_rows()
        self.assertIsNotNone(pick_best_match("中华人民共和国民法典", rows))
        self.assertIsNotNone(pick_best_match("民法典", rows))
        self.assertIsNone(pick_best_match("中华人民共和国刑法", rows))

    def test_check_reads_official_fields(self):
        checker = LawSourceChecker(fetch=lambda name, page_size=5, timeout=6: fake_rows())
        result = checker.check("中华人民共和国民法典")
        self.assertTrue(result.found)
        self.assertEqual(result.publish_date, "2020-05-28")
        self.assertEqual(result.effective_date, "2021-01-01")
        self.assertIn("官方库已收录", result.summary)

    def test_cache_avoids_second_network_call(self):
        calls = {"count": 0}

        def fetch(name, page_size=5, timeout=6):
            calls["count"] += 1
            return fake_rows()

        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "law_sources.json"
            first = LawSourceChecker(cache_path=cache, fetch=fetch)
            first.check("中华人民共和国民法典")
            second = LawSourceChecker(cache_path=cache, fetch=fetch)
            cached = second.check("中华人民共和国民法典")
        self.assertEqual(calls["count"], 1)
        self.assertTrue(cached.from_cache)
        self.assertTrue(cached.found)

    def test_stale_cache_is_refreshed(self):
        stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "law_sources.json"
            cache.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": {
                            "民法典": {
                                "found": True,
                                "official_title": "中华人民共和国民法典",
                                "publish_date": "1999-01-01",
                                "fetched_at": stale,
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            checker = LawSourceChecker(
                cache_path=cache, fetch=lambda name, page_size=5, timeout=6: fake_rows()
            )
            result = checker.check("中华人民共和国民法典")
        self.assertFalse(result.from_cache)
        self.assertEqual(result.publish_date, "2020-05-28")

    def test_network_failure_degrades_instead_of_raising(self):
        def boom(name, page_size=5, timeout=6):
            raise OSError("network down")

        checker = LawSourceChecker(fetch=boom)
        result = checker.check("中华人民共和国民法典")
        self.assertFalse(result.found)
        self.assertTrue(result.error)

    def test_disabled_checker_reports_reason(self):
        result = LawSourceChecker(enabled=False).check("中华人民共和国民法典")
        self.assertFalse(result.found)
        self.assertIn("未启用", result.error)


class OfficialCheckWiringTest(unittest.TestCase):
    def build(self, checker):
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=CaseStore.load(DATA / "cases.seed.json"),
            state_store=StateStore(),
            law_checker=checker,
        )
        events = []
        orchestrator.subscribe(events.append)
        record = orchestrator.run(
            task_id="law-source",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        return record, events

    def test_found_is_streamed_and_reported(self):
        checker = FakeChecker(found=True)
        record, events = self.build(checker)
        self.assertTrue(checker.names)
        hits = [event for event in events if event.payload.get("scope") == "law_web"]
        self.assertTrue(hits)
        self.assertTrue(any("已收录" in (event.payload.get("detail") or "") for event in hits))
        research = next(item for item in record.artifacts if item.kind == "research")
        self.assertIn("官方库核对", research.body)
        self.assertIn("公布 2020-05-28", research.body)

    def test_not_found_becomes_a_high_severity_finding(self):
        record, _ = self.build(FakeChecker(found=False))
        categories = [item.category for item in record.findings]
        self.assertIn("法条来源存疑", categories)
        finding = next(item for item in record.findings if item.category == "法条来源存疑")
        self.assertEqual(finding.severity.value, "high")

    def test_disabled_checker_keeps_the_report_clean(self):
        record, events = self.build(None)
        self.assertEqual(
            [event for event in events if event.payload.get("scope") == "law_web"], []
        )
        research = next(item for item in record.artifacts if item.kind == "research")
        self.assertNotIn("官方库核对", research.body)
        self.assertNotIn("法条来源存疑", [item.category for item in record.findings])


if __name__ == "__main__":
    unittest.main()

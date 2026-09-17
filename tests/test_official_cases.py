"""真实官方案例缓存：加载、检索、溯源字段，以及与金额样本的隔离。"""

import json
import tempfile
import unittest
from pathlib import Path

from anli.models import CaseRecord, Role
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"
OFFICIAL = DATA / "cases.official.json"


def official_case(
    case_id: str, dispute: str, holding: str, case_type: str = "消费维权"
) -> CaseRecord:
    return CaseRecord(
        case_id=case_id,
        court="最高人民法院",
        year=2026,
        region="全国",
        case_type=case_type,
        dispute=dispute,
        holding=holding,
        amounts={},
        evidence_points=["欺诈", "惩罚性赔偿"],
        source_url="https://www.court.gov.cn/shenpan/xiangqing/1.html",
        synthetic=False,
        source_name="最高人民法院官网·指导性案例",
        case_no="（2023）沪0113民初23152号",
        fetched_at="2026-09-16T13:48:56+00:00",
        keywords=["欺诈", "惩罚性赔偿", "消费者"],
    )


class OfficialCaseStoreTest(unittest.TestCase):
    def test_search_matches_by_keyword(self):
        store = CaseStore([], [official_case("指导性案例264号", "网络购物欺诈案", "构成欺诈应三倍赔偿")])
        hits = store.search_official(["欺诈", "惩罚性赔偿"], case_type="消费维权")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].case_id, "指导性案例264号")

    def test_cross_case_type_is_never_referenced(self):
        """案由不符一律不引用：关键词命中也不行。

        回归背景：关键词「机动车」曾把「机动车检测公司垄断纠纷」
        拉进交通事故报告的真实案例参照，对专业性直接失分。
        """

        monopoly = official_case(
            "反垄断典型案例·案例三",
            "机动车检测公司垄断纠纷",
            "民事案件审理中应注意识别垄断问题",
            case_type="商业垄断",
        )
        store = CaseStore([], [monopoly])
        hits = store.search_official(
            ["机动车", "交通事故", "赔偿"], case_type="交通事故人身损害赔偿"
        )
        self.assertEqual(hits, [])

    def test_no_match_returns_empty_instead_of_guessing(self):
        store = CaseStore([], [official_case("指导性案例264号", "网络购物欺诈案", "构成欺诈")])
        self.assertEqual(store.search_official(["离婚", "抚养"], case_type="婚姻家事"), [])

    def test_cache_file_carries_provenance(self):
        """缓存里每条都必须是真实来源：带链接、来源名、抓取时间，且没有刑事案件。"""

        self.assertTrue(OFFICIAL.exists(), "缺少官方案例缓存，请运行 tools/fetch_cases.py")
        records = json.loads(OFFICIAL.read_text(encoding="utf-8"))
        self.assertTrue(records)
        for item in records:
            self.assertFalse(item["synthetic"])
            self.assertTrue(item["source_url"].startswith("https://www.court.gov.cn/"))
            self.assertTrue(item["source_name"])
            self.assertTrue(item["fetched_at"])
            self.assertNotIn("刑事", item["holding"])
        # 金额样本必须为空：官方案例只做裁判要点参照，不参与金额区间
        self.assertTrue(all(item["amounts"] == {} for item in records))

    def test_unknown_fields_are_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / "seed.json"
            official = Path(tmp) / "official.json"
            seed.write_text("[]", encoding="utf-8")
            official.write_text(
                json.dumps(
                    [{"case_id": "指导性案例1号", "court": "最高人民法院", "year": 2026,
                      "region": "全国", "case_type": "合同纠纷", "dispute": "示例",
                      "holding": "示例要点", "title": "抓取工具多写的字段"}],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            store = CaseStore.load(seed, official)
        self.assertEqual(len(store.official), 1)


class CacheQualityTest(unittest.TestCase):
    def test_no_traffic_mislabeled_records(self):
        """缓存里不允许存在「挂着交通事故案由、内容却与事故无关」的记录。

        回归背景：反垄断案例因正文含「机动车检测」曾被误标为交通事故，
        出现在交通事故报告的真实案例参照里，对专业性直接失分。
        """

        import json

        records = json.loads(OFFICIAL.read_text(encoding="utf-8"))
        traffic = [item for item in records if item.get("case_type") == "交通事故人身损害赔偿"]
        for item in traffic:
            text = "".join(
                str(item.get(key) or "") for key in ("title", "dispute", "holding")
            ) + "".join(item.get("keywords") or [])
            self.assertTrue(
                any(k in text for k in ["交通事故", "交强险", "车祸", "道路交通事故"]),
                f"疑似误标：{item.get('case_id')}",
            )

    def test_guess_case_type_antitrust_is_not_traffic(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from tools.fetch_cases import guess_case_type

        self.assertEqual(
            guess_case_type("机动车检测公司垄断纠纷 反垄断典型案例"), "其他民事纠纷"
        )
        self.assertEqual(
            guess_case_type("机动车交通事故责任纠纷 交强险赔付"), "交通事故人身损害赔偿"
        )


class OfficialCaseWiringTest(unittest.TestCase):
    def build(self, store: CaseStore):
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=store,
            state_store=StateStore(),
        )
        events = []
        orchestrator.subscribe(events.append)
        record = orchestrator.run(
            task_id="official",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        return record, events

    def test_hits_are_streamed_and_reported(self):
        store = CaseStore(
            [],
            [
                official_case(
                    "指导性案例264号",
                    "交通事故赔偿纠纷",
                    "按责任比例认定损失",
                    case_type="交通事故人身损害赔偿",
                )
            ],
        )
        record, events = self.build(store)
        hits = [event for event in events if event.payload.get("scope") == "case_official"]
        self.assertTrue(hits)
        artifact = next(item for item in record.artifacts if item.kind == "research")
        self.assertIn("官方真实案例参照", artifact.body)
        self.assertIn("指导性案例264号", artifact.body)
        self.assertIn("案号", artifact.body)
        self.assertIn("抓取于", artifact.body)

    def test_official_cases_do_not_change_the_amount_interval(self):
        """官方案例没有金额，不能影响类案区间与样本量。"""

        with_official = CaseStore(
            [], [official_case("指导性案例264号", "交通事故赔偿纠纷", "按责任比例认定")]
        )
        record, _ = self.build(with_official)
        self.assertEqual(record.assessment.sample_size, 0)
        self.assertIsNone(record.assessment.case_median)

    def test_no_hit_says_so(self):
        record, _ = self.build(CaseStore([], []))
        artifact = next(item for item in record.artifacts if item.kind == "research")
        self.assertIn("未检索到可比的官方案例", artifact.body)


if __name__ == "__main__":
    unittest.main()

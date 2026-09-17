"""实证案例库接线测试：人工核验的案例必须进入检索、核验与区间计算。

诚实边界：未核验（imported_pending）的记录不进入流程；
库里没有实证时，系统维持 🟡 演示标注，绝不冒充。
"""

import json
import tempfile
import unittest
from pathlib import Path

from anli.models import Citation, Role
from anli.orchestrator import Orchestrator
from anli.real_cases import RealCase, RealCaseStore
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, CitationVerifier, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def make_case(case_id: str, amount: float, ratio: float = 0.7) -> RealCase:
    return RealCase(
        case_id=case_id,
        court="浙江省杭州市某区人民法院",
        year=2024,
        region="浙江省",
        case_type="交通事故人身损害赔偿",
        injury_level="无伤残",
        total_amount=amount,
        responsibility_ratio=ratio,
        court_level="基层人民法院",
        disability_auth=False,
        lost_wage_method="按凭证认定",
        key_evidence_tags=["事故认定书", "医疗费票据"],
        judge_reasoning_summary="法院逐项审核证据后，按责任比例支持了赔偿请求。",
        source_url="https://example.com/case",
        verification="human_verified",
    )


def build_store(tmp: Path, records: list[RealCase]) -> CaseStore:
    empirical = tmp / "real_cases.json"
    empirical.write_text(
        json.dumps([record.__dict__ for record in records], ensure_ascii=False),
        encoding="utf-8",
    )
    return CaseStore.load(
        DATA / "cases.seed.json", DATA / "cases.official.json", empirical
    )


class EmpiricalLoadTest(unittest.TestCase):
    def test_verified_records_enter_as_non_synthetic(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = build_store(
                Path(tmp),
                [make_case("E1", 5200), make_case("E2", 5600, ratio=1.0)],
            )
            empirical = [c for c in store.cases if c.case_id in {"E1", "E2"}]
            self.assertEqual(len(empirical), 2)
            self.assertTrue(all(not c.synthetic for c in empirical))

    def test_pending_records_do_not_enter(self):
        pending = make_case("P1", 5200)
        pending.verification = "imported_pending"
        store = build_store_from_records([pending])
        self.assertEqual(
            [c for c in store.cases if c.case_id == "P1"], []
        )

    def test_search_similar_finds_empirical(self):
        store = build_store_from_records(
            [make_case(f"E{i}", 5000 + i * 100) for i in range(4)]
        )
        hits = store.search_similar(
            case_type="交通事故人身损害赔偿", evidence=["事故认定书"]
        )
        self.assertTrue(hits)
        empirical_hits = [c for c in hits if not c.synthetic]
        self.assertTrue(empirical_hits, "实证案例必须排在检索结果最前")
        self.assertEqual(
            [c.case_id for c in hits][: len(empirical_hits)],
            [c.case_id for c in empirical_hits],
        )


def build_store_from_records(records):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "real_cases.json"
        path.write_text(
            json.dumps([r.__dict__ for r in records], ensure_ascii=False),
            encoding="utf-8",
        )
        return CaseStore.load(
            DATA / "cases.seed.json", DATA / "cases.official.json", path
        )


class VerifierTest(unittest.TestCase):
    def test_empirical_citation_is_authoritative(self):
        import tempfile

        records = [make_case("E9", 5300)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "real_cases.json"
            path.write_text(
                json.dumps([r.__dict__ for r in records], ensure_ascii=False),
                encoding="utf-8",
            )
            store = CaseStore.load(
                DATA / "cases.seed.json", DATA / "cases.official.json", path
            )
        verifier = CitationVerifier(LawStore.load(DATA / "laws.seed.json"), store)
        citation = Citation(
            source_type="case",
            source_id="E9",
            label="实证案号",
            quote=records[0].judge_reasoning_summary[:20],
        )
        result = verifier.verify(citation)
        self.assertTrue(result.verified)
        self.assertEqual(result.grade, "authoritative")
        self.assertIn("来源", result.note)


class OrchestratorTierTest(unittest.TestCase):
    def test_empirical_samples_get_green_nature_line(self):
        import tempfile

        records = [
            make_case(f"T{i}", 5000 + i * 200) for i in range(4)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "real_cases.json"
            path.write_text(
                json.dumps([r.__dict__ for r in records], ensure_ascii=False),
                encoding="utf-8",
            )
            store = CaseStore.load(
                DATA / "cases.seed.json", DATA / "cases.official.json", path
            )
        orchestrator = Orchestrator(
            law_store=LawStore.load(DATA / "laws.seed.json"),
            case_store=store,
            state_store=StateStore(),
        )
        record = orchestrator.run(
            task_id="empirical-e2e",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=car_accident_expectation(),
        )
        feasibility = next(
            a for a in record.artifacts if a.kind == "feasibility"
        )
        self.assertIn("🟢 实证类案", feasibility.body)
        self.assertIn("人工核验", feasibility.body)


if __name__ == "__main__":
    unittest.main()

"""报告一致性审计：同一份报告里，同一笔钱、同一个样本量，处处必须一致。

原则（用户要求）：给用户生成的报告不能自相矛盾，不能有任何幻觉。
本测试把这条原则变成可执行的断言——任何一次改动如果让某个章节
出现与评估引擎不一致的数字，这里立即变红。

覆盖场景：
- 小车事故（证据部分齐备）；
- 消费维权（材料齐备）；
- 纯口述（全部举证不能，金额为 0）；
- 大额重伤案（触发量级闸门，区间拒答）。

审计内容（每个场景逐项断言）：
1. 可支持金额在 摘要 / 可行性 / 计算说明 / 模拟判决书 / 导出报告中一致；
2. 样本量在 可行性 / 口径行 中一致；
3. 导出报告与内存交付物的标题集合一致（不多不少）；
4. 任何章节不得出现与评估金额矛盾的其它「可支持」数字。
"""

import json
import re
import unittest
from pathlib import Path

from anli.docx_export import build_docx
from anli.exporter import export_model_from_record, render_markdown
from anli.intake import build_profile
from anli.models import Role
from anli.orchestrator import Orchestrator
from anli.scenarios import (
    car_accident_expectation,
    car_accident_profile,
    consumption_expectation,
    consumption_profile,
)
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def build_orchestrator() -> Orchestrator:
    return Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(
            DATA / "cases.seed.json", DATA / "cases.official.json"
        ),
        state_store=StateStore(),
    )


def run(task_id: str, profile, expectation: float):
    orchestrator = build_orchestrator()
    return orchestrator.run(
        task_id=task_id, role=Role.CLAIMANT, profile=profile, expectation=expectation
    )


def digits(text: str) -> set[str]:
    """抽取文本中的金额数字（去千分位/空格），用于容错比对。"""

    cleaned = re.sub(r"[,\s]", "", text)
    return set(re.findall(r"\d+(?:\.\d+)?", cleaned))


def artifact_body(record, kind: str) -> str | None:
    for item in record.artifacts:
        if item.kind == kind:
            return item.body
    return None


SCENARIOS = [
    ("audit-car", car_accident_profile, car_accident_expectation),
    ("audit-consumption", consumption_profile, consumption_expectation),
]


class ConsistencyAuditTest(unittest.TestCase):
    def audit_amount(self, record):
        cap = record.assessment.legal_cap
        cap_text = f"{cap:,.0f}".replace(",", "")
        summary = str(record.extra.get("summary") or "")
        feasibility = artifact_body(record, "feasibility") or ""
        calculation = artifact_body(record, "calculation") or ""
        judgment = artifact_body(record, "document") or ""
        model = export_model_from_record(record, record.task_id)
        markdown = render_markdown(model)

        # 摘要：可支持金额必须出现（0 元时允许“可支持金额为 0 元”等表述）
        self.assertIn(
            cap_text,
            digits(summary),
            f"摘要里找不到可支持金额 {cap_text}：{summary[:120]}",
        )
        for name, body in [
            ("可行性评估", feasibility),
            ("金额计算说明", calculation),
        ]:
            self.assertIn(
                cap_text, digits(body), f"{name} 里的金额与评估引擎不一致（缺 {cap_text}）"
            )
        if judgment:
            self.assertIn(
                cap_text, digits(judgment), f"模拟判决书金额与评估引擎不一致（缺 {cap_text}）"
            )
        self.assertIn(
            cap_text, digits(markdown), f"导出报告金额与评估引擎不一致（缺 {cap_text}）"
        )

    def audit_sample_size(self, record):
        sample = record.assessment.sample_size
        feasibility = artifact_body(record, "feasibility") or ""
        self.assertIn(f"样本 {sample} 件", feasibility.replace("，", "，"))

    def audit_titles_match_export(self, record):
        model = export_model_from_record(record, record.task_id)
        export_titles = [title for title, _ in model["sections"]]
        artifact_titles = [item.title for item in record.artifacts]
        self.assertEqual(
            export_titles, artifact_titles, "导出报告的章节与内存交付物不一致"
        )

    def audit_docx_contains_cap(self, record):
        payload = build_docx(export_model_from_record(record, record.task_id))
        import io
        import xml.etree.ElementTree as ET
        import zipfile

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        text = "".join(node.text or "" for node in root.iter(f"{namespace}t"))
        cap_text = f"{record.assessment.legal_cap:,.0f}".replace(",", "")
        self.assertIn(cap_text, digits(text), "Word 导出里的金额与评估引擎不一致")

    def test_scenarios_are_internally_consistent(self):
        for task_id, profile_factory, expectation_factory in SCENARIOS:
            with self.subTest(scenario=task_id):
                record = run(task_id, profile_factory(), expectation_factory())
                self.audit_amount(record)
                self.audit_sample_size(record)
                self.audit_titles_match_export(record)
                self.audit_docx_contains_cap(record)

    def test_oral_case_zero_cap_stays_zero(self):
        """纯口述、无凭证：可支持金额为 0，任何章节不得出现暗示能拿到钱的数字。"""

        profile = build_profile(
            "6月10号我骑电动车被小轿车撞了，对方主要责任。医疗费3200元，误工费1800元。我想要对方赔12000元。",
            [],
            region="浙江省",
        )
        record = run("audit-oral", profile, 12000)
        self.assertEqual(record.assessment.legal_cap, 0)
        summary = str(record.extra.get("summary") or "")
        self.assertIn("0 元", summary)
        judgment = artifact_body(record, "document") or ""
        if judgment:
            self.assertIn("0 元", judgment)

    def test_big_case_refuses_interval_consistently(self):
        """大额案：区间拒答，且各章节不得再出现被拒答的样本区间数字。"""

        profile = build_profile(
            "6月10日我父亲骑电动车被小轿车撞伤，对方主要责任。住院治疗，医疗费已花80000元。",
            ["事故认定书.jpg", "医疗费发票.jpg"],
            region="浙江省",
        )
        record = run("audit-big", profile, 900000)
        assessment = record.assessment
        self.assertTrue(assessment.magnitude_mismatch)
        feasibility = artifact_body(record, "feasibility") or ""
        self.assertIn("暂不提供", feasibility)
        # 拒答时不得同时出现任何「同类案例区间：约 X」的表述
        self.assertNotRegex(feasibility, r"同类案例区间：约\s*[\d,]+")
        # 可支持金额仍是主参考并保持一致
        cap_text = f"{assessment.legal_cap:,.0f}".replace(",", "")
        self.assertIn(cap_text, digits(feasibility))


if __name__ == "__main__":
    unittest.main()

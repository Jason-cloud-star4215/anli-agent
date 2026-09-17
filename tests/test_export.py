"""交付物导出测试：导出的报告必须完整、一致、可直接打印发送。"""

import unittest
from pathlib import Path

from anli.exporter import build_markdown_export
from anli.models import Role, TaskState
from anli.orchestrator import Orchestrator
from anli.scenarios import car_accident_expectation, car_accident_profile
from anli.state import StateStore
from anli.stores import CaseStore, LawStore

DATA = Path(__file__).resolve().parent.parent / "data"


def run_task(task_id: str):
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
    return orchestrator, record


class ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orchestrator, cls.record = run_task("export-car")
        cls.markdown = build_markdown_export(cls.record, "export-car")

    def test_header_and_meta(self):
        self.assertIn("# 案理 · 法律分析报告", self.markdown)
        self.assertIn("报告编号 | export-car", self.markdown)
        self.assertIn("边界声明", self.markdown)
        self.assertIn("类案数据等级", self.markdown)

    def test_all_artifacts_present_with_titles(self):
        for artifact in self.record.artifacts:
            self.assertIn(artifact.title, self.markdown, f"导出缺少章节：{artifact.title}")

    def test_body_content_consistent_with_artifacts(self):
        summary = next(a for a in self.record.artifacts if a.kind == "summary")
        first_body_line = summary.body.split("\n")[0].strip()
        self.assertIn(first_body_line, self.markdown.replace("  \n", "\n"))

    def test_no_template_placeholders(self):
        self.assertNotIn("________", self.markdown, "不允许残留填空占位符")

    def test_footer_with_signature(self):
        self.assertIn("落款", self.markdown)
        self.assertIn("案理数字法律团队", self.markdown)

    def test_deferred_report_has_no_fake_content(self):
        """未完成任务（无交付物）导出时不能编造章节内容。"""

        empty_record = self.orchestrator.run(
            task_id="export-blank",
            role=Role.CLAIMANT,
            profile=car_accident_profile(),
            expectation=0,
        )
        # 构造一个未完成记录：直接交付空 artifacts 的场景
        empty_record.artifacts = []
        markdown = build_markdown_export(empty_record, "export-blank")
        self.assertIn("# 案理 · 法律分析报告", markdown)
        self.assertNotIn("最可能落点", markdown.split("---")[-1])


if __name__ == "__main__":
    unittest.main()

"""前端回归测试。

针对已经踩过的坑：按钮点了没反应、元素 ID 写错、静态资源没挂上。
这类问题不会让后端测试失败，但会直接毁掉演示。
"""

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


class FrontendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.src_html = (WEB / "index.src.html").read_text(encoding="utf-8")

    def test_bootstrap_is_vendored_and_linked(self):
        css = WEB / "vendor" / "bootstrap.min.css"
        self.assertTrue(css.exists(), "本地 Bootstrap 样式缺失")
        self.assertGreater(css.stat().st_size, 100_000)

    def test_page_does_not_depend_on_external_css(self):
        """样式由服务端内联，页面不依赖额外的样式请求，避免请求失败变成裸 HTML。"""

        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from anli.web import render_index

        html = render_index()
        self.assertNotIn('<link rel="stylesheet"', html)
        self.assertIn(".btn-primary", html)
        self.assertGreater(len(html), 200_000)

    def test_server_handles_query_string(self):
        """带查询参数的地址不能 404（演示模式 ?demo=car 依赖这一点）。"""

        source = (Path(__file__).resolve().parent.parent / "anli" / "web.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('self.path.split("?")[0]', source)

    def test_every_referenced_element_exists(self):
        ids = set(re.findall(r'\bid="([^"]+)"', self.html))
        used = set(re.findall(r'\$\("([^"]+)"\)', self.html))
        missing = sorted(used - ids)
        self.assertEqual(missing, [], f"脚本引用了页面上不存在的元素：{missing}")

    def test_no_duplicate_ids(self):
        ids = re.findall(r'\bid="([^"]+)"', self.html)
        duplicates = sorted({value for value in ids if ids.count(value) > 1})
        self.assertEqual(duplicates, [], f"存在重复 ID：{duplicates}")

    def test_sample_buttons_are_wired(self):
        self.assertIn('id="sampleCar"', self.html)
        self.assertIn('id="sampleConsumption"', self.html)
        self.assertIn('$("sampleCar").addEventListener', self.html)
        self.assertIn('$("sampleConsumption").addEventListener', self.html)

    def test_primary_actions_are_wired(self):
        self.assertIn('$("run").addEventListener("click", start)', self.html)
        self.assertIn('$("continue").addEventListener("click", continueAnalysis)', self.html)

    def test_sections_and_states_present(self):
        for element in [
            "summaryCard",
            "evidenceCard",
            "strategyCard",
            "reportCard",
            "citations",
            "findings",
            "stateBadge",
        ]:
            self.assertIn(f'id="{element}"', self.html)
        for state in ["NEEDS_EVIDENCE", "DELIVERED", "RUNNING", "FAILED"]:
            self.assertIn(state, self.html)

    def test_no_leftover_placeholder_api(self):
        """示例内容直接内置，不再依赖 /api/samples，避免点了没反应。"""

        self.assertNotIn("/api/samples", self.html)

    def test_built_page_is_self_contained(self):
        """构建产物必须自包含，直接双击文件打开也要有样式。"""

        self.assertNotIn('<link rel="stylesheet"', self.html)
        self.assertIn(".btn-primary", self.html)
        self.assertGreater(len(self.html), 200_000)

    def test_source_file_keeps_placeholder_link(self):
        """源文件保留占位链接，便于阅读与重新构建。"""

        self.assertIn('/vendor/bootstrap.min.css', self.src_html)

    def test_built_page_is_in_sync_with_source(self):
        """改了源文件却忘记重新构建，会导致线上页面与源码不一致。"""

        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from tools.build_web import CSS, LINK

        css = CSS.read_text(encoding="utf-8")
        expected = self.src_html.replace(LINK, "<style>\n" + css + "\n</style>")
        self.assertEqual(
            len(expected), len(self.html), "index.html 与源文件不同步，请运行 tools/build_web.py"
        )

    def test_three_columns_scroll_independently(self):
        """三栏各自滚动，页面本身不滚动。"""

        self.assertEqual(self.html.count('class="col-12 col-lg-3 col-scroll"'), 2)
        self.assertIn('class="col-12 col-lg-6 col-scroll"', self.html)
        self.assertIn("overflow-y: auto", self.html)
        self.assertIn("--header-h", self.html)

    def test_launcher_is_ascii_only(self):
        """批处理文件必须是纯 ASCII：中文内容在 cmd 下会乱码并报错。"""

        bat = Path(__file__).resolve().parent.parent / "启动案理.cmd"
        self.assertTrue(bat.exists(), "缺少启动脚本")
        raw = bat.read_bytes()
        non_ascii = [byte for byte in raw if byte > 127]
        self.assertEqual(non_ascii, [], "启动脚本包含非 ASCII 字符，cmd 会解析失败")
        self.assertIn(b"anli.web", raw)

    def test_evidence_confirmation_widget(self):
        """补交证据要能勾选确认，而不是只靠文件名识别。"""

        self.assertIn('id="confirmList"', self.html)
        self.assertIn("confirmed: checked", self.html)
        self.assertIn("我已有：", self.html)
        self.assertIn("项材料（填了", self.html)

    def test_answer_template_button(self):
        """要有填空式回答模板按钮，且模板按身份区分。"""

        self.assertIn('id="templateBtn"', self.html)
        self.assertIn("用模板填写", self.html)
        self.assertIn("var TEMPLATES", self.html)
        self.assertIn('$("templateBtn").addEventListener("click", insertTemplate)', self.html)
        for key in ['claimant: [', 'defendant: [', 'judge: [']:
            self.assertIn(key, self.html)

    def test_evidence_detail_questions(self):
        """勾选材料后要能继续回答细节，细节影响计入比例。"""

        self.assertIn("/api/evidence-schema", self.html)
        self.assertIn("q-answer", self.html)
        self.assertIn("ev-ok", self.html)
        self.assertIn("quality: quality", self.html)

    def test_process_stream_does_not_skip_typing_too_early(self):
        """积压阈值太低会把「辩论要点」整段变成闪现（曾经的 8 条阈值）。"""

        self.assertIn("STREAM_BACKLOG", self.html)
        self.assertIn("var STREAM_BACKLOG = 24", self.html)

    def test_result_cards_reveal_instead_of_popping(self):
        """结果区卡片要有依次浮现的动画，不能整块弹出来。"""

        self.assertIn("reveal-up", self.html)
        self.assertIn("revealCard", self.html)
        self.assertIn("staggerItems", self.html)

    def test_amounts_are_hedged_with_approx(self):
        """金额卡要用「约」的估算口径。"""

        self.assertIn('"约 " + money(a.legal_cap)', self.html)
        self.assertIn('return "约 " + money(low)', self.html)


if __name__ == "__main__":
    unittest.main()

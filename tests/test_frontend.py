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

    def test_page_reveals_cards_on_load(self):
        """首屏也要有滑入动画，不能只有结果页有。"""

        self.assertIn("revealAllCards", self.html)
        self.assertIn("main .card", self.html)

    def test_chat_area_exists(self):
        """要有对话区：看得见提交内容，并能继续第 2、第 3 轮。"""

        for element in ["chatCard", "chatLog", "roundBadge", "gateNotice"]:
            self.assertIn(f'id="{element}"', self.html)
        self.assertIn("renderChat", self.html)
        self.assertIn("systemReplyFor", self.html)
        self.assertIn("第 1 轮", self.html)

    def test_evidence_gate_blocks_conclusion_in_ui(self):
        """证据不足时金额卡显示「待补证 / 暂不定」。"""

        self.assertIn('capText = "待补证"', self.html)
        self.assertIn('likelyText = "暂不定"', self.html)

    def test_gate_samples_and_chinese_labels(self):
        """要能一键演示两道闸门，且过程流里不出现英文产物名。"""

        for element in ["sampleCriminal", "sampleBigRisk"]:
            self.assertIn(f'id="{element}"', self.html)
        self.assertIn("刑事案件示例", self.html)
        self.assertIn('out_of_scope: "超出覆盖范围提示"', self.html)
        self.assertIn('human_review: "转人工提示"', self.html)

    def test_every_sample_has_all_three_roles(self):
        """示例必须按身份分开写：被告不能看到原告口吻的案情（曾经回退到主张方版本）。"""

        markers = [
            # 大额索赔示例：三种身份各一份说法
            "我要求对方一共赔90万元",
            "对方要求我赔偿90万元，我认为金额太高",
            "原告目前仍在住院，医疗费8万元",
            # 刑事案件示例：三种身份各一份说法
            "我在网上被人以投资返利的名义骗走了8万元",
            "有人向公安报案说我涉嫌合同诈骗",
            "一起涉嫌诈骗罪的案件正在审查起诉阶段",
        ]
        for marker in markers:
            self.assertIn(marker, self.html, marker)
        # 角色版本缺失时要显式提示回退，而不是默默给主张方视角
        self.assertIn("该示例暂无当前身份的版本，显示的是主张方视角", self.html)

    def test_criminal_claimant_sample_is_the_victim_side(self):
        """刑事示例的「主张方」必须是被害人口吻，不能写成被指控方（曾经写反了）。"""

        self.assertIn("我在网上被人以投资返利的名义骗走了8万元", self.html)
        self.assertIn("我想知道钱还能不能追回来", self.html)
        # 被指控方的口吻只能出现在被告版本里
        self.assertNotIn("我表哥因为涉嫌诈骗罪被刑事拘留", self.html)

    def test_template_button_switches_with_role(self):
        """模板按身份区分，并在未手动改动时随身份切换。"""

        self.assertIn("refreshTemplateForRole", self.html)
        for marker in ["【对方的主张】", "【我认为不合理的部分】", "【被告抗辩】", "【案件概况】"]:
            self.assertIn(marker, self.html, marker)

    def test_page_shows_version_badge(self):
        """页头要有版本号，用来分辨旧缓存页面。"""

        self.assertIn('id="versionBadge"', self.html)
        self.assertIn('versionBadge', self.html)
        self.assertIn("Ctrl+F5", self.html)


if __name__ == "__main__":
    unittest.main()

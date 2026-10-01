"""投屏模式（?present=1）的静态回归：源文件与构建产物必须同时具备三要素——
1) html.present 的字号覆盖；2) 页头角标样式；3) 首帧前生效的检测脚本。
"""

import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


class PresentModeTest(unittest.TestCase):
    def test_source_has_present_layer(self):
        src = (WEB / "index.src.html").read_text(encoding="utf-8")
        self.assertIn("html.present", src)
        self.assertIn('get("present") === "1"', src)
        self.assertIn("投屏模式", src)

    def test_built_bundle_has_present_layer(self):
        """build_web 之后构建产物必须同步带上投屏层（防漏构建）。"""

        built = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertIn("html.present", built)
        self.assertIn("投屏模式", built)

    def test_present_does_not_hide_panels(self):
        """投屏模式只放大字号，不允许隐藏任何面板：三栏过程可见是产品语言。"""

        src = (WEB / "index.src.html").read_text(encoding="utf-8")
        start = src.index("html.present")
        block = src[start : src.index("}", src.index("html.present .chat-msg"))]
        self.assertNotIn("display: none", block)


if __name__ == "__main__":
    unittest.main()

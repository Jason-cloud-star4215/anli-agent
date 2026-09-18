"""分发打包安全测试：不泄漏密钥、不漏发关键文档。

背景：报告里的「数据等级 → 升级路径」指向《实证案例录入指南.md》，
这份指南必须真的在发放包里，否则用户按提示找不到东西。
"""

import unittest

from tools import pack


class PackIncludeTest(unittest.TestCase):
    def test_case_guide_is_shipped(self):
        self.assertIn("实证案例录入指南.md", pack.INCLUDE_FILES)
        self.assertTrue((pack.ROOT / "实证案例录入指南.md").exists())

    def test_intro_and_probe_docs_are_shipped(self):
        """介绍文档与数据源侦察结论都要跟着发放包走，方便直接给人看。"""

        for name in ("案理-产品介绍.md", "R3-数据源侦察.md"):
            self.assertIn(name, pack.INCLUDE_FILES)
            self.assertTrue((pack.ROOT / name).exists(), name)

    def test_constitution_docs_are_shipped(self):
        for name in ("README.md", "PRD.md", "PROGRESS.md", "DEMO_SCRIPT.md"):
            self.assertIn(name, pack.INCLUDE_FILES)

    def test_env_file_never_packed(self):
        self.assertIn(".env", pack.HARD_EXCLUDE)
        names = [path.name for path in pack.iter_files()]
        self.assertNotIn(".env", names)

    def test_runtime_data_never_packed(self):
        for path in pack.iter_files():
            relative = path.relative_to(pack.ROOT).as_posix()
            for leaked in ("state/", "cases/", "artifacts/", "cache/", "build/", "dist/.env"):
                self.assertFalse(
                    relative.startswith(leaked),
                    f"运行数据不应进发放包：{relative}",
                )


if __name__ == "__main__":
    unittest.main()

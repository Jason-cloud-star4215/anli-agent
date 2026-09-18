"""结论矩阵：责任档位 × 身份 × 证据充足/不足，逐格验行为。

零散补丁挡不住「说反话」这类问题——「我负主要责任」被算成对方赔 100%、
「我方全责」还在催补材料，都是单点修复漏出来的。
这里把 30 种组合全跑一遍，把每一档该说什么固定成断言。
"""

import unittest

from tools.responsibility_matrix import (
    ALL_EVIDENCE,
    RESPONSIBILITY_WORDS,
    build_orchestrator,
    run_cell,
)


class MatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orchestrator = build_orchestrator()
        cls.cells = []
        for role, cases in RESPONSIBILITY_WORDS.items():
            for label, wording in cases.items():
                for evidence in ([], ALL_EVIDENCE):
                    cls.cells.append(
                        run_cell(cls.orchestrator, role, label, wording, evidence)
                    )

    def test_matrix_is_complete(self):
        self.assertEqual(len(self.cells), 30)

    def test_party_at_zero_responsibility_says_so(self):
        """对方 0%：主张方必须被明确告知索赔没有责任基础，抗辩方要被告知对他有利。"""

        for cell in self.cells:
            if cell["ratio"] != 0.0 or cell["role"] == "judge":
                continue
            with self.subTest(role=cell["role"], label=cell["label"]):
                if cell["role"] == "claimant":
                    self.assertIn("缺乏责任基础", cell["first"])
                else:
                    self.assertIn("对你有利", cell["first"])

    def test_client_majority_leads_with_the_split(self):
        for cell in self.cells:
            if not cell["majority"] or cell["no_basis"] or cell["role"] == "judge":
                continue
            with self.subTest(role=cell["role"], label=cell["label"]):
                self.assertTrue(
                    "责任分摊" in cell["first"] or "责任认定对你不利" in cell["first"],
                    f"我方担主要责任时首句必须讲责任，实际是：{cell['first']}",
                )

    def test_no_basis_does_not_ask_for_more_evidence(self):
        """责任基础不成立时不再索要材料：补得再齐也改变不了责任认定。"""

        for cell in self.cells:
            if not cell["no_basis"]:
                continue
            with self.subTest(role=cell["role"], label=cell["label"]):
                self.assertEqual(cell["state"], "DELIVERED")
                self.assertFalse(cell["asked_evidence"])

    def test_full_evidence_delivers_except_when_liability_is_absent(self):
        for cell in self.cells:
            if cell["evidence"] != "充足" or cell["role"] == "judge":
                continue
            with self.subTest(role=cell["role"], label=cell["label"]):
                self.assertEqual(cell["state"], "DELIVERED", cell["first"])

    def test_missing_evidence_still_gates_normal_cases(self):
        """普通案子的证据闸门不能被这一轮改动顺手关掉。"""

        for cell in self.cells:
            if cell["evidence"] != "不足" or cell["no_basis"] or cell["role"] == "judge":
                continue
            with self.subTest(role=cell["role"], label=cell["label"]):
                self.assertEqual(cell["state"], "NEEDS_EVIDENCE")

    def test_documents_warn_only_when_liability_is_absent(self):
        for cell in self.cells:
            with self.subTest(role=cell["role"], label=cell["label"]):
                self.assertEqual(cell["has_warning"], cell["no_basis"])


if __name__ == "__main__":
    unittest.main()

"""脱敏测试：对外发送前，个人信息必须打码，但句子结构不能被打散。"""

import unittest

from anli.redact import describe, has_sensitive, redact, redact_with_counts


class RedactTest(unittest.TestCase):
    def test_phone_is_masked(self):
        text = "我的电话是13812345678，随时可以联系。"
        result = redact(text)
        self.assertNotIn("13812345678", result)
        self.assertIn("138****5678", result)
        self.assertIn("随时可以联系", result)

    def test_id_card_is_masked(self):
        text = "身份证号330106199001011234，住杭州市西湖区。"
        result = redact(text)
        self.assertNotIn("330106199001011234", result)
        self.assertIn("330106********1234", result)

    def test_bank_card_is_masked(self):
        text = "收款卡号6222021234567890，请核对。"
        result = redact(text)
        self.assertNotIn("6222021234567890", result)
        self.assertIn("6222********7890", result)

    def test_email_is_masked(self):
        result = redact("发到 zhangsan@example.com 就行")
        self.assertNotIn("zhangsan@example.com", result)
        self.assertIn("@example.com", result)

    def test_address_detail_is_masked(self):
        result = redact("被告住在浙江省杭州市西湖区文三路123号3单元501室")
        self.assertNotIn("文三路123号", result)
        self.assertIn("浙江省杭州市西湖区", result, "行政区划要保留，便于判断管辖")

    def test_labeled_number_is_masked(self):
        result = redact("保单号：P123456789012345")
        self.assertNotIn("P123456789012345", result)

    def test_plain_legal_text_is_untouched(self):
        """正常案情描述不该被误伤。"""

        text = "我骑电动车被小轿车撞了，交警认定对方主要责任，医疗费3200元，误工费1800元。"
        self.assertEqual(redact(text), text)
        self.assertFalse(has_sensitive(text))

    def test_counts_and_describe(self):
        text = "手机13812345678，身份证330106199001011234"
        cleaned, counts = redact_with_counts(text)
        self.assertEqual(counts["手机号"], 1)
        self.assertEqual(counts["身份证号"], 1)
        self.assertIn("已打码", describe(counts))
        self.assertIn("手机号", describe(counts))
        self.assertNotIn("13812345678", cleaned)

    def test_agent_prompt_exit_is_masked(self):
        """合并后修的洞：论证/评估阶段的「用户原话」也要过脱敏再发给模型。

        案情理解入口有自己的脱敏，但主张方/法官等 prompt 里会重复引用原话，
        这里锁死 agents.user_text_for_llm 这个统一出口。
        """

        from anli.agents import user_text_for_llm

        text = "我叫张三，身份证330106199001011234，卡号6222020200112233445。"
        out = user_text_for_llm(text)
        self.assertNotIn("330106199001011234", out)
        self.assertNotIn("6222020200112233445", out)
        self.assertTrue(has_sensitive(text))

    def test_empty_input(self):
        self.assertEqual(redact(""), "")
        self.assertFalse(has_sensitive(""))
        self.assertEqual(describe({}), "未发现需要打码的个人信息")


if __name__ == "__main__":
    unittest.main()

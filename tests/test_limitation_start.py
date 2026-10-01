"""回归：时效必须从事发日起算，不能拿分析当天凑数。

2026-10-01 WorkBuddy 交叉审计发现：主线案例（6月10号事故、10月1日分析）
的时效提示写「预计截止 2029-09-30」——起算点错用了分析日，比正确值
2029-06-10 晚了约 3.5 个月。修复后从案情原话推断事发日期起算，
抽不到日期时如实标注 assumed_start。
"""

import unittest
from datetime import date

from anli.tools_builtin import _calc_limitation, infer_case_date

ANALYSIS_DAY = date(2026, 10, 1)


class InferCaseDateTest(unittest.TestCase):
    def test_spoken_date_binds_year_from_context(self):
        """「6月10号」在 10 月分析 → 当年 6 月 10 日。"""

        self.assertEqual(
            infer_case_date("6月10号我骑电动车被撞了", today=ANALYSIS_DAY),
            "2026-06-10",
        )

    def test_future_spoken_date_falls_back_a_year(self):
        """10 月说「12月20号」通常指去年，年份回退。"""

        self.assertEqual(
            infer_case_date("12月20号买的手机出问题", today=ANALYSIS_DAY),
            "2025-12-20",
        )

    def test_full_date_preferred(self):
        """带年份的日期直接采用，取最早一条。"""

        self.assertEqual(
            infer_case_date("2025年3月1日下单，2025年3月5日收货", today=ANALYSIS_DAY),
            "2025-03-01",
        )

    def test_no_date_returns_none(self):
        self.assertIsNone(infer_case_date("我被人打了，想索赔", today=ANALYSIS_DAY))


class LimitationStartTest(unittest.TestCase):
    def test_deadline_counts_from_injury_date(self):
        """主线案例：6月10号事故 → 截止 2029-06-10，不再是分析日+3年。"""

        result = _calc_limitation(
            "交通事故人身损害赔偿",
            today=ANALYSIS_DAY,
            start_date=infer_case_date(
                "6月10号我骑电动车被一辆小轿车撞了", today=ANALYSIS_DAY
            ),
        )
        self.assertFalse(result["assumed_start"])
        self.assertEqual(result["start_date"], "2026-06-10")
        self.assertEqual(result["deadline"], "2029-06-10")
        self.assertIn("自案发日 2026-06-10 起算", result["note"])

    def test_missing_date_is_honestly_marked(self):
        """抽不到日期：按分析日估算但必须如实标注，不冒充事发日起算。"""

        result = _calc_limitation(
            "交通事故人身损害赔偿", today=ANALYSIS_DAY, start_date=None
        )
        self.assertTrue(result["assumed_start"])
        self.assertEqual(result["deadline"], "2029-10-01")
        self.assertIn("暂按分析当日估算", result["note"])

    def test_invalid_start_date_falls_back(self):
        result = _calc_limitation(
            "交通事故人身损害赔偿", today=ANALYSIS_DAY, start_date="not-a-date"
        )
        self.assertTrue(result["assumed_start"])

    def test_labor_dispute_one_year(self):
        result = _calc_limitation(
            "劳动争议", today=ANALYSIS_DAY, start_date="2025-06-01"
        )
        self.assertEqual(result["deadline"], "2026-06-01")


if __name__ == "__main__":
    unittest.main()

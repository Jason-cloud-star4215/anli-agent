"""法定赔偿标准表：把「需按法定标准计算」从公式变成能算的数字。

来源纪律与实证类案库一致：
- 每条标准必须带官方出处（统计公报 / 高院指引条款）与可点开的链接；
- 第三方汇编转载的数据，note 里必须写明"引用前请在官方渠道核对"；
- 查不到对应标准时，调用方必须如实回退到"给公式、列参数"，
  不允许拿邻近地区的数字冒充本地标准。

本表当前只覆盖浙江省（比赛在杭州，演示本地化）。其他地区扩展时
按同样格式追加条目，region 字段区分，lookup 按 region 精确匹配。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Standard:
    """一条法定赔偿标准。"""

    key: str              # 标准标识，如 private_unit_avg_wage
    label: str            # 展示名称
    value: float          # 数值
    unit: str             # 元/年 | 元/天
    region: str           # 适用地区
    year: int             # 数据所属年份（并非"年度"：2024年数据一般适用于2025年度）
    official: bool        # 是否官方口径
    basis: str            # 法律/指引依据条款
    source_label: str     # 来源名称
    source_url: str       # 来源链接
    note: str = ""


# item_assessment 里 missing_params 的措辞 → 标准表 key 的映射。
# 只映射"系统能替用户查到"的参数；伤残等级、误工天数这类属于用户事实，不在映射内。
PARAM_TO_KEY: dict[str, str] = {
    "当地相同或相近行业上一年度职工平均工资": "private_unit_avg_wage",
    "当地护工劳务报酬标准": "private_unit_avg_wage",
    "当地居民人均可支配收入": "urban_disposable_income",
    "当地营养费标准": "nutrition_daily",
}


def validate_record(data: dict) -> list[str]:
    """对一条标准记录做校验，返回问题列表。"""

    issues: list[str] = []
    for name in ("key", "label", "unit", "region", "basis", "source_label"):
        if not str(data.get(name) or "").strip():
            issues.append(f"{name} 不能为空")
    value = data.get("value")
    if not isinstance(value, (int, float)) or value <= 0:
        issues.append("value 必须是大于 0 的数值")
    year = data.get("year")
    if not isinstance(year, int) or not 2000 <= year <= 2100:
        issues.append("year 必须是合理的整数年份")
    if not str(data.get("source_url") or "").lower().startswith("http"):
        issues.append("source_url 必须是以 http 开头的链接")
    if not isinstance(data.get("official"), bool):
        issues.append("official 必须是布尔值")
    return issues


class StandardsStore:
    """标准表：同 key 允许多个年份条目，查询默认取数据年份最新的一条。"""

    def __init__(self, records: list[Standard] | None = None, problems: list[str] | None = None) -> None:
        self.records = records or []
        self.problems = problems or []

    @classmethod
    def load(cls, path: str | Path) -> "StandardsStore":
        path = Path(path)
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        records: list[Standard] = []
        problems: list[str] = []
        for index, item in enumerate(raw, start=1):
            issues = validate_record(item)
            if issues:
                problems.extend(f"第 {index} 条（{item.get('key', '?')}）：{issue}" for issue in issues)
                continue
            records.append(Standard(**item))
        return cls(records, problems)

    def lookup(self, key: str, data_year: int | None = None) -> Standard | None:
        """按 key 取标准；data_year 指定数据年份，否则取最新。

        找不到时返回 None——调用方必须回退到"只给公式"，不得编造数值。
        """

        candidates = [record for record in self.records if record.key == key]
        if data_year is not None:
            candidates = [record for record in candidates if record.year == data_year]
        if not candidates:
            return None
        return max(candidates, key=lambda record: record.year)

    def describe_param(self, param: str, region: str = "") -> str:
        """把逐项认定里"缺参数"的措辞翻译成带出处与数字的标准说明。

        region 只接受与条目一致的地区（当前仅浙江）；查不到返回空串，
        交由调用方维持旧行为（只列参数名）。
        """

        key = PARAM_TO_KEY.get(param)
        if key is None:
            return ""
        candidates = [record for record in self.records if record.key == key]
        if region:
            local = [record for record in candidates if record.region == region]
            if local:
                candidates = local
        if not candidates:
            return ""
        record = max(candidates, key=lambda record: record.year)
        if record.unit == "元/年":
            daily = round(record.value / 365)
            return (
                f"{record.label} {record.value:,.0f} {record.unit}（{record.year}年数据）"
                f"≈ {daily} 元/天；依据：{record.basis.split('：')[0]}；"
                f"来源：{record.source_label}"
            )
        return (
            f"{record.label} {record.value:,.0f} {record.unit}；"
            f"依据：{record.basis.split('：')[0]}；来源：{record.source_label}"
        )

    def disability_compensation(self, grade: int, applicable_year: int) -> dict | None:
        """残疾赔偿金 = 城镇居民人均可支配收入 × 20年 × 伤残赔偿指数。

        applicable_year 指"赔偿年度"（侵权行为适用的标准年度），
        取数据年份 = 年度 - 1 的收入值。查不到收入数据返回 None。
        60 岁以上每增一岁减一年、75 岁以上按 5 年的规则属于用户事实，
        由调用方在报告里提示，本函数只算标准情形。
        """

        if not 1 <= grade <= 10:
            return None
        record = self.lookup("urban_disposable_income", data_year=applicable_year - 1)
        if record is None:
            return None
        # 伤残赔偿指数：一级 100%，十级 10%，即 (11 - 级数) / 10
        index = (11 - grade) / 10
        amount = round(record.value * 20 * index, 2)
        return {
            "grade": grade,
            "applicable_year": applicable_year,
            "income_value": record.value,
            "income_year": record.year,
            "years": 20,
            "index": index,
            "amount": amount,
            "formula": f"{record.value:,.0f} × 20 × {index:.0%} = {amount:,.0f} 元",
            "basis": record.basis,
            "source_label": record.source_label,
            "source_url": record.source_url,
            "note": "60岁以上每增一岁减一年，75岁以上按5年计算（以判决为准）",
        }

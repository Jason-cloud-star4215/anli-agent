"""官方发布案例库：法院发布的典型案例稿（**不是**判决书全文）。

三层数据必须分清，谁都不能冒充谁：

    🟢 实证库（`data/real_cases.json`）：判决书全文 + 人工逐字段核验 → 唯一能驱动金额区间；
    🟠 官方发布案例（`data/cases.published.json`）：省高院／最高法发布的典型案例通报稿 →
       只做「类案参照」的文本层，讲清责任划分、伤残等级与裁判要旨；
    🟡 演示样本（`data/cases.seed.json`）：虚构，只用于跑通流程，全程标注。

这一层有三条自我约束：

1. **金额只作旁证**：发布稿里的判赔金额只列出来给人看，绝不参与
   「类案区间」「最可能落点」的计算——那是编辑过的二手材料，不能当判决数据用；
2. **必须标来源**：每条都带 `source_url` 与 `fetched_at`，报告里写明「非判决书全文」；
3. **不收刑事行政**：抓取阶段已经过滤，这里再做一次防御性校验。

数据由 `tools/fetch_published_cases.py` 生成，抓取口径见 `R3-数据源侦察.md`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

TIER_LABEL = "官方发布案例（典型案例稿，非判决书全文）"

CRIMINAL_MARKS = ("有期徒刑", "缓刑", "判处", "被告人", "公诉机关", "刑事拘留")


@dataclass
class PublishedCase:
    """一条官方发布案例。字段与抓取产物一一对应。"""

    case_id: str
    title: str                       # 要旨式标题
    case_name: str = ""              # 案名，如「项某诉刘某等机动车交通事故责任纠纷案」
    cause: str = ""                  # 系统案由
    region: str = ""
    court: str = ""
    injury_level: str = ""
    responsibility: str = ""
    amounts: list[str] = field(default_factory=list)
    case_no: str = ""
    facts: str = ""
    holding: str = ""
    meaning: str = ""
    source_url: str = ""
    source_name: str = ""
    article_title: str = ""
    published_date: str = ""
    fetched_at: str = ""

    @property
    def label(self) -> str:
        return self.case_name or self.title

    @property
    def amount_text(self) -> str:
        return "、".join(self.amounts)

    @property
    def is_clean_civil(self) -> bool:
        """防御性校验：正文里出现刑事表述的，不放进参照。"""

        blob = f"{self.title}{self.facts}{self.holding}"
        return not any(mark in blob for mark in CRIMINAL_MARKS)


def case_from_dict(item: dict) -> PublishedCase:
    """按已知字段构造；多出来的字段（tier/note 等）忽略，缺字段留空。"""

    known = {field_name for field_name in PublishedCase.__dataclass_fields__}
    payload = {key: value for key, value in item.items() if key in known}
    amounts = payload.get("amounts")
    payload["amounts"] = [str(amount) for amount in amounts] if isinstance(amounts, list) else []
    for key in ("case_id", "title"):
        payload.setdefault(key, "")
    return PublishedCase(**payload)


class PublishedCaseStore:
    """官方发布案例库：按案由与关键词检索，命中不到就返回空，不编造。"""

    def __init__(self, records: list[PublishedCase] | None = None) -> None:
        self.records = [item for item in (records or []) if item.is_clean_civil]

    @classmethod
    def load(cls, path: str | Path) -> "PublishedCaseStore":
        """读不到或格式不对都当作空库：这一层是锦上添花，不能让主流程挂掉。"""

        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls([])
        if not isinstance(raw, list):
            return cls([])
        records: list[PublishedCase] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                records.append(case_from_dict(item))
            except TypeError:
                continue
        return cls(records)

    def __len__(self) -> int:
        return len(self.records)

    def stats(self) -> dict:
        """给健康检查与文档用的概况。"""

        with_amount = sum(1 for item in self.records if item.amounts)
        by_cause: dict[str, int] = {}
        for item in self.records:
            by_cause[item.cause] = by_cause.get(item.cause, 0) + 1
        return {
            "total": len(self.records),
            "with_amount": with_amount,
            "causes": by_cause,
        }

    def search(
        self,
        keywords: list[str],
        case_type: str = "",
        limit: int = 4,
        min_score: float = 3.0,
    ) -> list[PublishedCase]:
        """按案由与关键词排序取前 N 条。

        同案由是最强信号（+3），关键词原样命中（+2）与二元切片（+0.4）用于排序——
        因为是发布稿，措辞与判决书不同，命中门槛要比官方案例缓存低一档，
        否则会出现「一条都参照不到」的假象。
        """

        scored: list[tuple[float, PublishedCase]] = []
        for case in self.records:
            if case_type and case.cause != case_type:
                continue
            haystack = (
                f"{case.title}{case.case_name}{case.cause}{case.region}"
                f"{case.responsibility}{case.injury_level}{case.facts}{case.holding}"
            )
            score = 0.0
            for keyword in keywords:
                text = (keyword or "").strip()
                if not text:
                    continue
                if text in haystack:
                    score += 2.0
                grams = [text[index : index + 2] for index in range(max(1, len(text) - 1))]
                if grams:
                    score += sum(1 for gram in grams if gram in haystack) * 0.4
            if case_type and case.cause == case_type:
                score += 3.0
            # 带金额与带责任认定的排在前面：参照价值更高
            if case.amounts:
                score += 0.5
            if case.responsibility:
                score += 0.5
            if score >= min_score:
                scored.append((score, case))
        # 同分保持抓取顺序，排序稳定；不再二次按日期排，避免把新发布的挤到后面看不出规律
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [case for _, case in scored[:limit]]

    def render_reference_lines(self, cases: list[PublishedCase]) -> list[str]:
        """渲染成报告里的「官方发布案例参照」小节。金额明确标注为旁证。"""

        return render_reference_lines(cases)


def render_reference_lines(cases: list[PublishedCase]) -> list[str]:
    """把发布案例渲染成「类案参照」小节的行。

    金额一律带「（旁证，不参与区间计算）」的尾巴——报告里不能让人误以为
    这些数字进了我们的区间计算。
    """

    lines: list[str] = []
    for case in cases:
        meta = [
            f"案名：{case.case_name}" if case.case_name else "",
            f"责任认定：{case.responsibility}" if case.responsibility else "",
            f"伤残：{case.injury_level}" if case.injury_level else "",
            f"文中金额：{case.amount_text}（旁证，不参与区间计算）" if case.amounts else "",
        ]
        meta = [item for item in meta if item]
        lines.append(f"- {case.title}")
        if meta:
            lines.append("    " + "；".join(meta))
        if case.holding:
            lines.append(f"    裁判要点：{case.holding[:200]}")
        source = f"    来源：{case.source_name or '法院官网'}"
        if case.source_url:
            source += f"（{case.source_url}"
            source += f"，抓取于 {case.fetched_at[:10]}）" if case.fetched_at else "）"
        lines.append(source)
    return lines

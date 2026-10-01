"""内置工具集合。

对应 BACKEND_STRUCTURE.md 第 2 节的工具层：法条检索、类案检索、期限与金额计算。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .material_text import extract_dates as _extract_full_dates
from .retrieval_flk import LawSourceChecker
from .published_cases import PublishedCaseStore
from .stores import CaseStore, LawStore
from .tools import Tool, ToolRegistry

LIMITATION_RULES: dict[str, tuple[float, str]] = {
    "交通事故人身损害赔偿": (3.0, "民法典第一百八十八条：普通诉讼时效三年"),
    "消费维权": (3.0, "民法典第一百八十八条：普通诉讼时效三年"),
    "劳动争议": (1.0, "劳动争议调解仲裁法第二十七条：仲裁时效一年"),
}

# 口语日期（「6月10号」「3月1日」）：不写年份，需按语境补
_DATE_WITHOUT_YEAR = re.compile(r"(?:^|[^\d])(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]")


def infer_case_date(text: str, today: date | None = None) -> str | None:
    """从案情原话推断事发日期（YYYY-MM-DD），抽不到返回 None。

    带年份的日期直接用 material_text 的正则（取最早一条，最接近事发）；
    口语日期（「6月10号」）没写年份：按当年补，若补完落在将来（晚于明天）
    则年份退一年——10 月说「12月20号」通常指的是去年。
    """

    day = today or date.today()
    full = _extract_full_dates(text or "")
    if full:
        return min(full)
    match = _DATE_WITHOUT_YEAR.search(text or "")
    if not match:
        return None
    try:
        candidate = date(day.year, int(match.group(1)), int(match.group(2)))
    except ValueError:
        return None
    if candidate > day + timedelta(days=1):
        try:
            candidate = date(day.year - 1, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    return candidate.isoformat()


def build_default_registry(
    law_store: LawStore,
    case_store: CaseStore,
    law_checker: LawSourceChecker | None = None,
    published_store: PublishedCaseStore | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()

    # 官方发布案例（省高院／最高法典型案例稿）：只有真的装了这份缓存才注册，
    # 否则过程流里会多出一条永远查不到的检索
    if published_store is not None and len(published_store) > 0:
        registry.register(
            Tool(
                name="published_case_search",
                description=(
                    "在法院发布的典型案例稿里检索同类案件参照，返回责任划分、伤残等级与裁判要旨。"
                    "这些是发布稿而非判决书全文，其中的金额只作旁证，不参与金额区间计算。"
                ),
                schema={"keywords": list, "case_type": str},
                handler=lambda keywords, case_type="": published_store.search(
                    list(keywords), case_type=case_type
                ),
            )
        )

    registry.register(
        Tool(
            name="law_lookup",
            description="按关键词检索内置法条库，返回结构化法条",
            schema={"keywords": list},
            handler=lambda keywords: law_store.search(list(keywords)),
        )
    )

    registry.register(
        Tool(
            name="case_search",
            description="按案由与证据构成检索相似类案，返回带案号的案例记录",
            schema={"case_type": str, "region": str, "evidence": list},
            handler=lambda case_type, region="", evidence=None: case_store.search_similar(
                case_type=case_type, evidence=evidence, region=region
            ),
        )
    )

    registry.register(
        Tool(
            name="official_case_search",
            description=(
                "在本地缓存的真实官方案例（最高人民法院指导性案例）里检索裁判要点参照。"
                "这些案例带案号与来源链接，但不参与金额区间计算。"
            ),
            schema={"keywords": list, "case_type": str},
            handler=lambda keywords, case_type="": case_store.search_official(
                list(keywords), case_type=case_type
            ),
        )
    )

    registry.register(
        Tool(
            name="calc_damages",
            description="按赔偿项目合计金额",
            schema={"items": dict},
            handler=lambda items: round(sum(float(v) for v in items.values()), 2),
        )
    )

    registry.register(
        Tool(
            name="calc_limitation",
            description="计算诉讼时效或仲裁时效：优先从案情中的事发日期起算，缺日期时如实标注按分析日估算",
            # start_date 是可选参数：schema 只声明必填项，validate 不会把 None 拦下
            schema={"case_type": str},
            handler=_calc_limitation,
        )
    )

    # 只有真的配了核对器才注册这个工具：没配就是没这个能力，
    # 不要在过程流里刷一条查不到结果的检索
    if law_checker is not None:
        registry.register(
            Tool(
                name="law_source_check",
                description=(
                    "在国家法律法规数据库核对这部法是否收录、公布与施行日期与官方页面。"
                    "只能证明该法存在，不能代替条文正文核验。"
                ),
                schema={"law_name": str},
                handler=lambda law_name: law_checker.check(law_name),
            )
        )

    return registry


def _calc_limitation(
    case_type: str, today: date | None = None, start_date: str | None = None
) -> dict:
    """时效计算。起算点必须是事发日（权利受损之日），不能拿分析当天凑数——
    2026-10-01 修复：此前一律按 `date.today()` 起算，主线案例（6月10号事故）
    会算出 2029-09-30 的截止日，比正确值晚了约 3.5 个月。案情抽不到日期时
    如实标注 assumed_start，并在提示里写明真实起算规则。"""

    years, rule = LIMITATION_RULES.get(case_type, (3.0, "普通诉讼时效三年"))
    day = today or date.today()

    injury: date | None = None
    if start_date:
        try:
            injury = date.fromisoformat(str(start_date)[:10])
        except ValueError:
            injury = None
    assumed = injury is None
    start = injury or day

    # 按公历年推进（三年就是同月同日 +3 年），不用 365×3 凑数——
    # 跨闰年时 365×3 会差一天，法律语义是日历年周期
    try:
        deadline = start.replace(year=start.year + int(years))
    except ValueError:  # 2月29日 → 平年无此日，落到 2月28日
        deadline = start.replace(year=start.year + int(years), day=28)

    if assumed:
        note = "案情未提供事发日期，暂按分析当日估算；实际自你知道或应当知道权利受损之日起算"
    else:
        note = f"自案发日 {injury.isoformat()} 起算；如有中止、中断或起算点争议，以法院认定为准"

    return {
        "case_type": case_type,
        "rule": rule,
        "years": years,
        "start_date": start.isoformat(),
        "deadline": deadline.isoformat(),
        "assumed_start": assumed,
        "note": note,
    }

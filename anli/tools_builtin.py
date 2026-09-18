"""内置工具集合。

对应 BACKEND_STRUCTURE.md 第 2 节的工具层：法条检索、类案检索、期限与金额计算。
"""

from __future__ import annotations

from datetime import date, timedelta

from .retrieval_flk import LawSourceChecker
from .published_cases import PublishedCaseStore
from .stores import CaseStore, LawStore
from .tools import Tool, ToolRegistry

LIMITATION_RULES: dict[str, tuple[float, str]] = {
    "交通事故人身损害赔偿": (3.0, "民法典第一百八十八条：普通诉讼时效三年"),
    "消费维权": (3.0, "民法典第一百八十八条：普通诉讼时效三年"),
    "劳动争议": (1.0, "劳动争议调解仲裁法第二十七条：仲裁时效一年"),
}


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
            description="计算诉讼时效或仲裁时效剩余时间",
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


def _calc_limitation(case_type: str, today: date | None = None) -> dict:
    years, rule = LIMITATION_RULES.get(case_type, (3.0, "普通诉讼时效三年"))
    start = today or date.today()
    days = int(round(years * 365))
    deadline = start + timedelta(days=days)
    return {
        "case_type": case_type,
        "rule": rule,
        "years": years,
        "deadline": deadline.isoformat(),
    }

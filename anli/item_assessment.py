"""逐项认定：按法院的裁判逻辑判断每一项能不能支持、按什么标准算。

不使用「主张金额 × 证据系数」这种打折算法。法院不会因为证据不足就把 3200 元判成 1920 元，
而是逐项处理：

1. 有凭证、能核实的 → 按凭证金额全额认定；
2. 事实存在但缺凭证、而法律规定了计算标准的 → 按法定标准计算（需参数，如误工天数、当地工资标准）；
3. 既无证据又无法定标准的 → 不予支持（归零）。

细节问题的作用也随之改变：它决定走哪条路径，而不是打个折扣。
"""

from __future__ import annotations

from dataclasses import dataclass, field

STATUS_LABEL = {
    "supported": "可以支持",
    "standard": "需按法定标准计算",
    "unsupported": "可能不予支持",
}


@dataclass
class ItemFinding:
    item: str
    claimed: float
    status: str  # supported | standard | unsupported
    counted: float
    basis: str
    required: list[str] = field(default_factory=list)
    standard: str = ""
    missing_params: list[str] = field(default_factory=list)
    note: str = ""


def _has(strong: set[str], *names: str) -> bool:
    return any(name in strong for name in names)


# --------------------------------------------------------------------- 交通事故
def _medical(item, amount, strong, weak, answers) -> ItemFinding:
    if _has(strong, "医疗费票据"):
        return ItemFinding(
            item, amount, "supported", amount,
            "有医疗费票据，按医疗机构收款凭证金额认定",
            required=["医疗费票据"],
        )
    if _has(strong, "病历"):
        return ItemFinding(
            item, amount, "standard", 0.0,
            "有病历能证明治疗发生，但没有费用凭证",
            required=["医疗费票据", "病历"],
            standard="需用医疗机构出具的费用清单或对方认可来确定实际支出",
            missing_params=["医院费用清单或盖章的费用明细"],
        )
    return ItemFinding(
        item, amount, "unsupported", 0.0,
        "没有任何医疗费用凭证，举证不能",
        required=["医疗费票据", "病历"],
        note="可尝试向医院调取费用清单，否则该项难以支持",
    )


def _lost_income(item, amount, strong, weak, answers) -> ItemFinding:
    detail = answers.get("误工证明") or []
    has_proof = _has(strong, "误工证明")
    has_flow = _has(strong, "工资流水")
    if has_proof and (has_flow or "amount" in detail):
        return ItemFinding(
            item, amount, "supported", amount,
            "有误工证明并能量化收入减少，按实际减少的收入认定",
            required=["误工证明", "工资流水"],
        )
    if has_proof:
        return ItemFinding(
            item, amount, "standard", 0.0,
            "有误工证明，但未写明扣发金额、也没有收入流水佐证",
            required=["误工证明", "工资流水"],
            standard="按受诉法院所在地相同或相近行业上一年度职工平均工资 × 误工天数计算",
            missing_params=["误工天数", "当地相同或相近行业上一年度职工平均工资"],
        )
    return ItemFinding(
        item, amount, "unsupported", 0.0,
        "没有误工证明等材料，无法证明收入减少",
        required=["误工证明", "工资流水"],
        note="可补开单位误工证明或提供工资流水后再主张",
    )


def _nursing(item, amount, strong, weak, answers) -> ItemFinding:
    if _has(strong, "护理费票据"):
        return ItemFinding(
            item, amount, "supported", amount,
            "有护理费发票或服务协议，按实际支出认定",
            required=["护理费票据"],
        )
    if _has(strong, "病历"):
        return ItemFinding(
            item, amount, "standard", 0.0,
            "有医嘱或病历显示需要护理，但无护理费凭证",
            required=["护理费票据", "病历"],
            standard="参照当地护工从事同等级别护理的劳务报酬标准 × 护理期限计算",
            missing_params=["护理期限", "当地护工劳务报酬标准"],
        )
    return ItemFinding(
        item, amount, "unsupported", 0.0,
        "既无护理费凭证，也无需要护理的医嘱",
        required=["护理费票据", "病历"],
    )


def _transport(item, amount, strong, weak, answers) -> ItemFinding:
    if _has(strong, "交通费票据"):
        return ItemFinding(
            item, amount, "supported", amount,
            "有与就医对应的交通票据，按票据认定",
            required=["交通费票据"],
        )
    return ItemFinding(
        item, amount, "unsupported", 0.0,
        "没有交通费票据，通常不予支持",
        required=["交通费票据"],
        note="法院有时会酌情支持少量，但金额远低于主张额",
    )


def _nutrition(item, amount, strong, weak, answers) -> ItemFinding:
    if _has(strong, "医嘱营养证明"):
        return ItemFinding(
            item, amount, "standard", 0.0,
            "有病历或诊断证明写明需要加强营养",
            required=["医嘱营养证明"],
            standard="根据医嘱与伤情，按当地标准酌定营养期与每日金额",
            missing_params=["营养期", "当地营养费标准"],
        )
    return ItemFinding(
        item, amount, "unsupported", 0.0,
        "没有医嘱写明需要加强营养，通常不予支持",
        required=["医嘱营养证明"],
    )


def _disability(item, amount, strong, weak, answers) -> ItemFinding:
    if _has(strong, "伤残鉴定"):
        return ItemFinding(
            item, amount, "standard", 0.0,
            "有伤残鉴定意见，赔偿金额需按等级计算",
            required=["伤残鉴定"],
            standard="当地上一年度居民人均可支配收入 × 赔偿年限 × 伤残赔偿系数",
            missing_params=["伤残等级", "赔偿年限", "当地居民人均可支配收入"],
        )
    return ItemFinding(
        item, amount, "unsupported", 0.0,
        "没有伤残等级鉴定，无法主张残疾赔偿金",
        required=["伤残鉴定"],
        note="治疗终结后可申请伤残鉴定，构成伤残才可主张",
    )


# --------------------------------------------------------------------- 消费维权
def _goods_price(item, amount, strong, weak, answers) -> ItemFinding:
    if _has(strong, "订单截图"):
        return ItemFinding(
            item, amount, "supported", amount,
            "有订单凭证，按实际支付价款认定",
            required=["订单截图"],
        )
    return ItemFinding(
        item, amount, "unsupported", 0.0,
        "没有订单或支付凭证，无法认定实际支付金额",
        required=["订单截图"],
    )


def _punitive(item, amount, strong, weak, answers) -> ItemFinding:
    has_talk = _has(strong, "聊天记录")
    has_ad = _has(strong, "商品宣传页面")
    if has_talk and has_ad:
        return ItemFinding(
            item, amount, "supported", amount,
            "有宣传页面与沟通记录，欺诈要件有证据支撑",
            required=["聊天记录", "商品宣传页面"],
        )
    if has_talk or has_ad:
        return ItemFinding(
            item, amount, "standard", 0.0,
            "有部分证据，但欺诈要件尚不完整",
            required=["聊天记录", "商品宣传页面"],
            standard="需证明经营者存在虚假或误导性陈述且具有主观故意，方可按价款三倍计算",
            missing_params=["能证明虚假宣传的页面或记录", "官方检测报告或平台认定"],
        )
    return ItemFinding(
        item, amount, "unsupported", 0.0,
        "没有证明虚假宣传的材料，欺诈难以认定",
        required=["聊天记录", "商品宣传页面"],
    )


# --------------------------------------------------------------------- 劳动争议
def _wage(item, amount, strong, weak, answers) -> ItemFinding:
    if _has(strong, "工资流水"):
        return ItemFinding(
            item, amount, "supported", amount,
            "有工资流水，按实际发放情况认定",
            required=["工资流水"],
        )
    return ItemFinding(
        item, amount, "standard", 0.0,
        "没有工资流水，需按约定工资标准计算",
        required=["工资流水"],
        standard="按劳动合同约定的工资标准或用人单位工资表计算",
        missing_params=["劳动合同约定的工资标准", "欠发月份"],
    )


def _severance(item, amount, strong, weak, answers) -> ItemFinding:
    if _has(strong, "劳动合同") and _has(strong, "解除通知"):
        return ItemFinding(
            item, amount, "standard", 0.0,
            "劳动关系与解除事实有材料支撑，金额需按法定标准计算",
            required=["劳动合同", "解除通知"],
            standard="按工作年限与解除前十二个月平均工资计算经济补偿",
            missing_params=["工作年限", "解除前十二个月平均工资"],
        )
    return ItemFinding(
        item, amount, "unsupported", 0.0,
        "缺少劳动合同或解除通知，经济补偿难以支持",
        required=["劳动合同", "解除通知"],
    )


ASSESSORS = {
    ("交通事故人身损害赔偿", "医疗费"): _medical,
    ("交通事故人身损害赔偿", "误工费"): _lost_income,
    ("交通事故人身损害赔偿", "护理费"): _nursing,
    ("交通事故人身损害赔偿", "交通费"): _transport,
    ("交通事故人身损害赔偿", "营养费"): _nutrition,
    ("交通事故人身损害赔偿", "伤残赔偿金"): _disability,
    ("消费维权", "商品价款"): _goods_price,
    ("消费维权", "惩罚性赔偿"): _punitive,
    ("劳动争议", "工资及加班费"): _wage,
    ("劳动争议", "经济补偿"): _severance,
}


def _default(item, amount, strong, weak, answers) -> ItemFinding:
    if strong:
        return ItemFinding(
            item, amount, "supported", amount,
            "有材料支撑，按主张金额认定（需人工复核）",
        )
    return ItemFinding(
        item, amount, "unsupported", 0.0,
        "没有材料支撑，举证不能",
    )


def assess_items(
    cause: str,
    claimed: dict[str, float],
    strong: set[str],
    weak: set[str],
    quality_answers: dict[str, list[str]] | None = None,
) -> list[ItemFinding]:
    answers = quality_answers or {}
    findings: list[ItemFinding] = []
    for item, amount in claimed.items():
        assessor = ASSESSORS.get((cause, item), _default)
        findings.append(assessor(item, amount, strong, weak, answers))
    return findings


def sufficiency(findings: list[ItemFinding]) -> float:
    """材料齐备度：按主张金额加权，1 表示每一项都有材料支撑。"""

    if not findings:
        # 一项金额都没有时，谈不上「齐备」，不能给满分
        return 0.0
    total = sum(item.claimed for item in findings)
    if total <= 0:
        return 1.0
    weight = {"supported": 1.0, "standard": 0.5, "unsupported": 0.0}
    scored = sum(item.claimed * weight.get(item.status, 0.0) for item in findings)
    return round(scored / total, 3)

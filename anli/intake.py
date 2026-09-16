"""案情理解：把用户的原话和证据材料，转成结构化案情。

当前是规则版实现：靠关键词、金额与证据词表抽取。
接入大模型后，本模块的职责不变，只是把规则替换成模型抽取 + 规则校验。
"""

from __future__ import annotations

import re

from .models import CaseProfile
from .item_assessment import assess_items, sufficiency

# --------------------------------------------------------------------- 案由识别
CAUSE_KEYWORDS: dict[str, list[str]] = {
    "交通事故人身损害赔偿": [
        "车祸", "交通事故", "被撞", "撞伤", "机动车", "电动车", "小车", "司机",
        "认定书", "交强险", "责任认定",
    ],
    "消费维权": [
        "网购", "商家", "退款", "退货", "假货", "翻新", "虚假宣传", "三倍",
        "12315", "商品", "客服", "拍下", "订单",
    ],
    "劳动争议": [
        "公司", "工资", "辞退", "开除", "加班", "劳动仲裁", "离职", "社保", "劳动合同",
    ],
    "租赁纠纷": ["租房", "房东", "押金", "房租", "中介", "退租"],
    "民间借贷": ["借钱", "借款", "欠款", "借条", "利息", "还钱"],
}

# --------------------------------------------------------------------- 证据词表
EVIDENCE_VOCAB: dict[str, list[str]] = {
    "事故认定书": ["事故认定书", "责任认定书", "认定书"],
    "医疗费票据": ["医疗费", "医药费", "发票", "票据", "收费单", "住院费"],
    "病历": ["病历", "诊断", "出院小结", "门诊记录", "医嘱"],
    "误工证明": ["误工证明", "误工费", "单位证明", "请假证明"],
    "工资流水": ["工资流水", "银行流水", "工资条", "收入证明", "纳税记录"],
    "护理费票据": ["护理费", "护工", "护理发票", "护理证明"],
    "交通费票据": ["交通费", "打车", "车票", "打车票"],
    "医嘱营养证明": ["营养费", "加强营养", "医嘱营养"],
    "伤残鉴定": ["伤残鉴定", "伤残等级", "鉴定意见"],
    "订单截图": ["订单", "下单", "购买记录", "支付记录"],
    "商品宣传页面": ["宣传", "详情页", "页面截图", "广告", "直播回放"],
    "聊天记录": ["聊天记录", "微信", "客服聊天", "对话记录"],
    "官方检测报告": ["检测报告", "鉴定报告", "官方检测"],
    "劳动合同": ["劳动合同", "用工合同"],
    "考勤记录": ["考勤", "打卡记录"],
    "解除通知": ["解除通知", "辞退通知", "离职证明"],
    "书面合同": ["合同", "协议", "借条"],
}

# --------------------------------------------------------------------- 赔偿项目
# required：缺了就不计入可支持金额；strengthen：有则能提高金额
CLAIM_RULES: dict[str, dict[str, dict[str, list[str]]]] = {
    "交通事故人身损害赔偿": {
        "医疗费": {"required": ["医疗费票据"], "strengthen": ["病历"]},
        "误工费": {"required": ["误工证明"], "strengthen": ["工资流水"]},
        "护理费": {"required": ["护理费票据"], "strengthen": ["病历"]},
        "交通费": {"required": ["交通费票据"], "strengthen": []},
        "营养费": {"required": ["医嘱营养证明"], "strengthen": ["病历"]},
        "伤残赔偿金": {"required": ["伤残鉴定"], "strengthen": []},
    },
    "消费维权": {
        "商品价款": {"required": ["订单截图"], "strengthen": []},
        "惩罚性赔偿": {
            "required": ["聊天记录"],
            "strengthen": ["商品宣传页面", "官方检测报告"],
        },
    },
    "劳动争议": {
        "工资及加班费": {"required": ["工资流水"], "strengthen": ["考勤记录"]},
        "经济补偿": {"required": ["劳动合同", "解除通知"], "strengthen": ["工资流水"]},
    },
}

RESPONSIBILITY_PATTERNS = [
    (["全责", "全部责任", "对方全责"], 1.0),
    (["主要责任", "主责"], 0.7),
    (["同等责任"], 0.5),
    (["次要责任", "次责"], 0.3),
]

# 只有按过错分担责任的案由才谈「责任比例」。
# 消费维权按退一赔三处理、劳动争议按工资标准处理，都不存在责任比例分摊，
# 硬套一个百分比会把结论算错（例如把消费案的可支持金额打五折）。
RESPONSIBILITY_APPLIES: dict[str, bool] = {
    "交通事故人身损害赔偿": True,
    "消费维权": False,
    "劳动争议": False,
    "租赁纠纷": True,
    "民间借贷": False,
}


def responsibility_applies(cause: str) -> bool:
    return RESPONSIBILITY_APPLIES.get(cause, True)

AMOUNT_UNITS = {"万": 10000.0, "千": 1000.0, "元": 1.0, "块": 1.0}

# 出现这些字样，才算「有书面材料」，而不只是嘴上提到
DOC_MORPHEMES = ("书", "票", "据", "证", "明", "水", "图", "告", "定", "同", "录", "单")


def classify_cause(text: str) -> str:
    scores: dict[str, int] = {}
    for cause, keywords in CAUSE_KEYWORDS.items():
        scores[cause] = sum(1 for keyword in keywords if keyword in text)
    best = max(scores, key=lambda key: scores[key])
    return best if scores[best] > 0 else "一般民事纠纷"


def parse_responsibility(text: str, cause: str = "") -> float:
    if cause and not responsibility_applies(cause):
        # 该案由不按责任比例分摊，一律按 100% 处理
        return 1.0
    for keywords, ratio in RESPONSIBILITY_PATTERNS:
        if any(keyword in text for keyword in keywords):
            return ratio
    return 1.0


NEGATIONS = ("没有", "没", "无", "未", "缺", "尚未", "还没")


def extract_evidence(text: str) -> tuple[set[str], set[str]]:
    """返回 (有材料迹象的证据, 仅口头提及的证据)。

    判定规则：
    - 关键词前后出现「票、证、明、书、图、录」等字样 → 视为有材料；
    - 关键词前出现「没有、无、缺」等否定词 → 只算口头提及，不算有材料。
    """

    strong: set[str] = set()
    weak: set[str] = set()
    for evidence, keywords in EVIDENCE_VOCAB.items():
        for keyword in keywords:
            start = text.find(keyword)
            while start >= 0:
                end = start + len(keyword)
                before = text[max(0, start - 8) : start]
                after = text[end : end + 8]
                negated = any(word in before for word in NEGATIONS)
                documentary = any(mark in keyword for mark in DOC_MORPHEMES) or any(
                    mark in after for mark in DOC_MORPHEMES
                )
                if documentary and not negated:
                    strong.add(evidence)
                else:
                    weak.add(evidence)
                start = text.find(keyword, end)
    weak -= strong
    return strong, weak


def infer_evidence_from_filenames(names: list[str]) -> set[str]:
    joined = " ".join(names)
    strong, weak = extract_evidence(joined)
    # 用户主动上传的，一律视为强证据
    return strong | weak


# 金额抽取按案由隔离，避免把消费维权的项目算进交通事故案
ITEM_ALIASES: dict[str, dict[str, list[str]]] = {
    "交通事故人身损害赔偿": {
        "医疗费": ["医疗费", "医药费", "看病花", "住院花"],
        "误工费": ["误工费", "误工"],
        "护理费": ["护理费", "护工费"],
        "交通费": ["交通费", "打车费"],
        "营养费": ["营养费"],
        "伤残赔偿金": ["伤残赔偿金", "残疾赔偿金"],
    },
    "消费维权": {
        # 被告视角常说「订单金额 / 商品金额 / 货款」，这些同样指商品价款
        "商品价款": [
            "商品价款", "订单金额", "商品金额", "货款", "手机花了",
            "买的时候花了", "一共花了", "花了",
        ],
        # 只认明确写出金额的说法；「退一赔三」按商品价款三倍推算，避免把总诉求重复计一遍
        "惩罚性赔偿": ["三倍赔偿", "惩罚性赔偿", "三倍价款"],
    },
    "劳动争议": {
        "工资及加班费": ["工资", "加班费"],
        "经济补偿": ["经济补偿", "赔偿金"],
    },
}

NUMBER = r"(\d+(?:\.\d+)?)\s*(万|千|元|块)?"


def extract_amounts(text: str, cause: str) -> dict[str, float]:
    """按案由抽取各项金额，例如「医疗费 3200 元」。

    只认本案件类型下定义过的项目；认不出的金额一律忽略，宁可少算也不乱算。
    """

    amounts: dict[str, float] = {}
    for item, aliases in ITEM_ALIASES.get(cause, {}).items():
        for alias in aliases:
            match = re.search(re.escape(alias) + r"[^\d]{0,8}" + NUMBER, text)
            if match:
                amounts[item] = float(match.group(1)) * AMOUNT_UNITS.get(
                    match.group(2) or "元", 1.0
                )
                break
    return amounts


def extract_expectation(text: str) -> float:
    """从原话里识别用户的心理预期金额，例如「我想要他赔我 12000 元」。"""

    values: list[float] = []
    pattern = r"(?:想要|要求|索赔|赔我|一共|总共|希望|应该赔|赔偿)[^\d]{0,8}" + NUMBER
    for match in re.finditer(pattern, text):
        values.append(
            float(match.group(1)) * AMOUNT_UNITS.get(match.group(2) or "元", 1.0)
        )
    return max(values) if values else 0.0


def infer_implied_items(cause: str, amounts: dict[str, float], text: str) -> dict[str, float]:
    """补齐用户提到但没直接给出金额的项目，目前只处理「退一赔三」。"""

    if (
        cause == "消费维权"
        and re.search(r"退一赔三|假一赔三|三倍", text)
        and "商品价款" in amounts
        and "惩罚性赔偿" not in amounts
    ):
        amounts = dict(amounts)
        amounts["惩罚性赔偿"] = amounts["商品价款"] * 3
    return amounts


def findings_to_profile_parts(cause: str, findings) -> tuple[dict, dict, dict, list[str]]:
    """把逐项认定结果落回案情结构。"""

    rules = CLAIM_RULES.get(cause, {})
    supported: dict[str, float] = {}
    blocked: dict[str, dict[str, object]] = {}
    notes: list[str] = []
    for finding in findings:
        if finding.status == "supported":
            supported[finding.item] = finding.counted
            continue
        rule = rules.get(finding.item) or {"strengthen": []}
        blocked[finding.item] = {
            "amount": finding.claimed,
            "missing": list(finding.required),
            "strengthen": rule.get("strengthen", []),
            "kind": finding.status,
            "standard": finding.standard,
            "params": list(finding.missing_params),
            "basis": finding.basis,
        }
        notes.append(f"{finding.item}：{finding.basis}")
    return supported, blocked, {}, notes
def build_profile(
    description: str,
    file_names: list[str] | None = None,
    region: str = "",
    confirmed_evidence: list[str] | None = None,
    waived_evidence: list[str] | None = None,
    quality_answers: dict[str, list[str]] | None = None,
) -> CaseProfile:
    """把用户原话与上传材料转成结构化案情。"""

    text = description.strip()
    file_names = file_names or []
    cause = classify_cause(text + " " + " ".join(file_names))
    strong_text, weak_text = extract_evidence(text)
    evidence_from_files = infer_evidence_from_filenames(file_names)
    # 用户明确勾选「我已有这份材料」的，直接按有材料处理，不依赖文件名识别
    confirmed = {name for name in (confirmed_evidence or []) if name in EVIDENCE_VOCAB}
    strong = strong_text | evidence_from_files | confirmed
    weak = weak_text - strong
    evidence = strong | weak

    amounts = extract_amounts(text, cause)
    amounts = infer_implied_items(cause, amounts, text)
    unquantified = [
        item
        for item, aliases in ITEM_ALIASES.get(cause, {}).items()
        if item not in amounts and any(alias in text for alias in aliases)
    ]
    item_findings = assess_items(cause, amounts, strong, weak, quality_answers)
    supported, blocked, partial, notes = findings_to_profile_parts(cause, item_findings)

    sources: dict[str, str] = {}
    for item in sorted(evidence):
        if item in evidence_from_files and item in (strong_text | weak_text):
            sources[item] = "描述提及 + 已上传"
        elif item in evidence_from_files:
            sources[item] = "已上传"
        elif item in confirmed:
            sources[item] = "用户确认已有"
        elif item in strong:
            sources[item] = "描述中提及材料名称，待上传原件"
        else:
            sources[item] = "仅口头提及，待补材料"

    required = sorted({need for rule in CLAIM_RULES.get(cause, {}).values() for need in rule["required"]})
    missing = [item for item in required if item not in evidence]

    return CaseProfile(
        summary=text[:60] or "用户未提供案情描述",
        cause=cause,
        description=text,
        region=region,
        responsibility_ratio=parse_responsibility(text, cause),
        timeline=[],
        disputed_points=_disputed_points(cause, blocked),
        available_evidence=sorted(evidence),
        missing_evidence=missing,
        evidence_sources=sources,
        evidence_notes=notes,
        extra={
            "unquantified_items": unquantified,
            "waived_evidence": sorted(
                {name for name in (waived_evidence or []) if name in EVIDENCE_VOCAB}
            ),
            "quality_answers": dict(quality_answers or {}),
            "sufficiency": sufficiency(item_findings),
            # 该案由是否适用责任比例（消费维权、劳动争议不适用）
            "responsibility_applies": responsibility_applies(cause),
        },
        claim_items=supported,
        blocked_items=blocked,
        partial_items=partial,
        item_findings=item_findings,
    )


def _disputed_points(cause: str, blocked: dict[str, dict[str, object]]) -> list[str]:
    points = {
        "交通事故人身损害赔偿": ["赔偿项目范围", "责任比例", "误工费与护理费标准"],
        "消费维权": ["是否构成欺诈", "能否主张惩罚性赔偿"],
        "劳动争议": ["劳动关系认定", "解除是否合法", "补偿标准"],
    }.get(cause, ["责任划分", "损失范围"])
    for item in blocked:
        points.append(f"{item}能否获得支持")
    return points

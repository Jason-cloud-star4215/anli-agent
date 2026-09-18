"""案情理解：把用户的原话和证据材料，转成结构化案情。

当前是规则版实现：靠关键词、金额与证据词表抽取。
接入大模型后，本模块的职责不变，只是把规则替换成模型抽取 + 规则校验。
"""

from __future__ import annotations

import re

from .materials import cross_check_amounts, evidence_from_materials, summarize
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

RESPONSIBILITY_LEVELS: list[tuple[tuple[str, ...], float]] = [
    (("全部责任", "全责"), 1.0),
    (("主要责任", "主责"), 0.7),
    (("同等责任",), 0.5),
    (("次要责任", "次责"), 0.3),
    (("不承担责任", "不负责任", "没有责任", "无责任", "无责"), 0.0),
]

# 说「我负全责」和「对方负全责」是相反的结论，必须看主体。
# 这里取关键词**前面最近**的称呼：越近越可能是这句话的主语。
#
# 但「我」是谁取决于用户选的身份：主张方（原告）说「我」指受害方，
# 抗辩方（被告）说「我」指被索赔方。所以称谓表按身份分两套。
CLAIMANT_SELF = ("我方", "我", "本人", "自己", "原告", "受害人", "伤者", "申请人", "买家")
CLAIMANT_OTHER = ("对方", "被告", "肇事", "侵权人", "车主", "司机", "被申请人", "商家", "平台")
DEFENDANT_SELF = ("我方", "我", "本人", "自己", "被告", "答辩人", "本公司", "本店")
DEFENDANT_OTHER = ("对方", "原告", "受害人", "伤者", "申请人", "买家")

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


def parse_responsibility(text: str, cause: str = "", role: str | None = None) -> float:
    """被告（对方）应承担的责任比例。保留旧签名，内部走方向识别版。"""

    return parse_responsibility_detail(text, cause, role)[0]


def _subject_before(text: str, position: int, role: str | None = None, window: int = 10) -> str:
    """看关键词前面最近的称呼是谁：`self`（我方）/ `other`（对方）/ `""`（没说）。"""

    is_defendant = str(role) in ("defendant", "Role.DEFENDANT")
    self_hints = DEFENDANT_SELF if is_defendant else CLAIMANT_SELF
    other_hints = DEFENDANT_OTHER if is_defendant else CLAIMANT_OTHER
    segment = text[max(0, position - window) : position]
    subject = ""
    best_index = -1
    for hint in self_hints:
        index = segment.rfind(hint)
        if index > best_index:
            best_index, subject = index, "self"
    for hint in other_hints:
        index = segment.rfind(hint)
        if index > best_index:
            best_index, subject = index, "other"
    return subject


def parse_responsibility_detail(
    text: str, cause: str = "", role: str | None = None
) -> tuple[float, str]:
    """返回 (对方责任比例, 依据说明)。

    这个比例在产品里统一表示「对方（被告）应当承担的比例」，所以要判断主体：

    - 「对方负主要责任」→ 0.7；「我负主要责任」→ 0.3（对方只承担次要责任）；
    - 「我负全部责任 / 对方无责任」→ 0.0，也就是**向对方索赔没有责任基础**；
    - 只写程度不写主体时沿用旧口径（按程度取值）。

    以前只认程度不认主体，会把「我负全部责任」算成对方赔 100%——
    那是把责任判反了，属于会误导用户的严重错误。
    """

    if cause and not responsibility_applies(cause):
        # 该案由不按责任比例分摊，一律按 100% 处理
        return 1.0, "该案由不按责任比例分摊，按全额计算"

    is_defendant = str(role) in ("defendant", "Role.DEFENDANT")

    def defendant_share_of(subject: str, ratio: float) -> float:
        """把「谁承担多大比例」换算成产品统一口径：被告承担多少。"""

        if subject == "self":
            # 我方 = 原告（主张方）/ 被告（抗辩方）
            return ratio if is_defendant else round(1.0 - ratio, 2)
        if subject == "other":
            return round(1.0 - ratio, 2) if is_defendant else ratio
        return ratio  # 没写主体：沿用旧口径（按程度取被告比例）

    findings: list[tuple[str, str, float]] = []
    for keywords, ratio in RESPONSIBILITY_LEVELS:
        for keyword in keywords:
            start = text.find(keyword)
            while start >= 0:
                subject = _subject_before(text, start, role)
                findings.append((subject, keyword, defendant_share_of(subject, ratio)))
                start = text.find(keyword, start + len(keyword))

    if not findings:
        return 1.0, ""

    explicit = [item for item in findings if item[0]]
    subject, keyword, share = (explicit or findings)[0]
    # 说明里用当事人身份写清楚，别让法官视角看到「我方」这种第一人称
    if is_defendant:
        self_label, other_label = "我方（被告）", "对方（原告）"
    elif str(role) in ("judge", "Role.JUDGE"):
        self_label, other_label = "原告侧", "被告侧"
    else:
        self_label, other_label = "我方（原告）", "对方（被告）"
    if subject == "self":
        return share, f"按描述，{keyword}由{self_label}承担 → 被告承担 {share:.0%}"
    if subject == "other":
        return share, f"按描述，{keyword}由{other_label}承担 → 被告承担 {share:.0%}"
    return share, f"按描述提到{keyword}，未写清由谁承担，暂按程度取值（被告 {share:.0%}）"


def client_share_of(
    responsibility_ratio: float, cause: str, role: str | None
) -> tuple[float | None, bool]:
    """算出「我方承担多少」以及「我方是不是主要责任方」。

    结论摘要要先说哪一头，取决于谁承担主要责任：
    我方只担次要责任时，重点是「能拿多少」；我方担主要责任时，
    重点是「对方只赔多少、其余要自己扛」——这两句是完全不同的引导。
    法官视角没有「我方」，返回 (None, False)。
    """

    text_role = str(role)
    if text_role in ("defendant", "Role.DEFENDANT"):
        share: float | None = responsibility_ratio
    elif text_role in ("claimant", "Role.CLAIMANT"):
        share = round(1.0 - responsibility_ratio, 2)
    else:
        return None, False
    majority = responsibility_applies(cause) and share is not None and share > 0.5
    return share, majority


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
    waive_all: bool = False,
    material_facts: list | None = None,
    role: str | None = None,
) -> CaseProfile:
    """把用户原话与上传材料转成结构化案情。

    `role` 决定「我」是谁：主张方（原告）还是抗辩方（被告）。
    这不只是措辞问题——「我负主要责任」在两种身份下的含义正好相反，
    解析责任比例时必须知道用户站在哪一边。
    """

    text = description.strip()
    file_names = file_names or []
    cause = classify_cause(text + " " + " ".join(file_names))
    strong_text, weak_text = extract_evidence(text)
    evidence_from_files = infer_evidence_from_filenames(file_names)
    # 用户明确勾选「我已有这份材料」的，直接按有材料处理，不依赖文件名识别
    confirmed = {name for name in (confirmed_evidence or []) if name in EVIDENCE_VOCAB}
    # 上传材料的**正文**里读到的证据线索（以前只用文件名，正文从未被打开）
    material_tags = evidence_from_materials(material_facts or [])
    strong = strong_text | evidence_from_files | confirmed | material_tags
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
        if item in material_tags:
            # 正文里读到的比文件名可信，说明用户到底交了哪份材料
            sources[item] = (
                "描述提及 + 材料正文中读到"
                if item in (strong_text | weak_text)
                else "材料正文中读到（已上传）"
            )
        elif item in evidence_from_files and item in (strong_text | weak_text):
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

    # 材料解析结果要说给人听：读到了几份、哪几份读不到、金额对不上在哪
    material_summary = summarize(material_facts or [])
    material_notes: list[str] = []
    if material_facts:
        if material_summary["total"]:
            material_notes.append(material_summary["text"])
        material_notes += cross_check_amounts(material_facts, amounts)
        material_notes += [
            f"{item.name}：{item.reason}" for item in material_facts if not item.parsed
        ]

    responsibility_ratio, responsibility_basis = parse_responsibility_detail(
        text, cause, role
    )
    # 对方完全不担责（我方全责 / 对方无责任）：索赔缺乏责任基础，
    # 后面的结论、策略与文书都要据此改口径，不能继续按「能拿多少」讲
    claim_has_no_basis = responsibility_applies(cause) and responsibility_ratio <= 0.0
    client_share, client_bears_majority = client_share_of(responsibility_ratio, cause, role)

    return CaseProfile(
        summary=text[:60] or "用户未提供案情描述",
        cause=cause,
        description=text,
        region=region,
        responsibility_ratio=responsibility_ratio,
        timeline=[],
        disputed_points=_disputed_points(cause, blocked),
        available_evidence=sorted(evidence),
        missing_evidence=missing,
        evidence_sources=sources,
        evidence_notes=notes + material_notes,
        extra={
            "unquantified_items": unquantified,
            "waived_evidence": sorted(
                {name for name in (waived_evidence or []) if name in EVIDENCE_VOCAB}
            ),
            "quality_answers": dict(quality_answers or {}),
            "sufficiency": sufficiency(item_findings),
            # 材料正文解析结果（含未解析原因）：补交轮次直接复用，不重复解析
            "material_facts": [item.to_dict() for item in (material_facts or [])],
            "material_summary": material_summary,
            # 该案由是否适用责任比例（消费维权、劳动争议不适用）
            "responsibility_applies": responsibility_applies(cause),
            "responsibility_basis": responsibility_basis,
            "claim_has_no_basis": claim_has_no_basis,
            # 「我方承担多少」与「我方是不是主要责任方」：结论先讲哪一头看这两个
            "client_share": client_share,
            "client_bears_majority": client_bears_majority,
            # 用户明确表示「材料拿不出来，就按现有情况给结论」：
            # 这类参数（误工天数、当地工资标准等）不是证据名称，无法逐个 waive，
            # 只能用这个开关整体放行，否则补证清单永远消不掉。
            "waive_all": bool(waive_all),
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

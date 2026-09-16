"""证据链补强：告诉用户缺什么、值多少钱、去哪儿补。

产品原则：证据不完整时，智能体不给出「最终结论」，
而是把缺口变成一份可执行的补证清单，等用户补齐后继续算。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import CaseProfile, Role

# 每类证据的获取途径
HOW_TO_GET: dict[str, str] = {
    "事故认定书": "向处理事故的交警部门申请出具《道路交通事故认定书》",
    "医疗费票据": "到医院收费窗口或自助机打印费用清单和发票，含门诊与住院",
    "病历": "向就诊医院申请复印病历、诊断证明或出院小结",
    "误工证明": "请单位人事开具误工证明，写明请假起止时间与扣发工资",
    "工资流水": "到银行打印近 6–12 个月工资流水，或从个税 App 导纳税记录",
    "护理费票据": "保留护工费发票或家政服务协议与付款记录",
    "交通费票据": "保留打车行程单、发票，注意与就诊时间地点对应",
    "医嘱营养证明": "请医生在病历或诊断证明中写明需要加强营养",
    "伤残鉴定": "委托有资质的司法鉴定机构做伤残等级鉴定（一般在治疗终结后）",
    "订单截图": "截图订单详情页，包含下单时间、商品、金额与店铺信息",
    "商品宣传页面": "保存商品详情页、直播回放或广告页面，重点保留宣传表述",
    "聊天记录": "导出与商家的完整对话，含时间与账号信息",
    "官方检测报告": "送有资质的检测机构出具检测报告",
    "劳动合同": "向单位索要或从档案中调取劳动合同",
    "考勤记录": "申请调取考勤或打卡记录",
    "解除通知": "保留解除劳动合同通知书或离职证明",
    "书面合同": "找出合同或协议原件并拍照留存",
    "金额或计算依据": "提供该项的凭证或计算依据，例如工资流水、票据、鉴定意见",
}

# 每类材料的关键细节：光说「有」不够，答得越全，计入比例越高
QUALITY_QUESTIONS: dict[str, list[dict[str, object]]] = {
    "医疗费票据": [
        {"key": "formal", "label": "有正式发票或收费票据", "weight": 0.5},
        {"key": "amount", "label": "票面金额与主张金额能对上", "weight": 0.3},
        {"key": "related", "label": "属于本次事故的治疗费用", "weight": 0.2},
    ],
    "误工证明": [
        {"key": "written", "label": "单位出具了书面证明", "weight": 0.4},
        {"key": "period", "label": "写明了请假起止时间", "weight": 0.2},
        {"key": "amount", "label": "写明了扣发的工资金额", "weight": 0.2},
        {"key": "flow", "label": "能配合工资流水或个税记录", "weight": 0.2},
    ],
    "护理费票据": [
        {"key": "formal", "label": "有正式发票或服务协议", "weight": 0.6},
        {"key": "amount", "label": "金额与实际支付一致", "weight": 0.4},
    ],
    "医嘱营养证明": [
        {"key": "order", "label": "病历或诊断证明中写明需要加强营养", "weight": 0.7},
        {"key": "period", "label": "写明了营养期", "weight": 0.3},
    ],
    "交通费票据": [
        {"key": "formal", "label": "有正式票据或行程单", "weight": 0.5},
        {"key": "match", "label": "时间地点与就诊记录能对应", "weight": 0.5},
    ],
    "事故认定书": [
        {"key": "official", "label": "交警部门正式出具", "weight": 0.7},
        {"key": "ratio", "label": "写明了责任比例", "weight": 0.3},
    ],
    "伤残鉴定": [
        {"key": "qualified", "label": "由有资质的鉴定机构出具", "weight": 0.7},
        {"key": "grade", "label": "明确了伤残等级", "weight": 0.3},
    ],
    "订单截图": [
        {"key": "full", "label": "包含商品、金额、订单号", "weight": 0.6},
        {"key": "time", "label": "能看出下单时间", "weight": 0.4},
    ],
    "商品宣传页面": [
        {"key": "saved", "label": "保存了当时的宣传页面或直播回放", "weight": 0.6},
        {"key": "key", "label": "能看清具体宣传表述", "weight": 0.4},
    ],
    "聊天记录": [
        {"key": "full", "label": "是完整对话而不是片段", "weight": 0.5},
        {"key": "account", "label": "能看出对方账号与时间", "weight": 0.5},
    ],
    "官方检测报告": [
        {"key": "qualified", "label": "由有资质的检测机构出具", "weight": 0.8},
        {"key": "conclusion", "label": "结论明确指出质量问题", "weight": 0.2},
    ],
}


def quality_score(evidence: str, answers: list[str] | None) -> float:
    """把勾选的细节换算成 0–1 的质量分。"""

    questions = QUALITY_QUESTIONS.get(evidence) or []
    if not questions:
        return 0.75
    answers = answers or []
    score = sum(
        float(item["weight"]) for item in questions if item["key"] in answers
    )
    return round(min(score, 1.0), 3)


def quality_gaps(evidence: str, answers: list[str] | None) -> list[str]:
    """返回还没勾选的细节，作为继续补强的提示。"""

    answers = answers or []
    return [
        str(item["label"])
        for item in (QUALITY_QUESTIONS.get(evidence) or [])
        if item["key"] not in answers
    ]


@dataclass
class EvidenceRequest:
    item: str
    impact: float
    missing: list[str] = field(default_factory=list)
    why: str = ""
    how: list[str] = field(default_factory=list)
    status: str = "missing"  # missing | partial


def _missing_reason(item: str, amount: float, missing: list[str], role: Role) -> str:
    if role == Role.DEFENDANT:
        return (
            f"对方没有{'、'.join(missing)}，这 {amount:.0f} 元属于举证不能，"
            "你可以主张不予支持"
        )
    if role == Role.JUDGE:
        return f"该项尚缺{'、'.join(missing)}，事实未查清，属于需要双方举证的事项"
    return f"没有{'、'.join(missing)}，这 {amount:.0f} 元目前完全不能主张"


def _partial_reason(
    counted: float, gap: float, missing: list[str], role: Role
) -> str:
    base = f"目前只有口头说明，按 60% 计入 {counted:.0f} 元"
    if role == Role.DEFENDANT:
        return f"{base}；若对方补不出{'、'.join(missing)}，可争取这 {gap:.0f} 元不计入"
    if role == Role.JUDGE:
        return f"{base}；补齐{'、'.join(missing)}后可全额认定，差额 {gap:.0f} 元"
    return f"{base}；补上{'、'.join(missing)}可多主张约 {gap:.0f} 元"


def build_evidence_requests(
    profile: CaseProfile, role: Role = Role.CLAIMANT
) -> list[EvidenceRequest]:
    """按影响金额从大到小，列出需要补充的证据；措辞随身份变化。"""

    # 用户已经明确「这些材料拿不出来，就按现有情况给结论」：
    # 不再列补证清单，直接把流程放行到交付（结论里会写明哪些项目因此未计入）。
    if (profile.extra or {}).get("waive_all"):
        return []

    requests: list[EvidenceRequest] = []

    for item, info in (profile.blocked_items or {}).items():
        amount = float(info.get("amount") or 0)
        kind = str(info.get("kind") or "unsupported")
        basis = str(info.get("basis") or "")
        if kind == "standard":
            # 事实存在，但缺凭证或参数，需要按法定标准计算
            params = [str(name) for name in (info.get("params") or [])]
            requests.append(
                EvidenceRequest(
                    item=item,
                    impact=amount,
                    missing=params or ["计算参数"],
                    why=f"{basis}；按法定标准计算还缺参数：{'、'.join(params) or '未明确'}",
                    how=[f"提供{name}" for name in params] or ["补充计算所需参数"],
                    status="standard",
                )
            )
            continue
        missing = [str(name) for name in (info.get("missing") or [])] or ["相关材料"]
        requests.append(
            EvidenceRequest(
                item=item,
                impact=amount,
                missing=missing,
                why=f"{basis}；该项目可能全部不予支持",
                how=[HOW_TO_GET.get(name, f"补取{name}") for name in missing],
                status="unsupported",
            )
        )

    for item, info in (profile.partial_items or {}).items():
        full = float(info.get("full_amount") or 0)
        counted = float(info.get("counted_amount") or 0)
        gap = max(full - counted, 0.0)
        evidence_name = str(info.get("gap") or "")
        answers = (profile.extra or {}).get("quality_answers") or {}
        details = quality_gaps(evidence_name, answers.get(evidence_name))
        requests.append(
            EvidenceRequest(
                item=item,
                impact=gap,
                missing=[f"{evidence_name}的细节"],
                why=f"{evidence_name}要素不全，影响该项的认定方式",
                how=[f"补齐细节：{label}" for label in details],
                status="quality",
            )
        )

    for item in (profile.extra or {}).get("unquantified_items") or []:
        requests.append(
            EvidenceRequest(
                item=str(item),
                impact=0.0,
                missing=["金额或计算依据"],
                why=f"你提到了{item}，但没有给出金额，目前完全无法计入主张",
                how=[HOW_TO_GET["金额或计算依据"]],
                status="unquantified",
            )
        )

    waived = set((profile.extra or {}).get("waived_evidence") or [])
    if waived:
        requests = [
            request
            for request in requests
            if not (request.missing and all(name in waived for name in request.missing))
        ]

    requests.sort(key=lambda request: request.impact, reverse=True)
    return requests


def potential_cap(profile: CaseProfile) -> float:
    """如果材料与参数都补齐，可主张的上限（用于「补齐能拿多少」的对照）。"""

    return sum(float(getattr(item, "claimed", 0.0)) for item in (profile.item_findings or []))


def evidence_sufficiency(profile: CaseProfile) -> float:
    """材料齐备度：由逐项认定结果计算，1 表示每项都有材料支撑。"""

    return float((profile.extra or {}).get("sufficiency", 1.0))


def render_requests(
    requests: list[EvidenceRequest], role: Role = Role.CLAIMANT
) -> str:
    head = {
        Role.DEFENDANT: "以下材料的举证责任在对方，你可以要求对方提供；对方拿不出来，这部分主张不应被支持：",
        Role.JUDGE: "以下事实需要双方补充举证后才能认定：",
        Role.CLAIMANT: "你目前提供的材料还不足以支撑全部主张，请补充以下材料：",
    }[role]
    tail = {
        Role.DEFENDANT: "把对方的举证情况提交上来，系统会重新核算可以抗辩掉多少。",
        Role.JUDGE: "双方补充举证后提交，系统会重新核算合理裁判区间。",
        Role.CLAIMANT: "补齐后请继续提交，系统会重新计算可支持金额与最可能落点。",
    }[role]
    missing_label = "对方应提供" if role == Role.DEFENDANT else "缺"

    lines = [head, ""]
    for index, request in enumerate(requests, start=1):
        tag = {
            "missing": "完全缺失",
            "partial": "仅有口头说明",
            "unquantified": "缺金额",
            "quality": "细节不全",
            "standard": "需按标准计算",
            "unsupported": "举证不能",
        }.get(request.status, "待补充")
        if request.impact > 0:
            lines.append(
                f"{index}. 【{tag}】{request.item}｜涉及金额约 {request.impact:.0f} 元"
            )
        else:
            lines.append(f"{index}. 【{tag}】{request.item}｜金额未填")
        lines.append(f"   {missing_label}：{'、'.join(request.missing)}")
        lines.append(f"   原因：{request.why}")
        lines.append(
            ("   你可以要求：" if role == Role.DEFENDANT else "   怎么补：")
            + "；".join(request.how)
        )
        lines.append("")
    lines.append(tail)
    return "\n".join(lines)

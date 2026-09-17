"""材料正文抽取的公共层。

判决书提取工具（`tools/case_extractor.py`）与用户上传材料解析（`anli/materials.py`）
共用这里的正则与抽取函数，避免同一套规则各自演化、结果对不上。

抽取原则：**宁可返回空，也不猜**。抽不到就让调用方如实标注「未识别 / 待人工确认」，
不允许用相似字段或默认值顶替。
"""

from __future__ import annotations

import re

CN_DIGIT = "一二三四五六七八九十"

# 责任表述 → 比例。判决书里几乎不会直接写百分比，而是写「主要责任」这类表述。
RATIO_PATTERNS: list[tuple[tuple[str, ...], float]] = [
    (("全部责任", "全责"), 1.0),
    (("主要责任", "主责"), 0.7),
    (("同等责任",), 0.5),
    (("次要责任", "次责"), 0.3),
]

INJURY_PAT = re.compile(r"([一二三四五六七八九十]{1,2}级伤残|构成伤残)")
MONEY_PAT = re.compile(r"([0-9][\d,，]*(?:\.\d+)?)([万亿]?)元")
COURT_PAT = re.compile(
    r"([\u4e00-\u9fa5]{2,12}?(?:市|县|区|地区|盟|州))?[\u4e00-\u9fa5]{0,8}?(中级人民法院|人民法院)"
)
CASE_ID_PAT = re.compile(r"[（(]20\d{2}[）)][\u4e00-\u9fa5\d]{1,25}?号")
DATE_PAT = re.compile(r"(20\d{2})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})\s*日?")

ZHEJIANG_CITIES = [
    "杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴",
    "金华", "衢州", "舟山", "台州", "丽水",
]


def strip_num(text: str) -> float:
    """把「1,234.5」这类带千分位的数字串转成浮点。"""

    value = text.replace(",", "").replace("，", "")
    return float(value)


def _to_yuan(amount: float, unit: str) -> float:
    if unit == "万":
        return amount * 10000
    if unit == "亿":
        return amount * 100000000
    return amount


def find_amount_candidates(text: str, start_ratio: float = 0.0, limit: int = 5) -> list[float]:
    """正文里出现的金额候选（去重、从大到小）。

    只保留 100 元到 1 亿之间的数：判决书里有大量案号、页码、日期数字，
    不设范围会把「2024」这类当成金额。
    """

    start = int(len(text) * start_ratio) if start_ratio else 0
    candidates: list[float] = []
    for match in MONEY_PAT.finditer(text[start:]):
        value = _to_yuan(strip_num(match.group(1)), match.group(2))
        if 100 <= value <= 100000000:
            candidates.append(value)
    return sorted(set(candidates), reverse=True)[:limit]


def extract_case_id(text: str) -> tuple[str, int]:
    """取最后一个案号：文书互引的案号通常在前文，主文案号靠后。"""

    matches = CASE_ID_PAT.findall(text)
    if not matches:
        return "", 0
    case_id = matches[-1]
    year_match = re.search(r"(20\d{2})", case_id)
    return case_id, (int(year_match.group(1)) if year_match else 0)


def extract_court(text: str) -> str:
    """优先取文书开头整行的法院名（完整、不被切碎），失败再退回正则。"""

    for line in text.split("\n")[:5]:
        line = line.strip()
        if "人民法院" in line or "中级人民法院" in line:
            return re.sub(r"[\s\u3000]", "", line)
    match = COURT_PAT.search(text[:1200])
    if match:
        return (match.group(1) or "") + match.group(2)
    return ""


def extract_region(court: str, text: str = "") -> str:
    if not court:
        return ""
    for city in ZHEJIANG_CITIES:
        if city in court:
            return city
    match = re.search(r"([\u4e00-\u9fa5]{2,4}?)市", court)
    if match:
        return match.group(1)
    return ""


def extract_injury(text: str) -> tuple[str, bool]:
    """返回 (伤残等级, 是否有鉴定意见)。"""

    match = INJURY_PAT.search(text)
    if match:
        level = match.group(1)
        if not level.startswith("构成"):
            return level, True
        return "有伤残", True
    if re.search(r"不构成伤残|未构成伤残|不构成伤残等级", text):
        return "无伤残", False
    if "伤残鉴定" in text or "伤残等级" in text:
        return "", True
    return "无伤残", False


def extract_ratio(text: str) -> tuple[float, str]:
    """责任比例。返回 0.0 表示待人工确认（不要当成「无责任」）。"""

    for keywords, ratio in RATIO_PATTERNS:
        for keyword in keywords:
            if keyword in text:
                return ratio, keyword
    percents = re.findall(r"承担[^。。]{0,12}?(30|40|50|60|70|80|100)%", text)
    if percents:
        return int(percents[0]) / 100, percents[0] + "%"
    return 0.0, ""


def extract_total(text: str) -> tuple[float, list[float]]:
    """判赔总额：优先取「共计/合计」条款（判决主文的合计口径），否则取候选最大值。

    全部候选都留在返回值里供人工复核——单项赔偿金（如残疾赔偿金）往往
    比合计还大，只取最大值会选中错误数字。
    """

    tail = text[int(len(text) * 0.6):]
    candidates = find_amount_candidates(tail)
    total_match = re.search(
        r"(?:共计|合计|总计)[^0-9]{0,20}([0-9][\d,，]*(?:\.\d+)?)([万亿]?)元", tail
    )
    if total_match:
        value = _to_yuan(strip_num(total_match.group(1)), total_match.group(2))
        if 100 <= value <= 100000000:
            return value, candidates
    return (candidates[0] if candidates else 0.0), candidates


def extract_lost_wage(text: str) -> str:
    if "误工费" not in text:
        return "未涉及"
    window = ""
    for match in re.finditer(r"误工费", text):
        window += text[max(0, match.start() - 60): match.end() + 80]
    if re.search(r"(不予支持|未能提供|无法证明|没有证据|未提交)", window):
        return "未支持"
    if re.search(r"(实际减少|因误工|收入证明|工资流水|凭证)", window):
        return "按凭证认定"
    if re.search(r"(年平均工资|行业标准|相同或相近行业)", window):
        return "按行业标准计算"
    return "待人工确认"


def extract_evidence_tags(text: str) -> list[str]:
    """按证据词表命中的材料名称（不区分「有材料 / 仅口头提及」）。"""

    from .intake import EVIDENCE_VOCAB

    return [
        evidence
        for evidence, keywords in EVIDENCE_VOCAB.items()
        if any(keyword in text for keyword in keywords)
    ]


def extract_summary(text: str) -> str:
    match = re.search(r"本院认为(.{40,260}?。)", text)
    if match:
        return "本院认为" + match.group(1)
    return ""


def extract_dates(text: str) -> list[str]:
    """正文里出现过的日期，规范化为 YYYY-MM-DD，按出现顺序去重。"""

    seen: list[str] = []
    for year, month, day in DATE_PAT.findall(text):
        iso = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        if iso not in seen:
            seen.append(iso)
    return seen


def detect_case_type(text: str, override: str = "") -> str:
    if override:
        return override
    if re.search(r"交通事故|交强险|交警|碰撞|追尾|道路交通事故", text):
        return "交通事故人身损害赔偿"
    if re.search(r"退一赔三|欺诈|消费者权益|虚假宣传", text):
        return "消费维权"
    if re.search(r"劳动合同|劳动争议|经济补偿|拖欠.{0,6}工资", text):
        return "劳动争议"
    return ""

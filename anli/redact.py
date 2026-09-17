"""对外发送前的脱敏。

红线：不得把用户上传的敏感材料写进对外产物或第三方服务（`CLAUDE.md` 第 5 节）。
`anli/llm_intake.py` 把案情描述发给大模型之前，必须先过这里。

脱敏是**打码**不是**删除**：保留长度与结构，让模型仍能理解上下文，
但看不出具体是谁、卡号是多少。这样既守住红线，又不牺牲理解质量。
"""

from __future__ import annotations

import re

# 顺序有意义：18 位身份证要先处理，否则会被银行卡规则先吃掉。
RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "身份证号",
        re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3}[\dXx])(?!\d)"),
        r"\1********\3",
    ),
    (
        "手机号",
        re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)"),
        r"\1****\3",
    ),
    (
        "银行卡号",
        re.compile(r"(?<!\d)(\d{4})\d{8,11}(\d{4})(?!\d)"),
        r"\1********\2",
    ),
    (
        "邮箱",
        re.compile(r"([A-Za-z0-9._%+-]{1,3})[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})"),
        r"\1***@\2",
    ),
    (
        "详细住址",
        # 只打码「某省某市某区」之后的门牌部分，保留行政区划便于判断管辖。
        # 前缀用 {1,3} 贪婪匹配多级行政区划，否则会把「杭州市西湖区」一起吃掉；
        # 后半段必须带「路/街/巷/小区」等字样，避免把「杭州市西湖区人民法院」误伤。
        re.compile(
            r"((?:[\u4e00-\u9fa5]{2,10}?(?:省|自治区|市|区|县|旗)){1,3})"
            r"([\u4e00-\u9fa5]{1,12}(?:路|街|巷|大道|小区|村|组)[\u4e00-\u9fa5\d]{0,12})"
        ),
        r"\1***",
    ),
]

# 这些词前后出现的数字串也一并打码（如「账号 6222…」「卡号 …」）
LABELED_NUMBER = re.compile(
    r"((?:账号|帐号|卡号|账户|流水号|保单号|证件号|统一社会信用代码)[：:\s]{0,4})([A-Za-z0-9]{6,})"
)


def redact(text: str) -> str:
    """把敏感信息打码。只做替换，不删除任何句子结构。"""

    return redact_with_counts(text)[0]


def redact_with_counts(text: str) -> tuple[str, dict[str, int]]:
    """返回 (脱敏后文本, 各类型命中次数)，便于日志与自查。"""

    if not text:
        return text, {}
    counts: dict[str, int] = {}
    result = text
    for label, pattern, replacement in RULES:
        result, hit = pattern.subn(replacement, result)
        if hit:
            counts[label] = counts.get(label, 0) + hit
    result, hit = LABELED_NUMBER.subn(r"\1***", result)
    if hit:
        counts["带标签的编号"] = counts.get("带标签的编号", 0) + hit
    return result, counts


def has_sensitive(text: str) -> bool:
    """是否含敏感信息（用于提示用户「本次对外发送已脱敏」）。"""

    return bool(redact_with_counts(text)[1])


def describe(counts: dict[str, int]) -> str:
    """给用户看的一句话说明。"""

    if not counts:
        return "未发现需要打码的个人信息"
    parts = [f"{label} {number} 处" for label, number in counts.items()]
    return "已打码：" + "、".join(parts)

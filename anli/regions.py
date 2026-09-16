"""适用地区：民事裁判尺度各省不同，地域必须作为显式输入，而不是隐含在语料里。

只列大陆 31 个省级行政区。港澳台适用不同法域，本产品不覆盖。
"""

from __future__ import annotations

DEFAULT_REGION = "浙江省"

REGIONS: list[str] = [
    "北京市", "天津市", "河北省", "山西省", "内蒙古自治区",
    "辽宁省", "吉林省", "黑龙江省", "上海市", "江苏省",
    "浙江省", "安徽省", "福建省", "江西省", "山东省",
    "河南省", "湖北省", "湖南省", "广东省", "广西壮族自治区",
    "海南省", "重庆市", "四川省", "贵州省", "云南省",
    "西藏自治区", "陕西省", "甘肃省", "青海省", "宁夏回族自治区",
    "新疆维吾尔自治区",
]


def normalize_region(value: str | None) -> str:
    """把用户输入收敛到列表内的省份；不认识的输入一律回落到默认省份。"""

    text = (value or "").strip()
    if not text:
        return DEFAULT_REGION
    if text in REGIONS:
        return text
    for region in REGIONS:
        if text in region or region.startswith(text):
            return region
    return DEFAULT_REGION

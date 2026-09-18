"""演示与测试用案情。数据均为虚构，仅用于跑通流程。"""

from __future__ import annotations

from .intake import build_profile
from .models import CaseProfile


def car_accident_description() -> str:
    return (
        "6月10号我骑电动车被一辆小轿车撞了，交警出了事故认定书，对方主要责任。"
        "门诊治疗花了医疗费3200元，因为养伤误工费大概1800元，请护工花了护理费900元，"
        "打车去医院的交通费300元，医生说要补充营养，营养费400元。"
        "我想要对方一共赔我12000元。"
    )


def car_accident_files() -> list[str]:
    return ["事故认定书.jpg", "医疗费发票.jpg", "误工证明.jpg"]


def consumption_description() -> str:
    return (
        "8月1号我在网上买了一部手机，商家宣传是全新原装，8月4号收货后发现是翻新机。"
        "我有订单截图、商品宣传页面和客服聊天记录，8月5号申请退款被商家拒绝。"
        "手机花了2999元，我想要退一赔三，一共赔我11996元。"
    )


def consumption_files() -> list[str]:
    return ["订单截图.png", "商品宣传页面.png", "客服聊天记录.png"]


def car_accident_profile(region: str = "浙江省") -> CaseProfile:
    """主线演示案例：交通事故人身损害赔偿。"""
    return build_profile(car_accident_description(), car_accident_files(), region=region)


def car_accident_expectation() -> float:
    """用户主观期望：索赔一万二。"""

    return 12000.0


def car_full_description() -> str:
    """证据齐全版：每一项主张都有对应材料，用来演示「跑到结论」的样子。

    主线样例（car_accident）故意留了缺口，会停在「待补证」；
    这个样例把材料补齐，用来验证证据闸门放行后的结论、区间与文书。
    唯一没放进来的是营养费——它必须有医嘱写明的营养期才能算，
    缺参数就该停在待补，这属于闸门正常工作，不是缺陷。
    """

    return (
        "6月10号我骑电动车被一辆小轿车撞了，交警出了事故认定书，认定对方负全部责任。"
        "我门诊治疗花了医疗费3200元，有医疗费发票；误工费1800元，单位开了误工证明，也有工资流水；"
        "请护工花了护理费2100元，有护理费发票；打车去医院的交通费300元，有交通费票据。"
        "我想要对方一共赔我5500元。"
    )


def car_full_files() -> list[str]:
    """与上面的主张一一对应：每一项都有材料。"""

    return [
        "事故认定书.jpg",
        "医疗费发票.jpg",
        "误工证明.jpg",
        "工资流水.pdf",
        "护理费发票.jpg",
        "交通费票据.jpg",
    ]


def car_full_profile(region: str = "浙江省") -> CaseProfile:
    return build_profile(car_full_description(), car_full_files(), region=region)


def car_full_expectation() -> float:
    """期望落在类案现实区间之内：这样结论不会自相矛盾（可支持很高、但落点更低）。"""

    return 5500.0


def consumption_profile(region: str = "浙江省") -> CaseProfile:
    """第二个领域样例：消费维权。"""
    return build_profile(consumption_description(), consumption_files(), region=region)


def consumption_expectation() -> float:
    return 11996.0


def consumption_gap_description() -> str:
    """消费维权·证据不足版：少了商品宣传页面，欺诈要件不完整。

    与 consumption（证据充足）成对，用来看证据闸门在消费案里的表现。
    """

    return (
        "8月1号我在网上买了一部手机，商家客服说保证是全新原装，8月4号收货后发现是翻新机。"
        "我有订单截图和客服聊天记录，但下单时没保存商品宣传页面。"
        "8月5号申请退款被商家拒绝。手机花了2999元，我想要退一赔三，一共赔我11996元。"
    )


def consumption_gap_files() -> list[str]:
    return ["订单截图.png", "客服聊天记录.png"]


def consumption_gap_profile(region: str = "浙江省") -> CaseProfile:
    return build_profile(consumption_gap_description(), consumption_gap_files(), region=region)


def at_fault_description() -> str:
    """责任在我方（对方无责）：索赔缺乏责任基础，用来看结论怎么改口径。

    这是最容易被产品说反话的一档——如果只按「责任程度」判，
    「我负全部责任」会被算成对方赔 100%。
    """

    return (
        "6月10号我骑电动车撞了一辆小轿车，交警出了事故认定书，认定我负全部责任，对方无责任。"
        "我门诊治疗花了医疗费3200元，因为养伤误工费大概1800元，我想要对方一共赔我12000元。"
    )


def at_fault_files() -> list[str]:
    return ["事故认定书.jpg", "医疗费发票.jpg"]


def at_fault_profile(region: str = "浙江省") -> CaseProfile:
    return build_profile(at_fault_description(), at_fault_files(), region=region)


def at_fault_expectation() -> float:
    return 12000.0

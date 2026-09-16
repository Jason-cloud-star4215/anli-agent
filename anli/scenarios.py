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


def consumption_profile(region: str = "浙江省") -> CaseProfile:
    """第二个领域样例：消费维权。"""
    return build_profile(consumption_description(), consumption_files(), region=region)


def consumption_expectation() -> float:
    return 11996.0

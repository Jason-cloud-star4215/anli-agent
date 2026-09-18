"""结论矩阵自查：责任档位 × 身份 × 证据充足/不足，逐格打印结论首句。

为什么要有这个工具：
    「我负主要责任」这种判断错一次，用户就再也不会信这套结论。
    零散补丁挡不住这类问题——必须把组合穷举出来，人工看一眼每一格说的话对不对。

用法：
    python tools/responsibility_matrix.py            # 打印矩阵
    python tools/responsibility_matrix.py --only 全责  # 只跑含关键字的格子
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anli.intake import build_profile  # noqa: E402
from anli.models import Role, TaskState  # noqa: E402
from anli.orchestrator import Orchestrator  # noqa: E402
from anli.published_cases import PublishedCaseStore  # noqa: E402
from anli.state import StateStore  # noqa: E402
from anli.stores import CaseStore, LawStore  # noqa: E402

DATA = ROOT / "data"

ALL_EVIDENCE = ["事故认定书", "医疗费票据", "误工证明", "工资流水", "护理费票据", "交通费票据"]

# 每种身份下「责任怎么分」的五种说法（都从用户自己的角度写）
RESPONSIBILITY_WORDS = {
    "claimant": {
        "对方全责": "交警出了事故认定书，认定对方负全部责任",
        "对方主要责任": "交警出了事故认定书，认定对方负主要责任",
        "同等责任": "交警出了事故认定书，认定双方同等责任",
        "我方主要责任": "交警出了事故认定书，认定我负主要责任",
        "我方全责": "交警出了事故认定书，认定我负全部责任，对方无责任",
    },
    "defendant": {
        "对方全责": "交警出了事故认定书，认定对方（原告）负全部责任，我无责任",
        "对方主要责任": "交警出了事故认定书，认定对方（原告）负主要责任",
        "同等责任": "交警出了事故认定书，认定双方同等责任",
        "我方主要责任": "交警出了事故认定书，认定我负主要责任",
        "我方全责": "交警出了事故认定书，认定我负全部责任",
    },
    "judge": {
        "原告全责": "交警出了事故认定书，认定原告负全部责任",
        "原告主要责任": "交警出了事故认定书，认定原告负主要责任",
        "同等责任": "交警出了事故认定书，认定双方同等责任",
        "被告主要责任": "交警出了事故认定书，认定被告负主要责任",
        "被告全责": "交警出了事故认定书，认定被告负全部责任",
    },
}

CLAIM_TAIL = "医疗费花了3200元，误工费1800元，护理费2100元，交通费300元，要求赔偿12000元。"
DEFENSE_LEAD = "对方骑电动车与我的车发生碰撞，"


def build_orchestrator() -> Orchestrator:
    return Orchestrator(
        law_store=LawStore.load(DATA / "laws.seed.json"),
        case_store=CaseStore.load(
            DATA / "cases.seed.json",
            DATA / "cases.official.json",
            DATA / "real_cases.json",
        ),
        state_store=StateStore(),
        published_store=PublishedCaseStore.load(DATA / "cases.published.json"),
    )


def build_description(role: str, wording: str) -> str:
    if role == "defendant":
        return (
            "6月10号对方骑电动车与我的车发生碰撞，" + wording + "。"
            "对方现在要求我赔偿医疗费3200元、误工费1800元、护理费2100元、交通费300元，一共12000元。"
        )
    return "6月10号我骑电动车被小轿车撞了，" + wording + "。" + CLAIM_TAIL


def run_cell(orchestrator: Orchestrator, role: str, label: str, wording: str, evidence: list[str]):
    description = build_description(role, wording)
    profile = build_profile(description, evidence, region="浙江省", role=role)
    record = orchestrator.run(
        task_id=f"matrix-{role}-{label}-{'full' if evidence else 'gap'}",
        role=Role(role),
        profile=profile,
        expectation=12000.0,
    )
    summary = next(
        (item.body for item in record.artifacts if item.kind == "summary"), ""
    )
    document = next(
        (item.body for item in record.artifacts if item.kind == "document"), ""
    )
    strategy = next(
        (item.body for item in record.artifacts if item.kind == "strategy"), ""
    )
    first = summary.splitlines()[0].replace("一句话结论：", "") if summary else "（无摘要）"
    return {
        "role": role,
        "label": label,
        "evidence": "充足" if evidence else "不足",
        "ratio": profile.responsibility_ratio,
        "client_share": profile.extra.get("client_share"),
        "no_basis": profile.extra.get("claim_has_no_basis"),
        "majority": profile.extra.get("client_bears_majority"),
        "state": record.state.value if isinstance(record.state, TaskState) else record.state,
        "first": first,
        # 文书是否把「责任不在被告」说清楚：当事人文书用「重要提示」，
        # 法官视角的推演用「被告不承担责任」——同一件事的两种写法
        "has_warning": ("重要提示" in document) or ("被告不承担责任" in document),
        "asked_evidence": bool(record.extra.get("evidence_request")),
        "first_strategy": strategy.splitlines()[0][:60] if strategy else "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="结论矩阵自查")
    parser.add_argument("--only", default="", help="只跑标签里包含该关键字的格子")
    args = parser.parse_args(argv)

    orchestrator = build_orchestrator()
    rows: list[dict] = []
    for role, cases in RESPONSIBILITY_WORDS.items():
        for label, wording in cases.items():
            if args.only and args.only not in label and args.only not in role:
                continue
            for evidence in ([], ALL_EVIDENCE):
                rows.append(run_cell(orchestrator, role, label, wording, evidence))

    header = f"{'身份':<9}{'责任档':<12}{'材料':<6}{'我方%':>7}{'对方%':>7}  {'状态':<15}结论首句"
    print(header)
    print("-" * 150)
    for row in rows:
        # 注意：ratio 永远是「被告承担」，对被告本人来说那就是「我方」这一栏，
        # 直接印两个数字会被读反，所以这里按身份换算成「我方 / 对方」
        ratio_value = row["ratio"]
        if ratio_value is None:
            mine = opponent = None
        elif row["role"] == "defendant":
            mine, opponent = ratio_value, round(1.0 - ratio_value, 2)
        elif row["role"] == "claimant":
            opponent, mine = ratio_value, round(1.0 - ratio_value, 2)
        else:
            mine, opponent = None, ratio_value  # 法官视角：只看被告承担多少
        ratio = f"{mine:.0%}" if mine is not None else "—"
        share = f"{opponent:.0%}" if opponent is not None else "—"
        print(
            f"{row['role']:<9}{row['label']:<12}{row['evidence']:<6}"
            f"{ratio:>7}{share:>7}  {row['state']:<15}{row['first']}"
        )
    print()
    print(f"共 {len(rows)} 格。判定要点：ratio=0 必须说「缺乏责任基础」或「对你有利」；"
          "我方>50% 必须首句讲责任分摊；ratio=100% 必须讲对方全额承担。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

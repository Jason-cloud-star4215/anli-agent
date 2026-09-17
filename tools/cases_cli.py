"""实证类案库录入与统计命令行（供人工补录真实判决使用）。

三条铁律（详见《实证案例录入指南.md》）：
1. 线索可来自全网，入库必须回到判决书原文逐字段核验；
2. 来源链接必须能点开看到判决内容（短视频/新闻通稿链接不算）；
3. 找不到原文就放弃这条案例，宁可少而真。


补录流程（全程不依赖任何模型，金额与案号必须人工从判决书原文抄录）：

1. python tools/cases_cli.py template > draft.json     生成空白模板
2. 逐条填写字段；来源链接必须是能点开的公开渠道
   （人民法院案例库 / 裁判文书网 / 法院官网公开文书）
3. python tools/cases_cli.py import --file draft.json  校验并合并入库（按案号去重）
4. python tools/cases_cli.py validate                  查看库内校验问题
5. python tools/cases_cli.py stats                     预览样本量与金额区间
6. 人工对照判决书原文核对全部字段后，把该条记录的
   verification 从 "imported_pending" 改为 "human_verified"，
   它才会进入分析流程并标注 🟢 实证。

用法：
    python tools/cases_cli.py template
    python tools/cases_cli.py import --file draft.json
    python tools/cases_cli.py validate
    python tools/cases_cli.py list
    python tools/cases_cli.py stats [--cause 交通事故人身损害赔偿]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anli.real_cases import RealCaseStore, validate_record  # noqa: E402

DATA_FILE = ROOT / "data" / "real_cases.json"


def _load_raw() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _save_raw(records: list[dict]) -> None:
    DATA_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def cmd_template(_args: argparse.Namespace) -> int:
    store = RealCaseStore([])
    print(json.dumps(store.template(), ensure_ascii=False, indent=2))
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    incoming = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if not isinstance(incoming, list):
        incoming = [incoming]
    existing = _load_raw()
    known_ids = {item.get("case_id") for item in existing}
    added, skipped, rejected = 0, 0, 0
    for item in incoming:
        issues = validate_record(item)
        if issues:
            rejected += 1
            print(f"✗ 拒绝（{item.get('case_id', '?')}）：{'；'.join(issues)}")
            continue
        if item.get("case_id") in known_ids:
            skipped += 1
            print(f"– 跳过（{item.get('case_id')}）：案号已存在")
            continue
        existing.append(item)
        known_ids.add(item.get("case_id"))
        added += 1
        print(f"✓ 已录入（{item.get('case_id')}），状态：{item.get('verification')}")
    _save_raw(existing)
    print(f"\n完成：新增 {added} 条，跳过 {skipped} 条，拒绝 {rejected} 条；库内共 {len(existing)} 条。")
    print("提醒：imported_pending 状态的记录不会进入分析流程，"
          "对照判决原文核验后改为 human_verified。")
    return 0 if rejected == 0 else 1


def cmd_validate(_args: argparse.Namespace) -> int:
    store = RealCaseStore.load(DATA_FILE)
    if not store.records and not store.problems:
        print("库为空（data/real_cases.json）。先用 template 子命令生成模板开始补录。")
        return 0
    if store.problems:
        print("校验发现问题（这些问题条目已被忽略，不会进入流程）：")
        for problem in store.problems:
            print(f"  - {problem}")
    pending = [item for item in store.records if item.verification == "imported_pending"]
    verified = store.verified()
    print(f"库内共 {len(store.records)} 条：🟢 已核验 {len(verified)} 条，"
          f"待核验 {len(pending)} 条。")
    for item in pending:
        print(f"  待核验：{item.case_id}（{item.court}，{item.year}）")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    store = RealCaseStore.load(DATA_FILE)
    if not store.records:
        print("库为空。")
        return 0
    for item in store.records:
        mark = "🟢" if item.verification == "human_verified" else "⚪"
        print(f"{mark} {item.case_id}｜{item.court}｜{item.year}｜"
              f"{item.case_type}｜{item.injury_level}｜"
              f"判赔 {item.total_amount:.0f} 元｜责任 {item.responsibility_ratio:.0%}")
        print(f"   来源：{item.source_url}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    store = RealCaseStore.load(DATA_FILE)
    cause = args.cause or "交通事故人身损害赔偿"
    result = store.stats(cause=cause)
    if result is None:
        print(f"案由「{cause}」暂无实证样本。系统会诚实标注，不会拿演示数据冒充。")
        return 0
    print(f"案由「{cause}」实证样本：n={result['n']}")
    print(f"下四分位 {result['p25']:.0f} 元 ｜ 中位数 {result['median']:.0f} 元 ｜ "
          f"上四分位 {result['p75']:.0f} 元")
    if result["n"] < 3:
        print("⚠ 样本量小于 3，界面会强制标注「参考价值有限」。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="实证类案库录入与统计")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("template", help="输出空白录入模板").set_defaults(func=cmd_template)
    add = sub.add_parser("import", help="校验并合并录入 JSON 文件")
    add.add_argument("--file", required=True, help="待导入的 JSON 文件路径")
    add.set_defaults(func=cmd_import)
    sub.add_parser("validate", help="检查库内记录与核验状态").set_defaults(func=cmd_validate)
    sub.add_parser("list", help="列出全部记录").set_defaults(func=cmd_list)
    stat = sub.add_parser("stats", help="按案由输出样本量与金额区间")
    stat.add_argument("--cause", default="", help="案由，默认交通事故人身损害赔偿")
    stat.set_defaults(func=cmd_stats)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

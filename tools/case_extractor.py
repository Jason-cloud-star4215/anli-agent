"""判决书半自动提取器：把人工复制的判决书全文，解析成结构化的类案草稿。

定位：人负责「找到判决书、复制全文、最后核对」，机器负责其余一切。
这是人工录入与全自动爬虫之间的折中——既符合 PRD「不做无授权大规模爬取」
的非目标承诺，又把单条录入时间从 20–30 分钟压到 3–5 分钟。

用法：
    python tools/case_extractor.py --file 判决书1.txt [判决书2.txt ...]
    python tools/case_extractor.py --dir 存放判决书的文件夹
    # 可选：--case-type 交通事故人身损害赔偿  --region 杭州

输出：
    - 解析结果逐条打印（含"需人工确认"清单）
    - 追加写入 data/draft_从文书提取.json（按案号去重）
    - 全部记录 verification=imported_pending，人工核对后用
      tools/cases_cli.py import 入库，再升级 human_verified

诚实边界：机器提取的字段可能出错，total_amount 取候选值中最高者、
责任比例来自责任表述映射，都必须人工对照原文确认后才能升级核验状态。

抽取规则本身放在 `anli/material_text.py`：上传材料解析（`anli/materials.py`）
用的是同一套，避免两处规则各自演化。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anli.material_text import (  # noqa: E402
    detect_case_type,
    extract_case_id,
    extract_court,
    extract_evidence_tags,
    extract_injury,
    extract_lost_wage,
    extract_ratio,
    extract_region,
    extract_summary,
    extract_total,
)
from anli.real_cases import RealCaseStore  # noqa: E402

DRAFT_FILE = ROOT / "data" / "draft_从文书提取.json"


def extract_record(text: str, case_type_override: str = "", region_override: str = "") -> dict:
    case_id, year = extract_case_id(text)
    court = extract_court(text)
    region = region_override or extract_region(court, text)
    injury, disability_auth = extract_injury(text)
    ratio, ratio_basis = extract_ratio(text)
    total, candidates = extract_total(text)
    template = RealCaseStore([]).template()
    return {
        "case_id": case_id or "待补案号-" + str(abs(hash(text)) % 100000),
        "court": court,
        "year": year,
        "region": region,
        "case_type": detect_case_type(text, case_type_override),
        "injury_level": injury or "待人工确认",
        "total_amount": total,
        "responsibility_ratio": ratio if ratio > 0 else template["responsibility_ratio"],
        "court_level": ("中级人民法院" if "中级人民法院" in court else "基层人民法院") if court else "待人工确认",
        "disability_auth": disability_auth,
        "lost_wage_method": extract_lost_wage(text),
        "key_evidence_tags": extract_evidence_tags(text),
        "judge_reasoning_summary": extract_summary(text) or "待人工确认",
        "source_url": "",
        "verification": "imported_pending",
        "_candidates_total": candidates,
        "_ratio_basis": ratio_basis,
    }


def review_notes(record: dict) -> list[str]:
    """列出必须人工确认的字段。"""

    notes = []
    if record["case_id"].startswith("待补案号"):
        notes.append("未找到案号，请手工补填")
    if not record["court"]:
        notes.append("未识别到法院名称")
    if record["total_amount"] <= 0:
        notes.append("未提取到判赔总额，请从判决主文抄录")
    else:
        notes.append(f"判赔总额取候选最大值 {record['total_amount']:,.0f} 元，候选还有："
                     + "、".join(f"{c:,.0f}" for c in record["_candidates_total"][1:]) + "，请对照判决主文确认")
    if record["responsibility_ratio"] == 0.0 or not record["_ratio_basis"]:
        notes.append("未识别到责任比例，请从「本院认为」确认")
    else:
        notes.append(f"责任比例按「{record['_ratio_basis']}」映射为 {record['responsibility_ratio']:.0%}，请确认")
    if record["injury_level"] == "待人工确认":
        notes.append("未识别到伤残等级，请确认（无伤残请写「无伤残」）")
    if not record["judge_reasoning_summary"] or record["judge_reasoning_summary"] == "待人工确认":
        notes.append("未提取到「本院认为」说理，请手工摘写")
    if not record["source_url"]:
        notes.append("来源链接必填：请附判决书所在页面的网址")
    return notes


def merge_draft(new_records: list[dict]) -> tuple[list[dict], int]:
    existing = []
    if DRAFT_FILE.exists():
        existing = json.loads(DRAFT_FILE.read_text(encoding="utf-8"))
    known = {item.get("case_id") for item in existing}
    added = 0
    for record in new_records:
        if record["case_id"] in known:
            print(f"– 跳过（{record['case_id']}）：草稿中已存在")
            continue
        existing.append(record)
        known.add(record["case_id"])
        added += 1
    DRAFT_FILE.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return existing, added


def load_texts(args: argparse.Namespace) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    paths: list[Path] = []
    if args.file:
        paths += [Path(f) for f in args.file]
    if args.dir:
        paths += sorted(Path(args.dir).glob("*.txt"))
    for path in paths:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        if len(raw.strip()) < 200:
            print(f"✗ 跳过 {path.name}：内容过短，不像判决书全文")
            continue
        texts.append((path.name, raw))
    return texts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="判决书半自动提取")
    parser.add_argument("--file", nargs="*", help="判决书全文 txt 文件（可多个）")
    parser.add_argument("--dir", help="批量读取文件夹内全部 txt")
    parser.add_argument("--case-type", default="", help="强制指定案由（默认自动识别）")
    parser.add_argument("--region", default="", help="强制指定地域（默认从法院名推断）")
    args = parser.parse_args(argv)
    if not args.file and not args.dir:
        parser.print_help()
        return 1

    new_records = []
    for name, text in load_texts(args):
        record = extract_record(text, args.case_type, args.region)
        print(f"\n===== {name} =====")
        print(f"  案号：{record['case_id']} ｜ 法院：{record['court']} ｜ 年份：{record['year']}")
        print(f"  案由：{record['case_type']} ｜ 地域：{record['region']} ｜ 伤残：{record['injury_level']}"
              f" ｜ 鉴定：{'有' if record['disability_auth'] else '无'}")
        print(f"  判赔总额：{record['total_amount']:,.0f} 元 ｜ 误工费认定：{record['lost_wage_method']}"
              f" ｜ 证据标签：{'、'.join(record['key_evidence_tags']) or '未识别'}")
        notes = review_notes(record)
        print("  需人工确认：")
        for note in notes:
            print(f"    - {note}")
        new_records.append(record)

    existing, added = merge_draft(new_records)
    print(f"\n完成：新提取 {added} 条，草稿库共 {len(existing)} 条 → {DRAFT_FILE.name}")
    print("下一步：逐条人工核对上述提示项 → tools/cases_cli.py import --file data/draft_从文书提取.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from anli.real_cases import RealCaseStore  # noqa: E402

DRAFT_FILE = ROOT / "data" / "draft_从文书提取.json"

CN_DIGIT = "一二三四五六七八九十"
RATIO_PATTERNS = [
    (("全部责任", "全责"), 1.0),
    (("主要责任", "主责"), 0.7),
    (("同等责任",), 0.5),
    (("次要责任", "次责"), 0.3),
]
INJURY_PAT = re.compile(r"([一二三四五六七八九十]{1,2}级伤残|构成伤残)")
MONEY_PAT = re.compile(r"([0-9][\d,，]*(?:\.\d+)?)([万亿]?)元")
COURT_PAT = re.compile(r"([\u4e00-\u9fa5]{2,12}?(?:市|县|区|地区|盟|州))?[\u4e00-\u9fa5]{0,8}?(中级人民法院|人民法院)")
CASE_ID_PAT = re.compile(r"[（(]20\d{2}[）)][\u4e00-\u9fa5\d]{1,25}?号")


def _strip_num(text: str) -> float:
    value = text.replace(",", "").replace("，", "")
    return float(value)


def extract_case_id(text: str) -> tuple[str, int]:
    matches = CASE_ID_PAT.findall(text)
    if not matches:
        return "", 0
    # 取最后一个案号：文书互引的案号通常在前文，主文案号靠后
    case_id = matches[-1]
    year = int(re.search(r"(20\d{2})", case_id).group(1))
    return case_id, year


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


def extract_region(court: str, text: str) -> str:
    if not court:
        return ""
    ZHEJIANG = ["杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水"]
    for city in ZHEJIANG:
        if city in court:
            return city
    match = re.search(r"([\u4e00-\u9fa5]{2,4}?)市", court)
    if match:
        return match.group(1)
    return ""


def extract_injury(text: str) -> tuple[str, bool]:
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
    for keywords, ratio in RATIO_PATTERNS:
        for keyword in keywords:
            if keyword in text:
                return ratio, keyword
    percents = re.findall(r"承担[^。。]{0,12}?(30|40|50|60|70|80|100)%", text)
    if percents:
        return int(percents[0]) / 100, percents[0] + "%"
    return 0.0, ""  # 0 表示待人工确认，校验会拦下


def extract_total(text: str) -> tuple[float, list[float]]:
    """判赔总额：优先取「共计/合计」条款（判决主文的合计口径），否则取候选最大值。

    全部候选都留在返回值里供人工复核——单项赔偿金（如残疾赔偿金）往往
    比合计还大，只取最大值会选中错误数字。
    """

    tail = text[int(len(text) * 0.6):]
    candidates: list[float] = []
    for match in MONEY_PAT.finditer(tail):
        value = _strip_num(match.group(1))
        unit = match.group(2)
        if unit == "万":
            value *= 10000
        elif unit == "亿":
            value *= 100000000
        if 100 <= value <= 100000000:
            candidates.append(value)
    candidates = sorted(set(candidates), reverse=True)
    total_match = re.search(
        r"(?:共计|合计|总计)[^0-9]{0,20}([0-9][\d,，]*(?:\.\d+)?)([万亿]?)元", tail
    )
    if total_match:
        value = _strip_num(total_match.group(1))
        if total_match.group(2) == "万":
            value *= 10000
        if 100 <= value <= 100000000:
            return value, candidates
    return (candidates[0] if candidates else 0.0), candidates[:5]


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
    from anli.intake import EVIDENCE_VOCAB

    tags = []
    for evidence, keywords in EVIDENCE_VOCAB.items():
        if any(keyword in text for keyword in keywords):
            tags.append(evidence)
    return tags


def extract_summary(text: str) -> str:
    match = re.search(r"本院认为(.{40,260}?。)", text)
    if match:
        return "本院认为" + match.group(1)
    return ""


def detect_case_type(text: str, override: str) -> str:
    if override:
        return override
    if re.search(r"交通事故|交强险|交警|碰撞|追尾|道路交通事故", text):
        return "交通事故人身损害赔偿"
    if re.search(r"退一赔三|欺诈|消费者权益|虚假宣传", text):
        return "消费维权"
    if re.search(r"劳动合同|劳动争议|经济补偿|拖欠.{0,6}工资", text):
        return "劳动争议"
    return ""


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


def merge_draft(new_records: list[dict]) -> list[dict]:
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

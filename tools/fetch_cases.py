"""从最高人民法院官网抓取指导性案例，建成真实类案缓存。

为什么抓这里：
- 人民法院案例库（rmfyalk.court.gov.cn）需要登录，裁判文书网有反爬与脚本壳，
  只有最高法官网的「指导性案例」栏目是公开静态页，且内容最完整；
- 指导性案例经最高人民法院审判委员会讨论通过，带案号、关键词、裁判要点、裁判结果，
  正是「类案参照」最需要的形态。

抓什么：案例编号、标题、发布日期、关键词、裁判要点、基本案情、裁判结果、案号、金额描述。
不抓什么：金额样本。官方文字里的金额属于个案，案由又各不相同，
直接混进「类案金额区间」会制造假精度，因此只作为文字说明保留（amounts_note）。

用法：
    python tools/fetch_cases.py                # 默认抓 3 页（约 60 篇）
    python tools/fetch_cases.py --pages 5      # 抓更多页
    python tools/fetch_cases.py --list-only    # 只更新列表，不抓正文

输出：data/cases.official.json（含来源链接与抓取时间，可直接进报告）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cases.official.json"
BASE = "https://www.court.gov.cn"
LIST_TEMPLATE = BASE + "/shenpan/gengduo/{page}.html"
LAST_LIST_PAGE = 77  # 栏目首页页码，向下递减

SOURCE_NAME = "最高人民法院官网·指导性案例"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

CASE_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("交通事故人身损害赔偿", ["交通事故", "机动车", "交强险", "人身损害"]),
    ("消费维权", ["消费者", "欺诈", "退一赔三", "商品", "惩罚性赔偿"]),
    ("劳动争议", ["劳动", "工资", "竞业限制", "加班", "经济补偿", "工伤"]),
    ("知识产权", ["专利", "商标", "著作权", "知识产权", "商业秘密"]),
    ("合同纠纷", ["合同", "买卖", "租赁", "借款", "违约"]),
    ("婚姻家事", ["婚姻", "离婚", "抚养", "继承", "赡养"]),
]

SECTION_NAMES = ["关键词", "裁判要点", "基本案情", "裁判结果", "裁判理由", "相关法条", "裁判文书"]

# 刑事标记：本产品不做刑事案件，带这些标记的案例一律不入库
CRIMINAL_MARKS = [
    "刑事", "刑初", "刑终", "公诉机关", "判处有期徒刑", "交通肇事罪",
    "人民检察院提起公诉", "被告人",
]


def fetch(url: str, timeout: int = 20) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="ignore")


MARK_OPEN = "@@S@@"
MARK_CLOSE = "@@E@@"


def to_text(html: str) -> str:
    """把页面转成纯文本，并把 <strong>小标题</strong> 标成可切分的标记。

    官方页面的小标题是「全角空格 + 加粗」，不是【】方括号，
    所以先给加粗片段打标记，再统一去标签。
    """

    text = re.sub(r"<script[\s\S]*?</script>", " ", html)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text)
    text = re.sub(
        r"<strong>\s*([^<>]{1,24}?)\s*</strong>",
        lambda match: f"\n{MARK_OPEN}{match.group(1).strip()}{MARK_CLOSE}\n",
        text,
    )
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</(p|div|li|h\d)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&ldquo;", "“"),
        ("&rdquo;", "”"),
        ("&mdash;", "—"),
        ("&quot;", '"'),
    ):
        text = text.replace(entity, char)
    text = re.sub(r"[\u3000]+", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def split_sections(text: str) -> dict[str, str]:
    """按加粗小标题切段。"""

    pattern = re.compile(re.escape(MARK_OPEN) + r"(.{1,24}?)" + re.escape(MARK_CLOSE))
    marks = [(match.start(), match.group(1).strip()) for match in pattern.finditer(text)]
    sections: dict[str, str] = {}
    for index, (position, name) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        body = text[position:end]
        body = pattern.sub("", body).strip()
        if name in SECTION_NAMES or name not in sections:
            sections.setdefault(name, body)
    return sections


def guess_case_type(text: str) -> str:
    for case_type, keywords in CASE_TYPE_RULES:
        if any(keyword in text for keyword in keywords):
            return case_type
    return "其他民事纠纷"


CASE_NO_PATTERNS = [
    r"（\d{4}）[^，。；\s]{2,20}?号",
    r"\(\d{4}\)[^，。；\s]{2,20}?号",
]
AMOUNT_PATTERNS = [
    r"[\d,]+(?:\.\d+)?\s*万元",
    r"[\d,]{3,}\s*元",
]


def find_case_nos(text: str) -> list[str]:
    found: list[str] = []
    for pattern in CASE_NO_PATTERNS:
        for match in re.findall(pattern, text):
            cleaned = match.strip()
            if cleaned not in found:
                found.append(cleaned)
    return found


def find_amounts(text: str, limit: int = 6) -> list[str]:
    found: list[str] = []
    for pattern in AMOUNT_PATTERNS:
        for match in re.findall(pattern, text):
            cleaned = match.strip()
            if cleaned not in found:
                found.append(cleaned)
    return found[:limit]


def parse_detail(url: str, html: str, fetched_at: str) -> dict | None:
    text = to_text(html)
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    number_match = re.search(r"指导性案例\s*(\d+)\s*号", text)
    if not number_match or "裁判要点" not in text:
        return None
    number = number_match.group(1)
    raw_title = (title_match.group(1).strip() if title_match else "") or text[:120]
    name = re.sub(r"^指导性案例\s*\d+\s*号[：:\-—\s]*", "", raw_title)
    name = re.sub(r"\s*[-—_]\s*中华人民共和国最高人民法院\s*$", "", name).strip()
    name = name or f"指导性案例{number}号"
    published = re.search(r"发布时间：\s*(\d{4}-\d{2}-\d{2})", text)
    sections = split_sections(text)
    keywords = [
        item.strip()
        for item in re.split(r"[，,、；;/\s]+", sections.get("关键词", ""))
        if 2 <= len(item.strip()) <= 12
    ]
    points = sections.get("裁判要点", "").strip()
    facts = sections.get("基本案情", "").strip()
    result = sections.get("裁判结果", "").strip()
    holding_source = points or facts
    holding = re.sub(r"\s+", " ", holding_source)[:600]
    case_text = " ".join([name, points, facts, result, " ".join(keywords)])
    case_nos = find_case_nos(" ".join([facts, result]))
    # 真案例一定会引至少一个案号；只有编号没有案号的多半是「第 N 批发布」这类汇总页
    if not case_nos:
        return None
    # 刑事案件不入库：产品明确不覆盖刑事，混进来会污染「民事类案参照」
    if any(mark in case_text for mark in CRIMINAL_MARKS):
        return None
    amounts = find_amounts(" ".join([facts, result]), limit=6)
    year = int(published.group(1)[:4]) if published else 0
    return {
        "case_id": f"指导性案例{number}号",
        "court": "最高人民法院",
        "year": year,
        "region": "全国",
        "case_type": guess_case_type(case_text),
        "dispute": name[:120],
        "holding": holding or name[:120],
        "amounts": {},
        "evidence_points": keywords[:8],
        "source_url": url,
        "synthetic": False,
        "source_name": SOURCE_NAME,
        "case_no": "；".join(case_nos[:3]),
        "fetched_at": fetched_at,
        "keywords": keywords[:8],
        "amounts_note": "、".join(amounts),
        "title": (title_match.group(1).strip() if title_match else name)[:120],
    }


def collect_links(pages: int, delay: float) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for offset in range(pages):
        page = LAST_LIST_PAGE - offset
        url = LIST_TEMPLATE.format(page=page)
        try:
            html = fetch(url)
        except (urllib.error.URLError, OSError) as exc:
            print(f"[warn] 列表页取回失败 {url} :: {exc}")
            continue
        items = re.findall(
            r'href="(/shenpan/xiangqing/\d+\.html)"[^>]*>\s*([^<]{5,120})', html
        )
        # 同一栏目里混着新闻稿，只保留标题写明「指导性案例」的条目
        case_items = [
            (path, title) for path, title in items if "指导性案例" in title
        ]
        print(f"[list] {url} -> {len(items)} 条（其中指导性案例 {len(case_items)} 条）")
        for path, title in items:
            if "指导性案例" not in title:
                continue
            full = BASE + path
            if full in seen:
                continue
            seen.add(full)
            links.append((full, title.strip()))
        time.sleep(delay)
    return links


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取最高人民法院指导性案例并写入本地缓存")
    parser.add_argument("--pages", type=int, default=12, help="抓取列表页数（每页 20 条）")
    parser.add_argument("--limit", type=int, default=200, help="最多抓多少篇正文")
    parser.add_argument("--delay", type=float, default=0.4, help="每次请求之间的间隔秒数")
    parser.add_argument("--list-only", action="store_true", help="只抓列表不抓正文")
    args = parser.parse_args(argv)

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    links = collect_links(args.pages, args.delay)
    if not links:
        print("没有取到任何列表条目，可能是官网结构变化或网络不可用。")
        return 1
    if args.list_only:
        for url, title in links[:20]:
            print("  ", title[:60], "->", url)
        return 0

    records: list[dict] = []
    failed = 0
    for index, (url, title) in enumerate(links[: args.limit], start=1):
        try:
            html = fetch(url)
            record = parse_detail(url, html, fetched_at)
        except (urllib.error.URLError, OSError) as exc:
            failed += 1
            print(f"[warn] {index}/{len(links)} 取回失败 {url} :: {exc}")
            time.sleep(args.delay)
            continue
        if record:
            records.append(record)
            print(
                f"[{index:>3}/{min(len(links), args.limit)}] {record['case_id']} "
                f"{record['case_type']} {record['dispute'][:34]}"
            )
        else:
            failed += 1
            print(f"[warn] {index} 解析不到指导性案例结构：{title[:40]}")
        time.sleep(args.delay)

    if not records:
        print("没有解析出任何案例，未写入缓存。")
        return 1

    records.sort(key=lambda item: item["case_id"], reverse=True)
    OUT.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n已写入 {OUT}（{len(records)} 篇，失败 {failed} 篇，抓取时间 {fetched_at}）")
    by_type: dict[str, int] = {}
    for record in records:
        by_type[record["case_type"]] = by_type.get(record["case_type"], 0) + 1
    print("按案由分布：", "，".join(f"{k} {v}" for k, v in sorted(by_type.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())

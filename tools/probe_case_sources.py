"""R3 数据源侦察：把「还能从哪里拿到真实的民事判例」变成一份可复现的清单。

为什么要这个工具：
    最高法官网两个栏目现在只剩最后一页可达（其余页码 404，也没有翻页接口），
    类案缓存里一件交通事故人身损害赔偿都没有。要决定 R3 数据源路线，
    必须先知道「别的官方渠道到底能拿到什么」，而不是凭印象换源。

纪律（硬约束）：
    - 只访问公开页面，带正常 UA，单线程、限速（默认 0.6 秒/请求）；
    - 不登录、不注册、不绕过任何验证码或反爬机制；
    - 原始页面缓存到 cache/probe/，同一 URL 不重复取，复跑很快；
    - 只做抽样统计，不做全量镜像。

用法：
    python tools/probe_case_sources.py                  # 默认抽样规模
    python tools/probe_case_sources.py --pages 12       # 每个来源多抓几页列表
    python tools/probe_case_sources.py --details 80     # 每个来源最多取多少篇正文

输出：artifacts/r3_source_probe.md（可达性、条数、含金额比例、案由分布、样例链接）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from fetch_cases import (  # noqa: E402
    excluded_from_library,
    find_amounts,
    find_case_nos,
    guess_case_type,
    to_text,
)

CACHE = ROOT / "cache" / "probe"
OUT = ROOT / "artifacts" / "r3_source_probe.md"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


def source_list() -> list[dict]:
    """每个来源：列表页若干 + 从列表页里怎么认出正文链接。"""

    hn_pages = [
        f"https://www.hncourt.gov.cn/public/index.php?p={page}&LocationID=1200000000&sub="
        for page in range(0, 140)
    ]
    gd_pages = [
        "https://www.gdcourts.gov.cn/gsxx/quanweifabu/anlihuicui/index.html"
    ] + [
        f"https://www.gdcourts.gov.cn/gsxx/quanweifabu/anlihuicui/index_{page}.html"
        for page in range(2, 22)
    ]
    fj_pages = ["https://www.fjcourt.gov.cn/page/demand/typicalcasesList/1202.html"] + [
        f"https://www.fjcourt.gov.cn/page/demand/typicalcasesList/1202.html?page={page}"
        for page in range(2, 12)
    ]
    return [
        {
            "key": "hn_typical",
            "name": "河南法院网·典型案例",
            "kind": "省级法院典型案例",
            "pages": hn_pages,
            "item_re": r"detail\.php\?id=(\d+)",
            "item_url": "https://www.hncourt.gov.cn/public/detail.php?id={}",
        },
        {
            "key": "gd_anli",
            "name": "广东法院网·权威发布·案例荟萃",
            "kind": "省级法院权威发布",
            "pages": gd_pages,
            "item_re": r"content/post_(\d+)\.html",
            "item_url": "https://www.gdcourts.gov.cn/gsxx/quanweifabu/anlihuicui/content/post_{}.html",
        },
        {
            "key": "fj_civil",
            "name": "福建法院网·民事案例",
            "kind": "省级法院分案由案例",
            "pages": fj_pages,
            "item_re": r"/Page/Court/News/ArticleTradition/([0-9a-f\-]{20,})\.htm",
            "item_url": "https://www.fjcourt.gov.cn/Page/Court/News/ArticleTradition/{}.htm",
        },
    ]


def fetch(url: str, timeout: int = 12) -> str | None:
    """带磁盘缓存的取回。缓存让复跑不重复打扰对方服务器。"""

    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    path = CACHE / f"{key}.html"
    if path.exists():
        raw = path.read_bytes()
    else:
        request = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read()
        except (urllib.error.URLError, OSError):
            return None
        path.write_bytes(raw)
        time.sleep(DELAY)
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


DELAY = 0.6

LIKE_CAUSES = ("交通事故人身损害赔偿", "消费维权")
AMOUNT_RE = re.compile(r"[\d,]{2,}(?:\.\d+)?\s*(?:万元|元)")
# 列表标题里出现这些词，说明是我们要的民生侵权／消费类案例（用来估算存量）
TITLE_HINTS = ("交通事故", "道路", "人身损害", "赔偿", "消费者", "消费", "产品责任", "医疗")


def title_hits(html: str, item_re: str) -> list[str]:
    """从列表页里挑出标题命中民生类关键词的条目，用来估算可扩的量。"""

    hits: list[str] = []
    for match in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        if not re.search(item_re, match.group(1)):
            continue
        title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        if 6 <= len(title) <= 70 and any(hint in title for hint in TITLE_HINTS):
            hits.append(title)
    return hits


def analyse(url: str) -> dict | None:
    html = fetch(url)
    if not html:
        return None
    text = to_text(html)
    if len(text) < 200 or "出错啦" in text[:200]:
        return None
    amounts = find_amounts(text, limit=8)
    return {
        "url": url,
        "length": len(text),
        "cause": guess_case_type(text),
        "amounts": amounts,
        "case_nos": find_case_nos(text)[:2],
        "is_judgment": ("判决如下" in text or "本院认为" in text),
        "excluded": excluded_from_library(text),
        "title": (re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I).group(1).strip()
                  if re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) else "")[:80],
    }


def run(pages_per_source: int, details_per_source: int) -> dict:
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sources": []}
    for source in source_list():
        entry = {
            "key": source["key"],
            "name": source["name"],
            "kind": source["kind"],
            "pages_ok": 0,
            "item_ids": [],
            "samples": [],
            "title_hits": [],
        }
        print(f"\n== {source['name']} ==")
        seen: set[str] = set()
        for page_url in source["pages"][:pages_per_source]:
            html = fetch(page_url)
            if not html:
                print(f"  [列表失败] {page_url}")
                continue
            entry["pages_ok"] += 1
            entry["title_hits"].extend(title_hits(html, source["item_re"]))
            for item_id in re.findall(source["item_re"], html):
                if item_id not in seen:
                    seen.add(item_id)
                    entry["item_ids"].append(item_id)
            print(f"  [列表] {page_url[:78]} → 累计条目 {len(entry['item_ids'])}")
        entry["item_count"] = len(entry["item_ids"])
        entry["title_hit_count"] = len(set(entry["title_hits"]))
        for item_id in entry["item_ids"][:details_per_source]:
            sample = analyse(source["item_url"].format(item_id))
            if sample:
                entry["samples"].append(sample)
        print(f"  正文可取 {len(entry['samples'])} / 尝试 {min(len(entry['item_ids']), details_per_source)}")
        report["sources"].append(entry)
    return report


def render(report: dict) -> str:
    lines = [
        "# R3 数据源侦察报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        "- 口径：只访问公开页面，限速 0.6 秒/请求，不登录、不绕过反爬；原始页面缓存在 `cache/probe/`。",
        "",
        "| 数据源 | 类型 | 列表页可达 | 条目数 | 标题命中民生类 | 抽到正文 | 正文里带金额 | 命中交通事故/消费维权 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for source in report["sources"]:
        samples = source["samples"]
        with_amount = sum(1 for item in samples if item["amounts"])
        like = sum(1 for item in samples if item["cause"] in LIKE_CAUSES)
        lines.append(
            f"| {source['name']} | {source['kind']} | {source['pages_ok']} | "
            f"{source['item_count']} | {source.get('title_hit_count', 0)} | "
            f"{len(samples)} | {with_amount} | {like} |"
        )
    lines += ["", "## 逐源详情", ""]
    for source in report["sources"]:
        samples = source["samples"]
        causes = Counter(item["cause"] for item in samples)
        lines += [
            f"### {source['name']}",
            "",
            f"- 列表页可达：{source['pages_ok']} 页；累计条目：{source['item_count']} 条",
            f"- 标题命中民生类关键词（交通事故/赔偿/消费/医疗等）：{source.get('title_hit_count', 0)} 条",
            f"- 抽到正文：{len(samples)} 篇；案由分布："
            + ("，".join(f"{k} {v}" for k, v in causes.most_common()) or "无"),
            "",
        ]
        if source.get("title_hits"):
            lines += ["标题命中样例：", ""]
            lines += [f"- {title}" for title in sorted(set(source["title_hits"]))[:12]]
            lines.append("")
        if samples:
            lines += ["| 样例 | 案由 | 金额 | 案号 | 判决书体裁 |", "| --- | --- | --- | --- | --- |"]
            for item in samples[:12]:
                lines.append(
                    f"| [{item['title'][:34]}]({item['url']}) | {item['cause']} | "
                    f"{'、'.join(item['amounts'][:3]) or '—'} | "
                    f"{'、'.join(item['case_nos']) or '—'} | "
                    f"{'是' if item['is_judgment'] else '否（发布稿）'} |"
                )
            lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R3 数据源侦察")
    parser.add_argument("--pages", type=int, default=6, help="每个来源抓几页列表")
    parser.add_argument("--details", type=int, default=40, help="每个来源最多取多少篇正文")
    parser.add_argument("--delay", type=float, default=0.6, help="请求间隔秒数")
    args = parser.parse_args(argv)

    global DELAY
    DELAY = args.delay
    report = run(args.pages, args.details)
    markdown = render(report)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(markdown, encoding="utf-8", newline="\n")
    print("\n" + markdown)
    print(f"已写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

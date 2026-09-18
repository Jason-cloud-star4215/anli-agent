"""抓取官方「典型案例发布稿」并切分成单案，落成可检索的缓存。

定位（和实证库严格分开）：
    实证库 = 判决书全文 + 人工逐字段核验 → 只有它能驱动金额区间；
    本工具产出的 = 法院发布的**典型案例通报稿** → 只能作为「类案参照」的文本层与弱参考，
    报告里必须标明「官方发布稿（非判决书全文）」。

为什么值得做：
    《最高人民法院发布道路交通事故损害赔偿典型案例》《广东法院机动车交通事故责任纠纷典型案例》
    这类发布稿逐案给出责任划分、伤残等级、误工费认定和判赔金额，
    正好补上类案参照里「一件交通事故案例都没有」的窟窿。

纪律（硬约束）：
    - 只访问公开页面，带正常 UA，单线程、限速（默认 0.8 秒/请求）；
    - 不登录、不注册、不绕过任何验证码或反爬机制；
    - 原始页面缓存到 cache/published/，同一 URL 不重复取；
    - 刑事、行政案例一律不收（与类案库同一条红线）。

用法：
    python tools/fetch_published_cases.py                 # 默认规模
    python tools/fetch_published_cases.py --gd-pages 21 --hn-pages 30
    python tools/fetch_published_cases.py --limit 50      # 每个来源最多抓多少篇正文

输出：data/cases.published.json
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
)

CACHE = ROOT / "cache" / "published"
OUT = ROOT / "data" / "cases.published.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DELAY = 0.8

# 列表标题里出现这些词才值得抓正文（河南站里新闻与论文占绝大多数）
NEWSWORTHY = ("交通事故", "道路", "人身损害", "赔偿", "消费者", "消费", "产品责任", "医疗")

SECTION_LABELS = (
    "基本案情",
    "具体内容",
    "案情",
    "案例摘要",
    "裁判结果",
    "裁判要旨",
    "要旨",
    "裁判理由",
    "典型意义",
    "关键词",
    "案例索引",
)
# 有些发布稿把小节写成「一、基本案情」「二、裁判结果」——这类是栏目标题，不是案件标题
NUMBERED_LABEL_RE = re.compile(
    r"^[一二三四五六七八九十]{1,3}\s*、\s*(基本案情|案情|裁判结果|典型意义|裁判要旨|裁判理由|关键词|案例索引)\s*$"
)
CASE_HEADING_RE = re.compile(
    r"^(?:案例\s*[0-9一二三四五六七八九十]{1,3}|[一二三四五六七八九十]{1,3}\s*、\s*\S)"
)
RESPONSIBILITY_RE = re.compile(
    r"(?:承担|负)[^。；，,]{0,12}?(全部责任|主要责任|次要责任|同等责任)|"
    r"(\d{1,3})\s*%\s*(?:的)?(?:赔偿)?责任"
)


def _label_of(line: str) -> str | None:
    """一行是不是「基本案情／裁判结果」这类栏目标题？是就返回栏目名。"""

    stripped = line.strip("@").strip()
    if stripped in SECTION_LABELS:
        return stripped
    match = NUMBERED_LABEL_RE.match(stripped)
    return match.group(1) if match else None


def _case_heading(line: str) -> bool:
    """是不是「案例1」「一、某某某」这种案件起始行（排除栏目标题）。"""

    return bool(CASE_HEADING_RE.match(line)) and not _label_of(line) and not line.startswith("——")


def _looks_like_case_name(text: str) -> bool:
    """判断一行是不是案名（如「琚某诉程某、某保险公司机动车交通事故责任纠纷案」）。

    有的发布稿把案名放在要旨前面，有的相反，靠这个区分，
    免得把「要旨」和「案名」写反。
    """

    cleaned = re.sub(r"\s+", "", text or "")
    if len(cleaned) < 6 or not cleaned.endswith("案"):
        return False
    return ("诉" in cleaned) or ("与" in cleaned and "纠纷" in cleaned)


# 页面元素（浏览次数、来源、发布时间）被当成标题时，用案名兜底
CHROME_RE = re.compile(r"浏览次数|发布时间|来源[:：]|字号|上一篇|下一篇|打印本页")
# 刑事表述：只要案情里出现，这条就不收（产品只做民事，红线不碰）
CRIMINAL_EXTRA_RE = re.compile(
    r"有期徒刑|缓刑|罚金|公诉机关|被告人|犯罪嫌疑人|刑事拘留|刑事附带民事|追究刑事责任|刑事"
)


def looks_criminal(text: str) -> bool:
    """刑事、行政一律不收；这里比 fetch_cases 的标记更严一点。"""

    return excluded_from_library(text) or bool(CRIMINAL_EXTRA_RE.search(text))


def fetch(url: str, timeout: int = 15) -> str | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()[:16]}.html"
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


def to_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text)
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</(p|div|li|h\d|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&ldquo;", "“").replace("&rdquo;", "”")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def page_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return match.group(1).strip().split("_")[0][:120] if match else ""


def published_date(text: str) -> str:
    match = re.search(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def cut_cases(text: str) -> list[dict]:
    """把一篇发布稿切成单案。

    切分锚点是「——某某诉某某案」这一行：每一件案子都会有案名，
    而小节标题的排版则五花八门（广东有的篇目用「一、基本案情」，有的用「基本案情」）。

    要吃得下的三种排版：
      广东 A：「一、要旨式标题 / ——案名 / 基本案情 / 裁判结果 / 典型意义」
      广东 B：「要旨式标题 / ——案名 / 一、基本案情 / 二、裁判结果 / 三、典型意义」
      河南 ：「案例1 / 要旨式标题 / ——案名 / 【基本案情】/【裁判结果】/【典型意义】」
    """

    text = re.sub(r"【\s*([^】]{2,10}?)\s*】", r"\n@@\1@@\n", text)
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    anchors = [index for index, line in enumerate(lines) if line.startswith("——")]
    starts: list[int] = []
    if anchors:
        for anchor in anchors:
            position = anchor - 1
            hop = 0
            # 往上找标题行：跳过「案例1」和栏目标题，停在真正的要旨式标题上
            while position >= 0 and hop < 4:
                line = lines[position]
                if line.startswith("——") or _label_of(line) or re.match(r"^案例\s*\d{1,3}$", line):
                    position -= 1
                    hop += 1
                    continue
                break
            starts.append(position if position >= 0 else anchor)
        starts = sorted(set(starts))
    else:
        starts = [index for index, line in enumerate(lines) if _case_heading(line)]

    cases: list[dict] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = lines[start:end]
        if len(block) < 4:
            continue
        heading = re.sub(
            r"^(?:案例\s*\d{1,3}|[一二三四五六七八九十]{1,3}\s*、)\s*", "", block[0]
        ).strip()
        body_lines = block[1:]
        case_name = ""
        dash_text = ""
        # 案名一般在标题下一行（「——某某诉某某…案」），允许中间隔一行
        for offset, line in enumerate(body_lines[:3]):
            if line.startswith("——"):
                dash_text = line.lstrip("——").strip()
                body_lines = body_lines[:offset] + body_lines[offset + 1 :]
                break
        title = heading or (body_lines[0] if body_lines else "")
        if dash_text:
            # 两种排法都要认：要旨在前（——后是案名）／案名在前（——后是要旨）
            if _looks_like_case_name(heading) and not _looks_like_case_name(dash_text):
                case_name = heading
                title = dash_text
            else:
                case_name = dash_text
        # 兜底：标题落成了栏目名（排版异常时），用案名代替，避免出现「典型意义」这种假标题
        if title.strip("@").strip() in SECTION_LABELS and case_name:
            title = case_name
        sections: dict[str, list[str]] = {}
        current = "正文"
        for line in body_lines:
            label = _label_of(line)
            if label:
                current = label
                sections.setdefault(current, [])
                continue
            if _case_heading(line) or line.startswith("——"):
                continue
            sections.setdefault(current, []).append(line)
        joined = " ".join(" ".join(value) for value in sections.values())

        def pick(*names: str) -> str:
            """栏目标题各家写法不同，按优先级取第一个有内容的。"""

            for name in names:
                value = " ".join(sections.get(name, [])).strip()
                if value:
                    return value
            return ""

        cases.append(
            {
                "title": title[:120],
                "case_name": case_name[:120],
                "facts": pick("基本案情", "具体内容", "案情")[:1500],
                "holding": pick("裁判结果", "裁判要旨", "要旨", "案例摘要")[:1500],
                "meaning": " ".join(sections.get("典型意义", []))[:1200],
                "text": joined.strip(),
            }
        )
    return cases


def responsibility_of(text: str) -> str:
    found: list[str] = []
    for match in RESPONSIBILITY_RE.finditer(text):
        value = match.group(1) or (f"{match.group(2)}% 责任" if match.group(2) else "")
        if value and value not in found:
            found.append(value)
    return "；".join(found[:3])


def injury_of(text: str) -> str:
    found = re.findall(r"[一二三四五六七八九十]{1,3}\s*级伤残|无伤残|伤残等级[^。；，]{0,6}", text)
    return found[0].replace(" ", "") if found else ""


def court_of(text: str) -> str:
    found = re.findall(r"[\u4e00-\u9fa5]{2,10}(?:高级|中级)?人民法院", text)
    return found[0] if found else ""


def list_items(html: str, item_re: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for match in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        href = match.group(1)
        if not re.search(item_re, href):
            continue
        title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
        found = re.search(item_re, href)
        if found:
            items.append((found.group(1), title))
    return items


def collect(source: dict, pages: int, limit: int, fetched_at: str) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    for page_url in source["pages"][:pages]:
        html = fetch(page_url)
        if not html:
            continue
        for item_id, title in list_items(html, source["item_re"]):
            if item_id in seen:
                continue
            if source.get("filter_titles") and not any(word in title for word in NEWSWORTHY):
                continue
            seen.add(item_id)
    print(f"[{source['key']}] 列表命中 {len(seen)} 篇")
    for item_id in list(seen)[:limit]:
        url = source["item_url"].format(item_id)
        html = fetch(url)
        if not html:
            print(f"  [失败] {url}")
            continue
        text = to_text(html)
        if len(text) < 300:
            continue
        article_title = page_title(html)
        date = published_date(text)
        # 案由以**文章标题**为准：这类发布稿基本都是单一案由的专题汇编
        # （《广东法院机动车交通事故责任纠纷典型案例》），
        # 用正文关键词判会被「交强险」「汽车」这类泛词带偏
        article_cause = guess_case_type(article_title)
        for index, case in enumerate(cut_cases(text), start=1):
            if len(case["text"]) < 120:
                continue
            # 案情、结果、意义全都没有实质内容的（多为论文或答记者问），不进缓存
            if len(case["facts"]) + len(case["holding"]) + len(case["meaning"]) < 60:
                continue
            # 没有裁判结果的记录（判决书解析类的搬运稿）当不了「类案参照」，
            # 参照要的就是「法院怎么判的」
            if len(case["holding"]) < 20:
                continue
            title = case["title"]
            if CHROME_RE.search(title) or title.strip("@").strip() in SECTION_LABELS:
                title = case["case_name"] or title
            if not title.strip():
                continue
            probe_text = " ".join([article_title, case["title"], case["text"]])
            if looks_criminal(probe_text):
                continue
            records.append(
                {
                    "case_id": f"{source['key']}-{item_id}-{index}",
                    "source_key": source["key"],
                    "source_name": source["name"],
                    "source_url": url,
                    "fetched_at": fetched_at,
                    "published_date": date,
                    "article_title": article_title,
                    "index": index,
                    "title": title,
                    "case_name": case["case_name"],
                    "cause": (
                        article_cause
                        if article_cause != "其他民事纠纷"
                        else guess_case_type(probe_text)
                    ),
                    "region": source["region"],
                    "court": court_of(case["text"]),
                    "injury_level": injury_of(case["text"]),
                    "responsibility": responsibility_of(case["text"]),
                    "amounts": find_amounts(case["text"], limit=6),
                    "case_no": "；".join(find_case_nos(case["text"])[:2]),
                    "facts": case["facts"],
                    "holding": case["holding"],
                    "meaning": case["meaning"],
                    "tier": "published",
                    "note": "法院发布的典型案例稿，非判决书全文；金额与责任认定以原文为准",
                }
            )
        print(f"  [{len(records):>4}] {article_title[:44]}")
    return records


def sources(gd_pages: int, hn_pages: int, all_gd: bool = False) -> list[dict]:
    return [
        {
            "key": "gd_anli",
            "name": "广东法院网·权威发布·案例荟萃",
            "region": "广东",
            "pages": [
                "https://www.gdcourts.gov.cn/gsxx/quanweifabu/anlihuicui/index.html"
            ]
            + [
                f"https://www.gdcourts.gov.cn/gsxx/quanweifabu/anlihuicui/index_{page}.html"
                for page in range(2, gd_pages + 1)
            ],
            "item_re": r"content/post_(\d+)\.html",
            "item_url": "https://www.gdcourts.gov.cn/gsxx/quanweifabu/anlihuicui/content/post_{}.html",
            # 默认按标题过滤：广东站抓太多会被限速，且大量篇目与我们的 P0 案由无关
            "filter_titles": not all_gd,
        },
        {
            "key": "hn_typical",
            "name": "河南法院网·典型案例",
            "region": "河南",
            "pages": [
                f"https://www.hncourt.gov.cn/public/index.php?p={page}&LocationID=1200000000&sub="
                for page in range(0, hn_pages)
            ],
            "item_re": r"detail\.php\?id=(\d+)",
            "item_url": "https://www.hncourt.gov.cn/public/detail.php?id={}",
            "filter_titles": True,
        },
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取官方典型案例发布稿并切分成单案")
    parser.add_argument("--gd-pages", type=int, default=21, help="广东案例荟萃抓几页列表")
    parser.add_argument("--hn-pages", type=int, default=30, help="河南典型案例抓几页列表")
    parser.add_argument("--limit", type=int, default=220, help="每个来源最多抓多少篇正文")
    parser.add_argument(
        "--all-gd", action="store_true", help="广东站不做标题过滤（全站约 200 篇，易触发限速）"
    )
    parser.add_argument("--delay", type=float, default=0.8, help="请求间隔秒数")
    args = parser.parse_args(argv)

    global DELAY
    DELAY = args.delay
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    records: list[dict] = []
    for source in sources(args.gd_pages, args.hn_pages, all_gd=args.all_gd):
        records.extend(collect(source, len(source["pages"]), args.limit, fetched_at))

    records.sort(key=lambda item: (item["source_key"], item["case_id"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n"：Windows 上默认会把 \n 翻成 \r\n，与仓库的 LF 约定冲突
    OUT.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    by_source = Counter(item["source_name"] for item in records)
    by_cause = Counter(item["cause"] for item in records)
    with_amount = sum(1 for item in records if item["amounts"])
    p0 = sum(1 for item in records if item["cause"] in ("交通事故人身损害赔偿", "消费维权"))
    print(f"\n已写入 {OUT}")
    print(f"共 {len(records)} 件单案；带金额 {with_amount} 件；交通事故/消费维权 {p0} 件")
    print("按来源：", "，".join(f"{k} {v}" for k, v in by_source.items()))
    print("按案由：", "，".join(f"{k} {v}" for k, v in by_cause.most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

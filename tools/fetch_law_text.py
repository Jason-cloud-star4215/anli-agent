"""从权威公开页面抓取法条正文，并核验后写入法条库。

用法：
    python tools/fetch_law_text.py            # 只做抓取与报告，不写文件
    python tools/fetch_law_text.py --write    # 把核验成功的条文写回 data/laws.seed.json

设计原则：条文正文不靠模型记忆，只从权威来源抓取；抓不到就保持未核验状态。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAW_FILE = ROOT / "data" / "laws.seed.json"

SOURCES = [
    "http://www.xinhuanet.com/politics/2020-06/01/c_1126061072.htm",
    "http://www.xinhuanet.com/politics/2020-06/01/c_1126061072_2.htm",
    "http://www.xinhuanet.com/politics/2020-06/01/c_1126061072_3.htm",
    "http://www.xinhuanet.com/politics/2020-06/01/c_1126061072_4.htm",
    "http://www.xinhuanet.com/politics/2020-06/01/c_1126061072_5.htm",
    "http://www.xinhuanet.com/politics/2020-06/01/c_1126061072_6.htm",
    "http://www.xinhuanet.com/politics/2020-06/01/c_1126061072_7.htm",
    "http://www.npc.gov.cn/npc/c2/c30834/202006/t20200602_306457.html",
    "http://www.npc.gov.cn/npc/c2/c30834/202006/t20200602_306458.html",
    "http://www.npc.gov.cn/npc/c30834/202006/75ba6483b8344591abd07917e1d25cc8.shtml",
    "https://www.gov.cn/xinwen/2020-06/01/content_5516649.htm",
    "http://www.moj.gov.cn/pub/sfbgw/flfggz/flfggzflfg/202006/t20200602_181787.html",
    "http://www.xinhuanet.com/politics/2020-06/01/c_1126061072.htm",
]

# 待抓取的条文：起始标记 → 下一条标记
TARGETS = {
    "mfd-188": ("第一百八十八条", "第一百八十九条"),
    "mfd-1173": ("第一千一百七十三条", "第一千一百七十四条"),
    "mfd-1179": ("第一千一百七十九条", "第一千一百八十条"),
    "mfd-1213": ("第一千二百一十三条", "第一千二百一十四条"),
}


def fetch(url: str, timeout: int = 25) -> str | None:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        raw = response.read()
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def to_plain_text(html: str) -> str:
    text = re.sub(r"(?s)<script.*?</script>", "", html)
    text = re.sub(r"(?s)<style.*?</style>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return text


def extract_article(text: str, start: str, end: str) -> str | None:
    start_index = text.find(start)
    if start_index < 0:
        return None
    end_index = text.find(end, start_index + len(start))
    if end_index < 0:
        return None
    segment = text[start_index:end_index]
    segment = re.sub(r"\s+", "", segment)
    return segment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓取并核验法条正文")
    parser.add_argument("--write", action="store_true", help="写回 data/laws.seed.json")
    args = parser.parse_args(argv)

    found: dict[str, tuple[str, str]] = {}
    for url in SOURCES:
        try:
            html = fetch(url)
        except Exception as exc:  # noqa: BLE001
            print(f"ERR  {url} :: {exc}")
            continue
        if not html:
            print(f"SKIP {url} :: 无法解码")
            continue
        text = to_plain_text(html)
        hit_count = 0
        for provision_id, (start, end) in TARGETS.items():
            if provision_id in found:
                continue
            article = extract_article(text, start, end)
            if article:
                found[provision_id] = (article, url)
                hit_count += 1
        print(f"{'HIT ' if hit_count else 'MISS'} {url} :: 命中 {hit_count} 条")

    if not found:
        print("未从任何来源抓到条文正文。")
        return 1

    print()
    for provision_id, (article, url) in sorted(found.items()):
        print(f"--- {provision_id}（来源 {url}）")
        print(article[:200])

    if not args.write:
        return 0

    provisions = json.loads(LAW_FILE.read_text(encoding="utf-8"))
    updated = 0
    for item in provisions:
        hit = found.get(item["provision_id"])
        if not hit:
            continue
        item["text"] = hit[0]
        item["source_url"] = hit[1]
        item["verified"] = True
        item["note"] = "已从权威公开来源抓取并核验"
        updated += 1
    LAW_FILE.write_text(
        json.dumps(provisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n已写回 {updated} 条核验通过的条文 → {LAW_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

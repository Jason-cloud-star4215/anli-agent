"""把 web/index.src.html 打包成自包含的 web/index.html。

为什么需要这一步：
直接用浏览器打开本地 HTML 文件时，外链样式（/vendor/bootstrap.min.css）
会解析成 file:///vendor/... 而加载失败，页面就变成裸 HTML。
把样式内联进单文件后，无论走 HTTP 服务还是直接双击文件，样式都不会丢。

用法：
    python tools/build_web.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SRC = WEB / "index.src.html"
OUT = WEB / "index.html"
CSS = WEB / "vendor" / "bootstrap.min.css"

LINK = '<link rel="stylesheet" href="/vendor/bootstrap.min.css">'


def build() -> int:
    if not SRC.exists():
        print(f"缺少源文件：{SRC}")
        return 1
    if not CSS.exists():
        print(f"缺少样式文件：{CSS}")
        return 1

    html = SRC.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    if LINK not in html:
        print("源文件里没有找到样式占位链接，可能已经内联过了。")
        built = html
    else:
        built = html.replace(LINK, "<style>\n" + css + "\n</style>")

    OUT.write_text(built, encoding="utf-8")
    print(f"已生成自包含页面：{OUT}（{len(built.encode('utf-8'))} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(build())

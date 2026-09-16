"""统一处理资源路径，兼容源码运行与 PyInstaller 打包。

- 只读资源（web 页面、法条与类案数据）：打包后位于解包目录。
- 可写目录（state、cases、日志）：打包后位于 exe 同级目录，用户可随时查看。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """只读资源根目录。"""

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def user_root() -> Path:
    """可写目录与配置文件的根目录。"""

    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return bundle_root().joinpath(*parts)

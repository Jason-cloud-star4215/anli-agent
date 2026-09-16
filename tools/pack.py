"""把项目打包成可以直接发给别人的压缩包。

安全约定（重要）：
- 绝不包含 .env（里面有模型密钥）与任何本地运行数据（state/、cases/）；
- 附带 .env.example，收件人自己填密钥；
- 包含 dist/案理.exe，收件人不想装 Python 也能直接双击运行。

用法：
    python tools/pack.py
"""

from __future__ import annotations

import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 只打包这些内容，避免把密钥和本地数据带出去
INCLUDE_FILES = [
    "README.md",
    "PRD.md",
    "APP_FLOW.md",
    "FRONTEND_GUIDELINE.md",
    "BACKEND_STRUCTURE.md",
    "CLAUDE.md",
    "PROGRESS.md",
    "DEMO_SCRIPT.md",
    "app.py",
    "pyproject.toml",
    "启动案理.cmd",
    ".env.example",
    "dist/案理.exe",
]

INCLUDE_DIRS = ["anli", "data", "web", "tools", "tests"]

EXCLUDE_SUFFIXES = {".pyc", ".pyo"}
EXCLUDE_DIR_NAMES = {"__pycache__", "build", "state", "cases", "artifacts", "cache"}

# 双重保险：即使目录规则被改动，这些文件也永远不打包
HARD_EXCLUDE = {".env", "dist/.env"}


def iter_files() -> list[Path]:
    files: list[Path] = []
    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.exists():
            files.append(path)
    for dirname in INCLUDE_DIRS:
        base = ROOT / dirname
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix in EXCLUDE_SUFFIXES:
                continue
            if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
                continue
            files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    target = ROOT.parent / f"案理-项目包-{date.today().strftime('%Y%m%d')}.zip"
    files = iter_files()
    included: list[str] = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            if relative in HARD_EXCLUDE or path.name == ".env":
                continue
            archive.write(path, f"anli-agent/{relative}")
            included.append(relative)

    total_mb = target.stat().st_size / 1024 / 1024
    print(f"已生成压缩包：{target}")
    print(f"包含 {len(included)} 个文件，压缩后 {total_mb:.1f} MB")
    leaked = [name for name in included if name.endswith(".env")]
    if leaked:
        print(f"警告：检测到密钥文件被打包：{leaked}")
        return 1
    print("已确认：不包含 .env 与本地运行数据（state/、cases/）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

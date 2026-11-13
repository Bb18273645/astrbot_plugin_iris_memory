#!/usr/bin/env python3
"""把作者仓库的新提交同步到本 Fork，同时保证自有功能不被覆盖。

执行顺序：检查分支与工作区 -> 拉取 upstream -> 变基自有提交 -> 运行功能守卫
->（可选）完整测试 ->（可选）强制推送 master 与 my-fixes。

变基冲突时脚本会停下并提示手工解决，不会自动取任何一侧。

用法：
    python scripts/sync_upstream.py                # 同步并验证，不推送
    python scripts/sync_upstream.py --push         # 同步、验证并推送
    python scripts/sync_upstream.py --push --full-tests
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ASTRBOT_SOURCE = REPO.parent / "AstrBot-v4.29.4"

# Windows 控制台默认不是 UTF-8，统一输出编码避免中文提示乱码
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """在工作目录内运行 git 命令。"""
    return subprocess.run(
        ["git", *args],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=check,
    )


def stop(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def main() -> int:
    parser = argparse.ArgumentParser(description="同步上游并保护 Fork 自有功能")
    parser.add_argument("--push", action="store_true", help="验证通过后强制推送 master 与 my-fixes")
    parser.add_argument("--full-tests", action="store_true", help="额外运行完整测试套件")
    args = parser.parse_args()

    # 0. 只在发布分支上同步
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "master":
        stop(f"请在 master 分支上运行同步脚本（当前分支：{branch}）。")

    # 1. 工作区必须干净，避免变基中途丢失未提交改动
    dirty = git("status", "--porcelain").stdout.strip()
    if dirty:
        stop(f"工作区有未提交的改动，先提交或 stash 后再同步：\n{dirty}")

    # 2. 拉取上游
    print("拉取上游...")
    fetched = git("fetch", "upstream", "--prune", check=False)
    if fetched.returncode != 0:
        stop(f"拉取 upstream 失败：\n{fetched.stderr.strip()}")

    new_commits = git("log", "--oneline", "HEAD..upstream/master").stdout.strip()
    if not new_commits:
        print("上游没有新提交，无需同步。")
        return 0

    print("上游新增提交：")
    print("\n".join(f"  {line}" for line in new_commits.splitlines()))

    # 3. 把自有提交重放到新上游之上
    print("\n变基自有提交...")
    rebase = git("rebase", "upstream/master", check=False)
    if rebase.returncode != 0:
        print("变基冲突。请按 FORK.md 的冲突原则保留两侧功能，解决后执行：")
        print("  git add <文件> && git rebase --continue")
        print("放弃本次同步：")
        print("  git rebase --abort")
        print(rebase.stdout)
        print(rebase.stderr, file=sys.stderr)
        return 1

    # 4. 功能守卫（使用当前解释器，无需额外依赖 AstrBot）
    print("\n运行自有功能守卫...")
    guard = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/fork", "-q"],
        cwd=REPO,
    )
    if guard.returncode != 0:
        stop("自有功能守卫失败：补丁在上游更新中丢失，请修复后再推送。")

    # 5. 可选：完整测试
    if args.full_tests:
        source = os.environ.get("ASTRBOT_SOURCE") or str(DEFAULT_ASTRBOT_SOURCE)
        if not Path(source).is_dir():
            stop(f"找不到 AstrBot 源码目录：{source}（可用 ASTRBOT_SOURCE 指定）。")
        print(f"\n运行完整测试（PYTHONPATH={source}）...")
        env = dict(os.environ, PYTHONPATH=source)
        full = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=REPO,
            env=env,
        )
        if full.returncode != 0:
            print("完整测试存在失败，请确认是否为已知环境问题后再推送。")

    # 6. 可选：推送
    # 变基会重写自有提交的哈希，因此必须用 force-with-lease：
    # 它只在远端仍是本地已知的状态时才覆盖，不会误删别人的提交。
    if args.push:
        print("\n推送 master 与 my-fixes...")
        for refspec in ("master", "master:my-fixes"):
            pushed = git("push", "--force-with-lease", "origin", refspec, check=False)
            if pushed.returncode != 0:
                stop(f"推送 {refspec} 失败：\n{pushed.stderr.strip()}")
        print(
            "已推送。接下来在 AstrBot 面板：插件 -> Iris Memory -> 更多 -> 重新安装 -> 强制更新。"
        )
    else:
        print("\n同步完成（未推送）。确认无误后执行：")
        print("  git push --force-with-lease origin master")
        print("  git push --force-with-lease origin master:my-fixes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

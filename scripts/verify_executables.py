"""可执行文件交付物实际启动与参数响应自动验收脚本 (`scripts/verify_executables.py`)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
EXE = DIST / "WallFlow.exe"
SCR = DIST / "WallFlowSaver.scr"


def verify_all() -> bool:
    print("=== 开始对打包生成的可执行文件进行真实启动验收 ===")
    if not EXE.exists():
        print(f"❌ 找不到主程序: {EXE}")
        return False
    if not SCR.exists():
        print(f"❌ 找不到屏保程序: {SCR}")
        return False

    print(f"1. 验证 {EXE.name} --check (命令行快速调度检查)...")
    res1 = subprocess.run([str(EXE), "--check"], capture_output=True, text=True, timeout=12)
    print(f"   退出码: {res1.returncode} (标准输出: {res1.stdout.strip() or '无'})")
    if res1.returncode != 0:
        print("❌ 主程序 --check 异常退出")
        return False

    print(f"2. 验证 {EXE.name} --auto-close-ms 1200 (GUI 生命周期正常启动与退出)...")
    res2 = subprocess.run([str(EXE), "--minimized", "--auto-close-ms", "1200"], capture_output=True, text=True, timeout=15)
    print(f"   退出码: {res2.returncode}")
    if res2.returncode != 0:
        print("❌ 主程序 GUI 启动异常")
        return False

    print(f"3. 验证 {SCR.name} /c --auto-close-ms 1000 (屏保配置模式)...")
    res3 = subprocess.run([str(SCR), "/c", "--auto-close-ms=1000"], capture_output=True, text=True, timeout=15)
    print(f"   退出码: {res3.returncode}")
    if res3.returncode != 0:
        print("❌ 屏保 /c 模式异常退出")
        return False

    print(f"4. 验证 {SCR.name} /s --auto-close-ms 1000 (屏保全屏模式)...")
    res4 = subprocess.run([str(SCR), "/s", "--auto-close-ms=1000"], capture_output=True, text=True, timeout=15)
    print(f"   退出码: {res4.returncode}")
    if res4.returncode != 0:
        print("❌ 屏保 /s 模式异常退出")
        return False

    print("\n✅ 全部可执行文件验收项均正常通过！")
    return True


if __name__ == "__main__":
    success = verify_all()
    sys.exit(0 if success else 1)

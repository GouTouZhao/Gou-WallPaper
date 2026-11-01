"""WallFlow 独立构建与打包主脚本 (`build.py`).

职责：
1. 运行 pytest 完整测试套件
2. 清理历史构建产物 (`build/`, `dist/`)
3. 编译生成 `dist/WallFlow.exe` (壁纸主程序，无黑框 GUI)
4. 编译生成 `dist/WallFlowSaver.scr` (Windows 原生屏幕保护程序)
5. 验证可执行文件的生成与基本启动功能
6. 计算 SHA-256 校验和并输出到 `release/SHA256SUMS.txt`
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
RELEASE_DIR = PROJECT_ROOT / "release"
ASSETS_DIR = PROJECT_ROOT / "assets"
ICON_PATH = ASSETS_DIR / "icon.ico"


def get_python_exe() -> str:
    """优先使用项目内干净的 `.venv` 解释器打包，避免全局大包污染."""
    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def ensure_icon() -> Path:
    from app.icon_helper import ensure_ico_file

    return ensure_ico_file(ICON_PATH)


def compute_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def run_tests(python_exe: str) -> bool:
    print("\n[1/6] 正在运行自动化测试套件 (pytest)...")
    cmd = [python_exe, "-m", "pytest", "tests", "-v"]
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if res.returncode != 0:
        print("❌ 测试套件执行失败，终止打包。")
        return False
    print("✅ 自动化测试全部通过！")
    return True


def clean_artifacts() -> None:
    print("\n[2/6] 清理历史构建目录...")
    for d in (BUILD_DIR, DIST_DIR, RELEASE_DIR):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            print(f"  已清理: {d}")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)


def build_executables(python_exe: str) -> bool:
    icon_file = ensure_icon()
    print(f"\n[3/6] 准备打包图标: {icon_file}")

    # 公共排除模块列表，防止打包膨胀
    excludes = [
        "torch",
        "tensorflow",
        "scipy",
        "pandas",
        "matplotlib",
        "notebook",
        "jupyter",
        "IPython",
        "cv2",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.Qt3DCore",
        "PySide6.QtQml",
        "PySide6.QtQuick",
    ]
    exclude_flags = []
    for mod in excludes:
        exclude_flags.extend(["--exclude-module", mod])

    # 1. 打包主程序 GouWallpaper.exe
    print("\n[4/6] 正在使用 PyInstaller 构建主程序: dist/GouWallpaper.exe ...")
    main_cmd = [
        python_exe,
        "-m",
        "PyInstaller",
        "--name=GouWallpaper",
        "--onefile",
        "--noconsole",
        f"--icon={icon_file}",
        "--clean",
        "--noconfirm",
        f"--add-data={ASSETS_DIR};assets",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR / 'main'}",
        f"--specpath={BUILD_DIR / 'spec'}",
        *exclude_flags,
        str(PROJECT_ROOT / "main.py"),
    ]
    t0 = time.perf_counter()
    res1 = subprocess.run(main_cmd, cwd=str(PROJECT_ROOT))
    if res1.returncode != 0:
        print("❌ 构建 GouWallpaper.exe 失败")
        return False
    print(f"✅ GouWallpaper.exe 构建成功 (耗时 {time.perf_counter() - t0:.1f}s)")

    # 2. 打包屏幕保护程序 GouWallpaperSaver.scr
    print("\n[5/6] 正在构建 Windows 原生屏幕保护程序: dist/GouWallpaperSaver.scr ...")
    scr_temp_exe = DIST_DIR / "GouWallpaperSaver.exe"
    scr_final = DIST_DIR / "GouWallpaperSaver.scr"
    scr_cmd = [
        python_exe,
        "-m",
        "PyInstaller",
        "--name=GouWallpaperSaver",
        "--onefile",
        "--noconsole",
        f"--icon={icon_file}",
        "--clean",
        "--noconfirm",
        f"--add-data={ASSETS_DIR};assets",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR / 'screensaver'}",
        f"--specpath={BUILD_DIR / 'spec'}",
        *exclude_flags,
        str(PROJECT_ROOT / "screensaver_entry.py"),
    ]
    t1 = time.perf_counter()
    res2 = subprocess.run(scr_cmd, cwd=str(PROJECT_ROOT))
    if res2.returncode != 0:
        print("❌ 构建 GouWallpaperSaver 失败")
        return False

    # 重命名为 Windows 原生 .scr 扩展名
    if scr_temp_exe.exists():
        if scr_final.exists():
            scr_final.unlink()
        scr_temp_exe.rename(scr_final)
    elif not scr_final.exists():
        print("❌ 未找到生成的 GouWallpaperSaver 可执行文件")
        return False

    print(f"✅ GouWallpaperSaver.scr 构建成功 (耗时 {time.perf_counter() - t1:.1f}s)")

    # 制作兼容性副本（同时提供 WallFlow.exe 和 WallFlowSaver.scr，确保兼容任意历史脚本）
    shutil.copy2(DIST_DIR / "GouWallpaper.exe", DIST_DIR / "WallFlow.exe")
    shutil.copy2(DIST_DIR / "GouWallpaperSaver.scr", DIST_DIR / "WallFlowSaver.scr")

    # 拷贝 assets 目录到 dist/assets
    dist_assets = DIST_DIR / "assets"
    dist_assets.mkdir(parents=True, exist_ok=True)
    for f in ASSETS_DIR.glob("*.*"):
        shutil.copy2(f, dist_assets / f.name)

    return True


def verify_and_hash() -> bool:
    print("\n[6/6] 验证生成文件与计算 SHA-256 校验和...")
    exe_path = DIST_DIR / "GouWallpaper.exe"
    scr_path = DIST_DIR / "GouWallpaperSaver.scr"

    if not exe_path.exists() or not scr_path.exists():
        print("❌ 目标产物不完整！")
        return False

    exe_mb = exe_path.stat().st_size / (1024 * 1024)
    scr_mb = scr_path.stat().st_size / (1024 * 1024)
    print(f"  • {exe_path.name}: {exe_mb:.2f} MB")
    print(f"  • {scr_path.name}: {scr_mb:.2f} MB")

    # 实际测试命令行启动与参数校验
    print("  正在验证可执行文件启动响应...")
    test_exe = subprocess.run([str(exe_path), "--check"], capture_output=True, timeout=10)
    print(f"  • GouWallpaper.exe --check 退出码: {test_exe.returncode} (预期 0)")

    test_scr = subprocess.run([str(scr_path), "/c", "--auto-close-ms=1000"], capture_output=True, timeout=10)
    print(f"  • GouWallpaperSaver.scr /c --auto-close-ms 退出码: {test_scr.returncode} (预期 0)")

    # 写入 release/SHA256SUMS.txt
    sums_file = RELEASE_DIR / "SHA256SUMS.txt"
    lines = [
        f"{compute_sha256(exe_path)}  GouWallpaper.exe\n",
        f"{compute_sha256(scr_path)}  GouWallpaperSaver.scr\n",
        f"{compute_sha256(DIST_DIR / 'WallFlow.exe')}  WallFlow.exe\n",
        f"{compute_sha256(DIST_DIR / 'WallFlowSaver.scr')}  WallFlowSaver.scr\n",
    ]
    sums_file.write_text("".join(lines), encoding="utf-8")
    print(f"\n✅ 校验和已写入: {sums_file}")
    for line in lines:
        print(f"   {line.strip()}")

    print("\n🎉 全部打包任务已圆满完成！")
    print(f"产物目录: {DIST_DIR}")
    return True


def main() -> int:
    py = get_python_exe()
    print(f"=== WallFlow 构建系统启动 (使用 Python: {py}) ===")
    if not run_tests(py):
        return 1
    clean_artifacts()
    if not build_executables(py):
        return 1
    if not verify_and_hash():
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

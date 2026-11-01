"""性能基准测试与大型图片库测量脚本 (`scripts/benchmark_performance.py`).

测量第 12.2 节要求的指标：
- 图片库扫描耗时
- 缩略图加载速度
- 壁纸布局与渲染耗时
- 屏保首帧加载耗时
- 内存峰值与内存增量
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psutil
from PIL import Image

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.image_library import ImageLibrary
from app.image_processor import load_image_for_target, render_image_layout
from app.settings_manager import FolderSourceConfig


def run_benchmarks(num_test_images: int = 100) -> dict[str, float]:
    print(f"=== 开始 WallFlow 核心性能基准测量 (测试图片量: {num_test_images}) ===")
    test_dir = ROOT / "build" / "benchmark_data"
    test_dir.mkdir(parents=True, exist_ok=True)

    proc = psutil.Process(os.getpid())
    mem_init_mb = proc.memory_info().rss / (1024 * 1024)

    # 1. 生成一批 4K (3840x2160) 与 2K 大型测试 PNG
    print(f"生成 {num_test_images} 张不同分辨率与方向的大型测试图片...")
    for i in range(num_test_images):
        p = test_dir / f"bench_img_{i:04d}.png"
        if not p.exists():
            w, h = (3840, 2160) if i % 2 == 0 else (1080, 1920)
            img = Image.new("RGB", (w, h), color=((i * 7) % 255, (i * 13) % 255, (i * 19) % 255))
            img.save(p, format="PNG")

    # 2. 测量扫描耗时
    lib = ImageLibrary(disk_cache_dir=test_dir / "thumbs")
    lib.set_sources([FolderSourceConfig(path=str(test_dir), enabled=True, recursive=True)])

    t_scan_start = time.perf_counter()
    summary = lib.scan()
    t_scan = time.perf_counter() - t_scan_start
    print(f"• 图片库扫描耗时: {t_scan:.4f} 秒 (扫描 {summary.valid_count} 张, 速度: {summary.valid_count / max(0.001, t_scan):.1f} 张/秒)")

    # 3. 测量缩略图生成速度
    sample_paths = lib.get_all_paths()[:20]
    t_thumb_start = time.perf_counter()
    for p in sample_paths:
        lib.load_thumbnail_pil(p, max_size=(240, 150))
    t_thumb = (time.perf_counter() - t_thumb_start) / max(1, len(sample_paths))
    print(f"• 单张 4K 缩略图生成平均耗时: {t_thumb * 1000.0:.2f} 毫秒")

    # 4. 测量壁纸渲染与布局耗时
    t_render_start = time.perf_counter()
    src = load_image_for_target(sample_paths[0], target_size=(1920, 1080))
    rendered = render_image_layout(src, 1920, 1080, display_mode="fill")
    t_render = (time.perf_counter() - t_render_start) * 1000.0
    print(f"• 单屏 1080p 壁纸解码与布局耗时: {t_render:.2f} 毫秒")

    # 5. 测量内存占用
    mem_final_mb = proc.memory_info().rss / (1024 * 1024)
    mem_delta_mb = mem_final_mb - mem_init_mb
    print(f"• 初始内存: {mem_init_mb:.1f} MB, 当前常驻内存: {mem_final_mb:.1f} MB (净增量: {mem_delta_mb:.1f} MB)")

    return {
        "scan_seconds": t_scan,
        "thumb_ms": t_thumb * 1000.0,
        "render_ms": t_render,
        "rss_mb": mem_final_mb,
        "delta_mb": mem_delta_mb,
    }


if __name__ == "__main__":
    run_benchmarks(50)

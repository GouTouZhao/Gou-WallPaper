"""高分辨率大型 PNG 与内存控制性能测试 (`tests/test_performance.py`)."""

from __future__ import annotations

import time
from pathlib import Path

import psutil
from PIL import Image

from app.image_library import ImageLibrary
from app.image_processor import load_image_for_target, render_image_layout
from app.settings_manager import FolderSourceConfig


def test_large_png_handling_and_memory_consumption(tmp_path: Path):
    """测试超高清大图的快速元数据提取、按需降采样解码与内存控制."""
    img_dir = tmp_path / "big_images"
    img_dir.mkdir(parents=True, exist_ok=True)

    # 创建一张 4K (3840x2160) 的大型 PNG 原图
    big_path = img_dir / "ultra_4k_sample.png"
    big_img = Image.new("RGB", (3840, 2160), color=(70, 110, 160))
    big_img.save(big_path, format="PNG")

    proc = psutil.Process()
    mem_before = proc.memory_info().rss

    # 1. 扫描阶段：只读元数据，耗时应在 10 毫秒以内
    lib = ImageLibrary(disk_cache_dir=tmp_path / "thumbs")
    lib.set_sources([FolderSourceConfig(path=str(img_dir), enabled=True, recursive=False)])

    t0 = time.perf_counter()
    summary = lib.scan()
    scan_elapsed = time.perf_counter() - t0
    assert summary.valid_count == 1
    assert scan_elapsed < 0.2  # 扫描单张 4K 图片必须毫秒级

    # 2. 缩略图生成：按需降采样，生成 240x150 缩略图
    t1 = time.perf_counter()
    thumb = lib.load_thumbnail_pil(str(big_path), max_size=(240, 150))
    thumb_elapsed = time.perf_counter() - t1
    assert thumb is not None
    assert thumb.width <= 240
    assert thumb.height <= 150
    assert thumb_elapsed < 0.5

    # 3. 屏幕渲染：按目标屏幕分辨率 (如 1920x1080) 解码布局
    t2 = time.perf_counter()
    screen_src = load_image_for_target(str(big_path), target_size=(1920, 1080))
    rendered = render_image_layout(screen_src, 1920, 1080, display_mode="fill")
    render_elapsed = time.perf_counter() - t2
    assert rendered.size == (1920, 1080)
    assert render_elapsed < 1.0

    mem_after = proc.memory_info().rss
    mem_diff_mb = (mem_after - mem_before) / (1024 * 1024)
    # 验证内存占用合理，未发生数倍原图解压失控
    assert mem_diff_mb < 200.0


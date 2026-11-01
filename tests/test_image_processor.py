"""图片布局、拼贴网格与色彩调整测试 (`tests/test_image_processor.py`)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from app.image_processor import (
    apply_adjustments,
    compute_collage_grid,
    render_collage,
    render_image_layout,
)
from app.settings_manager import ImageAdjustmentConfig


def test_render_image_layouts(tmp_path: Path):
    src = Image.new("RGB", (1200, 800), color=(100, 150, 200))

    # 1. Fill (1920x1080)
    img_fill = render_image_layout(src, 1920, 1080, display_mode="fill")
    assert img_fill.size == (1920, 1080)

    # 2. Fit (1920x1080)
    img_fit = render_image_layout(src, 1920, 1080, display_mode="fit", background_color="#000000")
    assert img_fit.size == (1920, 1080)

    # 3. Stretch
    img_stretch = render_image_layout(src, 800, 600, display_mode="stretch")
    assert img_stretch.size == (800, 600)

    # 4. Center
    img_center = render_image_layout(src, 1600, 1200, display_mode="center")
    assert img_center.size == (1600, 1200)

    # 5. Tile
    img_tile = render_image_layout(src, 2000, 1500, display_mode="tile")
    assert img_tile.size == (2000, 1500)


def test_compute_collage_grid():
    # 测试 2~8 拼贴矩形计算
    for count in range(2, 9):
        rects = compute_collage_grid(count, 1920, 1080, gap=4)
        assert len(rects) == count
        for x, y, w, h in rects:
            assert x >= 0 and y >= 0
            assert x + w <= 1920
            assert y + h <= 1080
            assert w > 0 and h > 0


def test_render_collage(tmp_path: Path):
    paths = []
    for i in range(4):
        p = tmp_path / f"collage_test_{i}.png"
        Image.new("RGB", (600, 400), color=(i * 40, 100, 150)).save(p)
        paths.append(str(p))

    collage_img = render_collage(paths, 1920, 1080, display_mode="fill")
    assert collage_img.size == (1920, 1080)


def test_non_destructive_image_adjustments(tmp_path: Path):
    src_file = tmp_path / "source_large.png"
    src_img = Image.new("RGB", (1000, 800), color=(128, 128, 128))
    src_img.save(src_file)

    orig_bytes = src_file.read_bytes()
    orig_hash = hashlib.sha256(orig_bytes).hexdigest()

    # 应用色彩调整
    adj = ImageAdjustmentConfig(brightness=20, contrast=-15, saturation=30, warmth=25)
    adjusted = apply_adjustments(src_img, adj)
    assert adjusted.size == (1000, 800)

    # 关键断言：源文件未被修改、覆盖或破坏
    assert src_file.read_bytes() == orig_bytes
    assert hashlib.sha256(src_file.read_bytes()).hexdigest() == orig_hash


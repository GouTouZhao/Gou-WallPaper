"""pytest 全局测试夹具与测试数据生成 (`tests/conftest.py`)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from PIL import Image

from app.clock import VirtualClock
from app.image_library import ImageLibrary
from app.settings_manager import FolderSourceConfig, SettingsManager


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def virtual_clock() -> VirtualClock:
    initial = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
    return VirtualClock(initial_utc=initial, initial_monotonic=1000.0)


@pytest.fixture
def sample_image_dir(tmp_path: Path) -> Path:
    """创建包含合法 PNG/JPG/WebP/BMP/TIFF、多层子目录、包含中文和空格的图片测试目录."""
    img_dir = tmp_path / "测试 图片 库"
    img_dir.mkdir(parents=True, exist_ok=True)
    sub_dir = img_dir / "子文件夹_Landscape"
    sub_dir.mkdir(parents=True, exist_ok=True)

    # 1. 横屏 PNG (1920x1080)
    img1 = Image.new("RGB", (1920, 1080), color=(30, 60, 90))
    img1.save(img_dir / "landscape_1.png", format="PNG")

    # 2. 竖屏 JPG (1080x1920)
    img2 = Image.new("RGB", (1080, 1920), color=(180, 50, 40))
    img2.save(img_dir / "portrait_1.jpg", format="JPEG")

    # 3. WebP
    img3 = Image.new("RGB", (1280, 720), color=(50, 160, 80))
    img3.save(img_dir / "photo_3.webp", format="WEBP")

    # 4. BMP
    img4 = Image.new("RGB", (800, 600), color=(100, 100, 200))
    img4.save(img_dir / "sample_4.bmp", format="BMP")

    # 5. TIFF
    img5 = Image.new("RGB", (640, 480), color=(220, 180, 50))
    img5.save(img_dir / "sample_5.tif", format="TIFF")

    # 6. 子文件夹中的大型 PNG
    img6 = Image.new("RGB", (3840, 2160), color=(40, 40, 60))
    img6.save(sub_dir / "4k_壁纸_sub.png", format="PNG")

    # 7. 损坏文件 (伪装成 PNG 的纯文本)
    bad_file = img_dir / "corrupted_fake.png"
    bad_file.write_text("THIS IS NOT A VALID PNG IMAGE FILE CONTENT", encoding="utf-8")

    # 8. 动态 GIF (应被静态图片库过滤跳过)
    gif_file = img_dir / "animated.gif"
    g1 = Image.new("RGB", (100, 100), (255, 0, 0))
    g2 = Image.new("RGB", (100, 100), (0, 255, 0))
    g1.save(gif_file, save_all=True, append_images=[g2], duration=100, loop=0)

    return img_dir


@pytest.fixture
def clean_settings(tmp_path: Path) -> SettingsManager:
    config_dir = tmp_path / "config_test"
    return SettingsManager(config_dir=config_dir)


@pytest.fixture
def populated_library(sample_image_dir: Path, tmp_path: Path) -> ImageLibrary:
    lib = ImageLibrary(
        thumbnail_cache_max_mb=32,
        disk_cache_dir=tmp_path / "cache" / "thumbs",
    )
    lib.set_sources([FolderSourceConfig(path=str(sample_image_dir), enabled=True, recursive=True)])
    lib.scan()
    return lib


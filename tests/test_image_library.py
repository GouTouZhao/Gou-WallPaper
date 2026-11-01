"""静态图片库扫描、过滤与缩略图缓存测试 (`tests/test_image_library.py`)."""

from __future__ import annotations

from pathlib import Path

from app.image_library import BoundedByteLRUCache, ImageLibrary
from app.settings_manager import FolderSourceConfig


def test_scan_finds_all_static_images_and_filters_gif_corrupted(populated_library: ImageLibrary, sample_image_dir: Path):
    images = populated_library.get_all_images()
    # 目录下有 6 张合法图片 (PNG, JPG, WebP, BMP, TIF, 加上子文件夹 4k PNG)
    assert len(images) == 6

    formats = {img.format_name for img in images}
    assert "PNG" in formats
    assert "JPEG" in formats
    assert "WEBP" in formats
    assert "BMP" in formats
    assert "TIFF" in formats

    # 验证动态 GIF 与损坏文件被跳过，并记录在 skipped_records 中
    skipped = populated_library.get_skipped_records()
    skipped_paths = [s.path for s in skipped]
    assert any("corrupted_fake.png" in p for p in skipped_paths)
    assert any("animated.gif" in p for p in skipped_paths)


def test_non_recursive_scanning(sample_image_dir: Path, tmp_path: Path):
    lib = ImageLibrary(disk_cache_dir=tmp_path / "thumbs")
    # 关闭递归扫描
    lib.set_sources([FolderSourceConfig(path=str(sample_image_dir), enabled=True, recursive=False)])
    summary = lib.scan()

    # 子文件夹中的 4k_壁纸_sub.png 应被排除，只扫描根目录下 5 张合法图片
    assert summary.valid_count == 5
    assert not any("子文件夹" in img.path for img in lib.get_all_images())


def test_disabled_folder_source(sample_image_dir: Path, tmp_path: Path):
    lib = ImageLibrary(disk_cache_dir=tmp_path / "thumbs")
    lib.set_sources([FolderSourceConfig(path=str(sample_image_dir), enabled=False, recursive=True)])
    summary = lib.scan()
    assert summary.valid_count == 0
    assert len(lib.get_all_images()) == 0


def test_chinese_and_space_paths(populated_library: ImageLibrary):
    # 路径中包含“测试 图片 库”和“子文件夹_Landscape”
    paths = populated_library.get_all_paths()
    assert any("测试 图片 库" in p for p in paths)
    assert any("4k_壁纸_sub.png" in p for p in paths)


def test_thumbnail_generation_and_lru_cache(populated_library: ImageLibrary):
    paths = populated_library.get_all_paths()
    first_path = paths[0]

    thumb1 = populated_library.load_thumbnail_pil(first_path, max_size=(200, 150))
    assert thumb1 is not None
    assert thumb1.width <= 200
    assert thumb1.height <= 150

    # 第二次获取命中内存 LRU 缓存
    thumb2 = populated_library.load_thumbnail_pil(first_path, max_size=(200, 150))
    assert thumb2 is not None
    assert thumb2.size == thumb1.size

    # 清理缓存
    cleaned = populated_library.clear_cache()
    assert populated_library.thumbnail_cache.current_bytes == 0


def test_bounded_byte_lru_cache_eviction():
    # 上限 1000 字节，每项 300 字节
    cache = BoundedByteLRUCache(max_bytes=1000, max_items=10)
    cache.put("item1", "A" * 300, 300)
    cache.put("item2", "B" * 300, 300)
    cache.put("item3", "C" * 300, 300)
    assert cache.item_count == 3
    assert cache.current_bytes == 900

    # 放入第 4 个，总字节达 1200 > 1000，淘汰最早未使用的 item1
    cache.put("item4", "D" * 300, 300)
    assert cache.get("item1") is None
    assert cache.get("item2") is not None
    assert cache.get("item3") is not None
    assert cache.get("item4") is not None
    assert cache.current_bytes <= 1000


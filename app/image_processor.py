"""图片解码、比例布局、多图拼贴与色彩调整模块 (`app/image_processor.py`).

核心设计：
- 针对 4K/8K 大型 PNG 进行按需降采样解码，绝不修改原始图片文件
- 支持 6 种显示布局：Fill（填充裁剪）、Fit（适应留边）、Stretch（拉伸）、Center（居中）、Tile（平铺）、Span（跨屏）
- 支持 2~8 张图片高美感网格拼贴模式 (`render_collage`)
- 支持亮度、对比度、饱和度、色温（冷暖）非破坏性调整 (`apply_adjustments`)
"""

from __future__ import annotations

import math
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageColor, ImageEnhance, ImageOps

Image.MAX_IMAGE_PIXELS = None

from app.logger import get_logger
from app.settings_manager import ImageAdjustmentConfig

logger = get_logger("image_processor")

_IMAGE_CACHE_LOCK = threading.Lock()
_IMAGE_CACHE: OrderedDict[tuple[str, tuple[int, int], float], Image.Image] = OrderedDict()
_MAX_IMAGE_CACHE_ENTRIES = 2


def clear_image_cache() -> None:
    """彻底清空图片全尺寸解码缓存，释放已占用的内存."""
    with _IMAGE_CACHE_LOCK:
        for img in list(_IMAGE_CACHE.values()):
            try:
                img.close()
            except Exception:
                pass
        _IMAGE_CACHE.clear()


def parse_rgb_color(color_str: str, default: tuple[int, int, int] = (0, 0, 0)) -> tuple[int, int, int]:
    """解析十六进制或颜色名称为 RGB 元组."""
    try:
        rgb = ImageColor.getrgb(color_str or "#000000")
        return (int(rgb[0]), int(rgb[1]), int(rgb[2]))
    except Exception:
        return default


def load_image_for_target(
    path: str | Path,
    target_size: tuple[int, int] | None = None,
) -> Image.Image:
    """安全加载静态图片并自动应用 EXIF 方向校正 (带内存 LRU 缓存加速).

    若提供了 `target_size`，对超大图利用 `draft` 提示加速解码，并控制内存峰值。
    """
    cache_key = None
    if target_size is not None:
        try:
            resolved_p = Path(path).resolve()
            mtime = os.path.getmtime(resolved_p)
            cache_key = (str(resolved_p), (int(target_size[0]), int(target_size[1])), float(mtime))
            with _IMAGE_CACHE_LOCK:
                if cache_key in _IMAGE_CACHE:
                    cached_img = _IMAGE_CACHE[cache_key]
                    _IMAGE_CACHE.move_to_end(cache_key)
                    return cached_img.copy()
        except OSError:
            cache_key = None

    with Image.open(path) as raw_img:
        fmt = (raw_img.format or "").upper()
        if target_size is not None and fmt in ("JPEG", "MPO"):
            tw, th = max(1, target_size[0]), max(1, target_size[1])
            try:
                raw_img.draft("RGB", (tw * 2, th * 2))
            except Exception:
                pass

        if fmt in ("JPEG", "MPO", "TIFF"):
            try:
                transposed = ImageOps.exif_transpose(raw_img)
            except Exception:
                transposed = raw_img
        else:
            transposed = raw_img

        rgb_img = transposed.convert("RGB")
        # 若原图远大于目标显示尺寸的 2 倍，先做一次快速降采样再交给后续高精度布局，极大降低内存占用并加速渲染
        if target_size is not None:
            tw, th = max(1, target_size[0]), max(1, target_size[1])
            if rgb_img.width > tw * 2.2 and rgb_img.height > th * 2.2:
                rgb_img.thumbnail((int(tw * 2.0), int(th * 2.0)), Image.Resampling.BILINEAR)

        if cache_key is not None:
            with _IMAGE_CACHE_LOCK:
                _IMAGE_CACHE[cache_key] = rgb_img.copy()
                while len(_IMAGE_CACHE) > _MAX_IMAGE_CACHE_ENTRIES:
                    _, old_img = _IMAGE_CACHE.popitem(last=False)
                    try:
                        old_img.close()
                    except Exception:
                        pass

        return rgb_img


def render_image_layout(
    source: Image.Image,
    target_width: int,
    target_height: int,
    display_mode: str = "fill",
    background_color: str = "#000000",
) -> Image.Image:
    """将单张图片按照指定布局模式渲染到 `(target_width, target_height)` 画布.

    支持：`fill` | `fit` | `stretch` | `center` | `tile` | `span`
    """
    target_width = max(1, int(target_width))
    target_height = max(1, int(target_height))
    bg_rgb = parse_rgb_color(background_color)
    mode = (display_mode or "fill").lower()

    src_w, src_h = source.size
    if src_w <= 0 or src_h <= 0:
        return Image.new("RGB", (target_width, target_height), bg_rgb)

    if mode in ("fill", "span"):
        # 等比缩放至完全覆盖目标区域并居中裁剪，不拉伸变形
        scale = max(target_width / src_w, target_height / src_h)
        scaled_w = max(target_width, int(round(src_w * scale)))
        scaled_h = max(target_height, int(round(src_h * scale)))
        resized = source.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        left = max(0, (scaled_w - target_width) // 2)
        top = max(0, (scaled_h - target_height) // 2)
        return resized.crop((left, top, left + target_width, top + target_height))

    if mode == "fit":
        # 等比缩放至完整放入目标区域，四周填充背景色（默认纯黑）
        scale = min(target_width / src_w, target_height / src_h)
        scaled_w = max(1, int(round(src_w * scale)))
        scaled_h = max(1, int(round(src_h * scale)))
        resized = source.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (target_width, target_height), bg_rgb)
        offset_x = (target_width - scaled_w) // 2
        offset_y = (target_height - scaled_h) // 2
        canvas.paste(resized, (offset_x, offset_y))
        return canvas

    if mode == "stretch":
        # 强制拉伸填满画布
        return source.resize((target_width, target_height), Image.Resampling.LANCZOS)

    if mode == "center":
        # 保持原始像素大小居中显示，超出部分裁剪，不足部分填充背景色
        canvas = Image.new("RGB", (target_width, target_height), bg_rgb)
        crop_left = max(0, (src_w - target_width) // 2)
        crop_top = max(0, (src_h - target_height) // 2)
        crop_w = min(src_w, target_width)
        crop_h = min(src_h, target_height)
        cropped = source.crop((crop_left, crop_top, crop_left + crop_w, crop_top + crop_h))
        paste_x = max(0, (target_width - crop_w) // 2)
        paste_y = max(0, (target_height - crop_h) // 2)
        canvas.paste(cropped, (paste_x, paste_y))
        return canvas

    if mode == "tile":
        # 平铺填充整个画布
        canvas = Image.new("RGB", (target_width, target_height), bg_rgb)
        for y in range(0, target_height, src_h):
            for x in range(0, target_width, src_w):
                canvas.paste(source, (x, y))
        return canvas

    # 默认回退 fill
    return render_image_layout(source, target_width, target_height, "fill", background_color)


def compute_collage_grid(
    count: int,
    width: int,
    height: int,
    gap: int = 4,
) -> list[tuple[int, int, int, int]]:
    """计算 2~8 张图片的拼贴网格矩形列表 `[(x, y, w, h), ...]`.

    自适应网格规划算法：自动分配最优行列，末行不足时自动加宽居中。
    """
    n = max(1, min(8, int(count)))
    width = max(1, int(width))
    height = max(1, int(height))
    gap = max(0, int(gap))

    col_map = {1: 1, 2: 2, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4}
    cols = col_map.get(n, int(math.ceil(math.sqrt(n))))
    rows = int(math.ceil(n / cols))

    rects: list[tuple[int, int, int, int]] = []
    total_v_gaps = gap * (rows - 1)
    base_row_h = max(1, (height - total_v_gaps) // rows)

    idx = 0
    for r in range(rows):
        remaining = n - idx
        row_cols = min(cols, remaining)
        y = r * (base_row_h + gap)
        row_h = height - y if r == rows - 1 else base_row_h

        total_h_gaps = gap * (row_cols - 1)
        cell_w = max(1, (width - total_h_gaps) // row_cols)

        for c in range(row_cols):
            x = c * (cell_w + gap)
            w = width - x if c == row_cols - 1 else cell_w
            rects.append((x, y, max(1, w), max(1, row_h)))
            idx += 1

    return rects


def render_collage(
    image_paths: Sequence[str | Path],
    target_width: int,
    target_height: int,
    display_mode: str = "fill",
    background_color: str = "#000000",
    gap: int = 4,
) -> Image.Image:
    """将 2~8 张图片拼贴渲染到单台显示器的画布上."""
    bg_rgb = parse_rgb_color(background_color)
    canvas = Image.new("RGB", (max(1, target_width), max(1, target_height)), bg_rgb)
    if not image_paths:
        return canvas

    rects = compute_collage_grid(len(image_paths), target_width, target_height, gap=gap)
    cell_mode = "fit" if display_mode == "fit" else "fill"

    for path, (x, y, w, h) in zip(image_paths, rects):
        try:
            src = load_image_for_target(path, target_size=(w, h))
            tile = render_image_layout(src, w, h, display_mode=cell_mode, background_color=background_color)
            canvas.paste(tile, (x, y))
        except Exception as exc:
            logger.warning("拼贴子图加载失败 [%s]: %s", path, exc)

    return canvas


def apply_adjustments(image: Image.Image, adjustments: ImageAdjustmentConfig) -> Image.Image:
    """对图像应用亮度、对比度、饱和度与色温调整（非破坏性，返回新图像）."""
    if adjustments.is_identity():
        return image

    result = image.convert("RGB")

    # 1. 亮度 (-100..+100 -> 0.2..1.8)
    if adjustments.brightness != 0:
        factor = max(0.1, 1.0 + (adjustments.brightness / 100.0) * 0.8)
        result = ImageEnhance.Brightness(result).enhance(factor)

    # 2. 对比度 (-100..+100 -> 0.2..1.8)
    if adjustments.contrast != 0:
        factor = max(0.1, 1.0 + (adjustments.contrast / 100.0) * 0.8)
        result = ImageEnhance.Contrast(result).enhance(factor)

    # 3. 饱和度 (-100..+100 -> 0.0..2.0)
    if adjustments.saturation != 0:
        factor = max(0.0, 1.0 + (adjustments.saturation / 100.0))
        result = ImageEnhance.Color(result).enhance(factor)

    # 4. 色温 Warmth (-100 冷色调 .. +100 暖色调)
    if adjustments.warmth != 0:
        delta = (adjustments.warmth / 100.0) * 0.15
        r_gain = 1.0 + delta
        b_gain = 1.0 - delta
        r_lut = [max(0, min(255, int(round(i * r_gain)))) for i in range(256)]
        g_lut = list(range(256))
        b_lut = [max(0, min(255, int(round(i * b_gain)))) for i in range(256)]
        result = result.point(r_lut + g_lut + b_lut)

    return result


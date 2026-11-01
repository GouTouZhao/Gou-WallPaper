"""应用图标生成与加载辅助模块 (`app/icon_helper.py`).

自动生成 `assets/icon.ico` 及内存 `QIcon`，确保系统托盘、主窗口与 PyInstaller 打包始终拥有清晰图标。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from PySide6.QtGui import QIcon, QImage, QPixmap


def _get_project_assets_dir() -> Path:
    """获取项目 assets 静态资源目录."""
    return Path(__file__).resolve().parent.parent / "assets"


def create_wallflow_pil_icon(size: int = 256) -> Image.Image:
    """加载或绘制现代风格的 WallFlow 应用图标."""
    assets_dir = _get_project_assets_dir()
    for candidate_name in ("icon.png", "app_icon_original.png", "icon.ico"):
        p = assets_dir / candidate_name
        if p.exists() and p.stat().st_size > 100:
            try:
                img = Image.open(p).convert("RGBA")
                if img.size != (size, size):
                    img = img.resize((size, size), Image.Resampling.LANCZOS)
                return img
            except Exception:
                pass

    # 兜底：若未找到图片文件，程序化生成简约图标
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = max(4, size // 16)
    rect = [margin, margin, size - margin, size - margin]
    radius = max(12, size // 5)
    draw.rounded_rectangle(rect, radius=radius, fill=(24, 90, 188, 255), outline=(96, 165, 250, 255), width=max(2, size // 48))

    # 太阳
    sun_r = size // 9
    sun_cx = int(size * 0.68)
    sun_cy = int(size * 0.34)
    draw.ellipse(
        [sun_cx - sun_r, sun_cy - sun_r, sun_cx + sun_r, sun_cy + sun_r],
        fill=(251, 191, 36, 255),
    )

    # 远山与近山
    p1 = [
        (int(size * 0.16), int(size * 0.78)),
        (int(size * 0.42), int(size * 0.40)),
        (int(size * 0.70), int(size * 0.78)),
    ]
    draw.polygon(p1, fill=(56, 189, 248, 235))

    p2 = [
        (int(size * 0.36), int(size * 0.78)),
        (int(size * 0.62), int(size * 0.48)),
        (int(size * 0.86), int(size * 0.78)),
    ]
    draw.polygon(p2, fill=(14, 165, 233, 255))

    return img


def ensure_ico_file(target_path: str | Path) -> Path:
    """确保磁盘上的 `.ico` 图标文件存在；若不存在则自动生成多尺寸 `.ico`."""
    p = Path(target_path)
    if p.exists() and p.stat().st_size > 100:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    base = create_wallflow_pil_icon(256)
    base.save(p, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return p


def ensure_ui_icon_assets(assets_dir: str | Path | None = None) -> Path:
    """确保界面所需的全部简约黑白灰高对比度 PNG 图标文件存在；若不存在或已更新则自动生成."""
    if assets_dir is None:
        p_dir = Path(__file__).resolve().parent.parent / "assets"
    else:
        p_dir = Path(assets_dir)
    p_dir.mkdir(parents=True, exist_ok=True)

    # 1. Checkbox Unchecked (简约深灰边框与黑底)
    cb_u = p_dir / "cb_unchecked.png"
    im = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([1, 1, 18, 18], radius=4, fill="#141414", outline="#52525b", width=2)
    im.save(cb_u)

    # 2. Checkbox Checked (白底黑勾，纯黑白简约高对比度)
    cb_c = p_dir / "cb_checked.png"
    im = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([1, 1, 18, 18], radius=4, fill="#ffffff", outline="#ffffff", width=2)
    d.line([(5, 10), (8, 14), (14, 5)], fill="#000000", width=2)
    im.save(cb_c)

    # 3. Checkbox Disabled (低调暗灰)
    cb_d = p_dir / "cb_disabled.png"
    im = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([1, 1, 18, 18], radius=4, fill="#0d0d0d", outline="#27272a", width=2)
    im.save(cb_d)

    # 4. Radio Unchecked (深灰外圆)
    r_u = p_dir / "radio_unchecked.png"
    im = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([1, 1, 18, 18], fill="#141414", outline="#52525b", width=2)
    im.save(r_u)

    # 5. Radio Checked (白框白芯)
    r_c = p_dir / "radio_checked.png"
    im = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([1, 1, 18, 18], fill="#141414", outline="#ffffff", width=2)
    d.ellipse([5, 5, 14, 14], fill="#ffffff")
    im.save(r_c)

    # 6. Radio Disabled
    r_d = p_dir / "radio_disabled.png"
    im = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([1, 1, 18, 18], fill="#0d0d0d", outline="#27272a", width=2)
    im.save(r_d)

    # 7. Arrow Down (简约灰白箭头)
    arr = p_dir / "arrow_down.png"
    im = Image.new("RGBA", (14, 14), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.polygon([(2, 5), (12, 5), (7, 10)], fill="#d4d4d8")
    im.save(arr)

    # 8. Arrow Down Disabled
    arr_d = p_dir / "arrow_down_disabled.png"
    im = Image.new("RGBA", (14, 14), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.polygon([(2, 5), (12, 5), (7, 10)], fill="#52525b")
    im.save(arr_d)

    return p_dir


def get_app_qicon(ico_path: str | Path | None = None) -> QIcon:
    """获取 Qt `QIcon` 实例."""
    if ico_path is not None and Path(ico_path).exists():
        icon = QIcon(str(ico_path))
        if not icon.isNull():
            return icon

    # 优先检测项目 assets/ 目录下的原生高清图标文件
    assets_dir = _get_project_assets_dir()
    for candidate in (assets_dir / "icon.ico", assets_dir / "icon.png"):
        if candidate.exists() and candidate.stat().st_size > 100:
            icon = QIcon(str(candidate))
            if not icon.isNull():
                return icon

    pil_img = create_wallflow_pil_icon(64)
    data = pil_img.tobytes("raw", "RGBA")
    qimg = QImage(data, pil_img.width, pil_img.height, pil_img.width * 4, QImage.Format.Format_RGBA8888).copy()
    return QIcon(QPixmap.fromImage(qimg))


"""WallFlow 现代化物理拟真显示器预览舞台 (`app/ui/preview_stage.py`).

1. 拟真显示器画框 (Monitor Bezels & Stand) 与多显示器真实物理拓扑排布；
2. 多显示器聚焦缩放 (Focus Chips: 全部屏幕 / 独立单屏)；
3. 交互式工具栏：
   - 随机重选 (Shuffle)
   - 保存拼贴为图片 (Save as Image)
   - 全屏沉浸式放大 (Expand / Lightbox)
   - 立即应用此预览 (Apply This)
4. 当前使用的图片胶囊列表 (Used Images List)，支持在资源管理器中定位及单图更换。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.image_library import ImageLibrary
from app.image_processor import apply_adjustments, parse_rgb_color, render_collage, render_image_layout
from app.logger import get_logger
from app.wallpaper_manager import MonitorInfo, WallpaperManager, compute_virtual_desktop_bounds

logger = get_logger("ui.preview_stage")


class FullscreenLightbox(QDialog):
    """全屏/沉浸式大图预览弹窗."""

    def __init__(self, pixmap: QPixmap, title_text: str = "壁纸全屏预览", parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet("background-color: #050811;")
        self.resize(1100, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(12)

        # 顶部栏
        header = QHBoxLayout()
        lbl_title = QLabel(f"{title_text}")
        lbl_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        lbl_hint = QLabel("按 ESC 键或点击右上角按钮退出全屏")
        lbl_hint.setStyleSheet("font-size: 12px; color: #888888;")

        btn_close = QPushButton("关闭")
        btn_close.setObjectName("SecondaryBtn")
        btn_close.setFixedWidth(80)
        btn_close.clicked.connect(self.close)

        header.addWidget(lbl_title)
        header.addWidget(lbl_hint)
        header.addStretch()
        header.addWidget(btn_close)
        layout.addLayout(header)

        # 居中大图
        self.lbl_image = QLabel()
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_image.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pixmap = pixmap
        layout.addWidget(self.lbl_image, 1)

        self._update_display()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_display()

    def _update_display(self) -> None:
        if self._pixmap.isNull():
            return
        avail_w = max(100, self.lbl_image.width())
        avail_h = max(100, self.lbl_image.height())
        scaled = self._pixmap.scaled(
            avail_w,
            avail_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.lbl_image.setPixmap(scaled)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)


class MonitorMockupCanvas(QWidget):
    """绘制真实显示器外框、微光边框与屏幕内容的拟真画布."""

    monitorClicked = Signal(int)  # 点击某个显示器时发出索引号

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

        self._preview_pixmap: QPixmap | None = None
        self._monitors: list[MonitorInfo] = []
        self._focus_index: int | None = None  # None 为全屏拓扑，int 为聚焦单个屏幕
        self._hover_monitor_idx: int | None = None

        self._last_monitor_rects: dict[int, QRectF] = {}

    def set_content(
        self,
        pixmap: QPixmap | None,
        monitors: list[MonitorInfo],
        focus_index: int | None = None,
    ) -> None:
        self._preview_pixmap = pixmap
        self._monitors = monitors
        self._focus_index = focus_index
        self.update()

    def set_focus(self, focus_index: int | None) -> None:
        self._focus_index = focus_index
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        width = self.width()
        height = self.height()

        # 1. 绘制深色微渐变背景
        bg_grad = QLinearGradient(0, 0, 0, height)
        bg_grad.setColorAt(0.0, QColor("#090e18"))
        bg_grad.setColorAt(1.0, QColor("#0e1524"))
        painter.fillRect(0, 0, width, height, bg_grad)

        # 若无显示器数据，回退到默认 16:9
        monitors = self._monitors or [
            MonitorInfo(
                device_name="Primary",
                x=0,
                y=0,
                width=1920,
                height=1080,
                is_primary=True,
            )
        ]

        # 计算绘制区域比例
        if self._focus_index is not None and 0 <= self._focus_index < len(monitors):
            target_monitors = [monitors[self._focus_index]]
            min_x, min_y, vw, vh = target_monitors[0].x, target_monitors[0].y, target_monitors[0].width, target_monitors[0].height
        else:
            target_monitors = monitors
            min_x, min_y, vw, vh = compute_virtual_desktop_bounds(monitors)

        vw = max(1, vw)
        vh = max(1, vh)

        # 留边 (margin)
        margin_x = 28.0
        margin_y = 24.0
        avail_w = max(50.0, width - margin_x * 2)
        avail_h = max(50.0, height - margin_y * 2 - 16.0)  # 留出底座空间

        scale = min(avail_w / vw, avail_h / vh)
        stage_w = vw * scale
        stage_h = vh * scale
        start_x = (width - stage_w) / 2.0
        start_y = (height - stage_h - 16.0) / 2.0

        self._last_monitor_rects.clear()

        # 2. 绘制每个显示器（物理边框 + 内容 + 装饰徽标）
        for idx, mon in enumerate(target_monitors):
            orig_idx = monitors.index(mon) if mon in monitors else idx
            mon_x = start_x + (mon.x - min_x) * scale
            mon_y = start_y + (mon.y - min_y) * scale
            mon_w = mon.width * scale
            mon_h = mon.height * scale
            screen_rect = QRectF(mon_x, mon_y, mon_w, mon_h)
            self._last_monitor_rects[orig_idx] = screen_rect

            # (1) 绘制显示器支架底座微光
            stand_w = min(mon_w * 0.35, 90.0)
            stand_h = 10.0
            stand_x = mon_x + (mon_w - stand_w) / 2.0
            stand_y = mon_y + mon_h + 3.0
            stand_path = QPainterPath()
            stand_path.addRoundedRect(QRectF(stand_x, stand_y, stand_w, stand_h), 4, 4)
            painter.fillPath(stand_path, QColor(25, 34, 49, 180))

            # (2) 绘制显示器外边框 (Bezel)
            bezel_pad = 7.0
            bezel_rect = QRectF(
                mon_x - bezel_pad,
                mon_y - bezel_pad,
                mon_w + bezel_pad * 2,
                mon_h + bezel_pad * 2 + 5.0,  # 下边框略厚
            )
            is_hover = (self._hover_monitor_idx == orig_idx)

            bezel_path = QPainterPath()
            bezel_path.addRoundedRect(bezel_rect, 10, 10)

            # 外框底色与微金属质感 - 极简黑白灰
            painter.fillPath(bezel_path, QColor("#121212"))
            bezel_border_color = QColor("#ffffff" if is_hover else "#27272a")
            painter.strokePath(bezel_path, QPen(bezel_border_color, 1.5))

            # (3) 屏幕内容剪裁与绘制
            painter.save()
            screen_clip = QPainterPath()
            screen_clip.addRoundedRect(screen_rect, 5, 5)
            painter.setClipPath(screen_clip)

            if self._preview_pixmap and not self._preview_pixmap.isNull():
                # 绘制对应图像区域
                if self._focus_index is not None:
                    # 单屏模式直接绘制
                    painter.drawPixmap(
                        screen_rect.toRect(),
                        self._preview_pixmap,
                        self._preview_pixmap.rect(),
                    )
                else:
                    # 全景多屏：从虚拟桌面总图中按比例裁切
                    src_x = int(((mon.x - min_x) / vw) * self._preview_pixmap.width())
                    src_y = int(((mon.y - min_y) / vh) * self._preview_pixmap.height())
                    src_w = int((mon.width / vw) * self._preview_pixmap.width())
                    src_h = int((mon.height / vh) * self._preview_pixmap.height())
                    painter.drawPixmap(
                        screen_rect.toRect(),
                        self._preview_pixmap,
                        QRect(src_x, src_y, src_w, src_h),
                    )
            else:
                # 占位图
                painter.fillRect(screen_rect, QColor("#0a0a0a"))
                painter.setPen(QColor("#71717a"))
                painter.setFont(QFont("Segoe UI", 11))
                painter.drawText(screen_rect, Qt.AlignmentFlag.AlignCenter, "正在渲染高清预览...")

            painter.restore()

            # (4) 绘制屏幕内微阴影/内沿倒角
            painter.strokePath(screen_clip, QPen(QColor(0, 0, 0, 140), 1.0))

            # (5) 绘制左上角显示器半透明磨砂徽标 (#1 主屏 1920x1080)
            badge_text = f"#{orig_idx + 1} {'主屏 ' if mon.is_primary else ''}{mon.width}×{mon.height}"
            badge_font = QFont("Segoe UI", 9, QFont.Weight.Bold)
            painter.setFont(badge_font)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(badge_text) + 14
            th = 20

            badge_rect = QRectF(mon_x + 8, mon_y + 8, tw, th)
            badge_path = QPainterPath()
            badge_path.addRoundedRect(badge_rect, 4, 4)
            painter.fillPath(badge_path, QColor(0, 0, 0, 220))
            painter.strokePath(badge_path, QPen(QColor("#ffffff" if is_hover else "#3f3f46"), 1.0))

            painter.setPen(QColor("#ffffff" if is_hover else "#e4e4e7"))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

            # (6) 绘制下边框中央微小电源指示灯
            led_x = mon_x + mon_w / 2.0
            led_y = bezel_rect.bottom() - 3.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffffff" if mon.is_primary else "#52525b"))
            painter.drawEllipse(QPoint(int(led_x), int(led_y)), 2, 2)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        found_idx = None
        for idx, rect in self._last_monitor_rects.items():
            if rect.contains(pos):
                found_idx = idx
                break
        if found_idx != self._hover_monitor_idx:
            self._hover_monitor_idx = found_idx
            self.setCursor(Qt.CursorShape.PointingHandCursor if found_idx is not None else Qt.CursorShape.ArrowCursor)
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            for idx, rect in self._last_monitor_rects.items():
                if rect.contains(pos):
                    self.monitorClicked.emit(idx)
                    break


class PreviewStageWidget(QFrame):
    """综合壁纸效果预览舞台 (集成拟真画框、工具栏、多屏切换、图片胶囊)."""

    applyRequested = Signal(list)  # 请求将当前展示的图片集合应用为壁纸
    refreshRequested = Signal()    # 请求重新随机生成预览
    imagePickRequested = Signal(int, str)  # 请求更换第 N 张图片 (index, current_path)

    def __init__(
        self,
        wallpaper_manager: WallpaperManager,
        library: ImageLibrary,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._wp_mgr = wallpaper_manager
        self._library = library

        self._current_paths: list[str] = []
        self._rendered_pil: Image.Image | None = None
        self._rendered_pixmap: QPixmap | None = None
        self._focus_monitor: int | None = None
        self._last_render_cache_key: tuple | None = None

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        # 1. 顶部标题与操作工具栏
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        # 左侧标题与分辨率说明
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        lbl_title = QLabel("画面预览与多屏视窗")
        lbl_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        self.lbl_subtitle = QLabel("正在初始化多显示器拓扑...")
        self.lbl_subtitle.setStyleSheet("font-size: 11px; color: #888888;")
        title_box.addWidget(lbl_title)
        title_box.addWidget(self.lbl_subtitle)
        top_bar.addLayout(title_box)

        top_bar.addStretch()

        # 右侧快捷动作按钮 (简约纯文本规范)
        self.btn_shuffle = QPushButton("随机换图")
        self.btn_shuffle.setObjectName("SecondaryBtn")
        self.btn_shuffle.setToolTip("在当前图片库中随机抽取新壁纸并预览 (不会立即改变桌面)")
        self.btn_shuffle.clicked.connect(self._on_shuffle_clicked)

        self.btn_save = QPushButton("导出图片")
        self.btn_save.setObjectName("SecondaryBtn")
        self.btn_save.setToolTip("将当前合成的高清壁纸直接保存为本地图片文件")
        self.btn_save.clicked.connect(self._on_save_clicked)

        self.btn_expand = QPushButton("全屏查看")
        self.btn_expand.setObjectName("SecondaryBtn")
        self.btn_expand.setToolTip("全屏沉浸式无干扰查看壁纸画质 (ESC 键退出)")
        self.btn_expand.clicked.connect(self._on_expand_clicked)

        self.btn_apply = QPushButton("应用到桌面")
        self.btn_apply.setToolTip("立即将当前舞台中预览的图片应用为 Windows 桌面壁纸")
        self.btn_apply.clicked.connect(self._on_apply_clicked)

        top_bar.addWidget(self.btn_shuffle)
        top_bar.addWidget(self.btn_save)
        top_bar.addWidget(self.btn_expand)
        top_bar.addWidget(self.btn_apply)
        layout.addLayout(top_bar)

        # 2. 拟真物理显示器外框画布
        self.mockup_canvas = MonitorMockupCanvas(self)
        self.mockup_canvas.monitorClicked.connect(self._on_canvas_monitor_clicked)
        layout.addWidget(self.mockup_canvas, 1)

        # 3. 多显示器聚焦 Chips (单屏/多屏切换)
        self.chip_bar = QHBoxLayout()
        self.chip_bar.setSpacing(6)
        self.lbl_chip_prompt = QLabel("显示器聚焦：")
        self.lbl_chip_prompt.setStyleSheet("font-size: 12px; color: #94a3b8;")
        self.chip_bar.addWidget(self.lbl_chip_prompt)
        layout.addLayout(self.chip_bar)

        # 4. 当前预览使用的图片卡片胶囊栏 (Used Images Pills)
        used_box = QVBoxLayout()
        used_box.setSpacing(6)
        self.lbl_used_title = QLabel("当前合成使用的图片素材 (点击可在资源管理器中定位)：")
        self.lbl_used_title.setStyleSheet("font-size: 12px; color: #94a3b8; font-weight: 500;")
        used_box.addWidget(self.lbl_used_title)

        self.scroll_used = QScrollArea()
        self.scroll_used.setFixedHeight(68)
        self.scroll_used.setWidgetResizable(True)
        self.scroll_used.setStyleSheet("background: transparent; border: none;")

        self.widget_used_content = QWidget()
        self.layout_used_items = QHBoxLayout(self.widget_used_content)
        self.layout_used_items.setContentsMargins(0, 0, 0, 0)
        self.layout_used_items.setSpacing(8)
        self.layout_used_items.addStretch()
        self.scroll_used.setWidget(self.widget_used_content)
        used_box.addWidget(self.scroll_used)

        layout.addLayout(used_box)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_pending_refresh", False) or self._rendered_pixmap is None:
            self._pending_refresh = False
            self.refresh_preview(force=True)

    def release_preview_memory(self) -> None:
        """释放高清预览图占用的 PIL Image 与 QPixmap 内存，进入轻量休眠状态."""
        if self._rendered_pil is not None:
            try:
                self._rendered_pil.close()
            except Exception:
                pass
            self._rendered_pil = None
        self._rendered_pixmap = None
        self._last_render_cache_key = None
        self._pending_refresh = True
        if hasattr(self, "mockup_canvas"):
            self.mockup_canvas.set_content(None, self.mockup_canvas._monitors, None)

    def refresh_preview(self, override_paths: list[str] | None = None, force: bool = False) -> None:
        """根据当前壁纸设置与指定图片渲染最新预览."""
        # 性能与内存终极优化：若当前处于托盘后台或非当前标签页隐藏状态，标记待刷新并跳过耗时耗内存的渲染
        if not force and not self.isVisible():
            self._pending_refresh = True
            if override_paths:
                self._current_paths = list(override_paths)
            return

        self._pending_refresh = False
        try:
            mon_data = self._wp_mgr.monitors
            monitors = mon_data() if callable(mon_data) else mon_data
            _, _, vw, vh = compute_virtual_desktop_bounds(monitors)
            self.lbl_subtitle.setText(
                f"{len(monitors)} 台显示器 · 虚拟桌面拓扑 {vw}×{vh} · 双击显示器可放大聚焦"
            )

            # 更新多屏聚焦 Chips
            self._update_focus_chips(monitors)

            # 决定用于渲染的图片列表
            wp_cfg = self._wp_mgr._settings.config.wallpaper
            target_count = max(2, min(8, wp_cfg.collage_count)) if wp_cfg.collage_enabled else (len(monitors) if wp_cfg.multi_monitor_mode == "different" else 1)

            if override_paths:
                self._current_paths = override_paths[:target_count]
            elif not self._current_paths:
                self._current_paths = self._wp_mgr._settings.config.wallpaper.current_wallpaper_paths
                if not self._current_paths:
                    cur = self._wp_mgr._selector.current()
                    if cur:
                        self._current_paths = [cur]
                if not self._current_paths:
                    all_p = self._library.get_all_paths()
                    if all_p:
                        self._current_paths = all_p[:target_count]

            if not self._current_paths:
                self.mockup_canvas.set_content(None, monitors, self._focus_monitor)
                self._update_used_pills([])
                return

            adj = wp_cfg.adjustments
            current_cache_key = (
                tuple(self._current_paths),
                str(wp_cfg.display_mode),
                str(wp_cfg.background_color),
                bool(wp_cfg.collage_enabled),
                int(wp_cfg.collage_count),
                int(adj.brightness),
                int(adj.contrast),
                int(adj.saturation),
                int(adj.warmth),
                len(monitors),
                vw,
                vh,
            )

            # 若渲染缓存匹配且 Pixmap 有效，跳过重复解码合成，瞬间完成切换！
            if (
                not override_paths
                and self._rendered_pixmap is not None
                and not self._rendered_pixmap.isNull()
                and self._last_render_cache_key == current_cache_key
            ):
                self.mockup_canvas.set_content(self._rendered_pixmap, monitors, self._focus_monitor)
                return

            self._last_render_cache_key = current_cache_key

            # 生成高清渲染图 (宽 1280 高 720 以保障大屏下绝不模糊)
            pil_img = self._wp_mgr.generate_preview_image(
                preview_width=1280,
                preview_height=720,
                image_paths=self._current_paths,
            )
            self._rendered_pil = pil_img

            # 转换成 QPixmap
            rgb_data = pil_img.convert("RGB").tobytes("raw", "RGB")
            qimg = QImage(rgb_data, pil_img.width, pil_img.height, pil_img.width * 3, QImage.Format.Format_RGB888)
            self._rendered_pixmap = QPixmap.fromImage(qimg)

            self.mockup_canvas.set_content(self._rendered_pixmap, monitors, self._focus_monitor)
            self._update_used_pills(self._current_paths)

        except Exception as exc:
            logger.warning("预览舞台刷新异常: %s", exc)

    def _update_focus_chips(self, monitors: list[MonitorInfo]) -> None:
        """更新多显示器聚焦切换 Chips."""
        # 清除旧 Chip 按钮 (保留前置文字)
        while self.chip_bar.count() > 1:
            item = self.chip_bar.takeAt(1)
            if item.widget():
                item.widget().deleteLater()

        if len(monitors) <= 1:
            self.lbl_chip_prompt.setVisible(False)
            return

        self.lbl_chip_prompt.setVisible(True)

        # "全部" Chip
        btn_all = QPushButton("全部屏幕 (All)")
        btn_all.setObjectName("SecondaryBtn" if self._focus_monitor is not None else "")
        btn_all.setFixedHeight(26)
        btn_all.clicked.connect(lambda: self._set_focus(None))
        self.chip_bar.addWidget(btn_all)

        for idx, mon in enumerate(monitors):
            label = f"#{idx + 1} {'(主屏)' if mon.is_primary else ''} {mon.width}×{mon.height}"
            btn = QPushButton(label)
            btn.setObjectName("SecondaryBtn" if self._focus_monitor != idx else "")
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda _, i=idx: self._set_focus(i))
            self.chip_bar.addWidget(btn)

        self.chip_bar.addStretch()

    def _set_focus(self, focus_idx: int | None) -> None:
        self._focus_monitor = focus_idx
        self.mockup_canvas.set_focus(focus_idx)
        # 刷新 Chip 按钮高亮
        mon_data = self._wp_mgr.monitors
        monitors = mon_data() if callable(mon_data) else mon_data
        self._update_focus_chips(monitors)

    def _on_canvas_monitor_clicked(self, mon_idx: int) -> None:
        """在画布上点击了某个显示器，直接切换聚焦."""
        if self._focus_monitor == mon_idx:
            self._set_focus(None)
        else:
            self._set_focus(mon_idx)

    def _update_used_pills(self, paths: list[str]) -> None:
        """更新底部使用的图片胶囊小卡片."""
        while self.layout_used_items.count() > 0:
            item = self.layout_used_items.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not paths:
            lbl_empty = QLabel("暂未选用图片")
            lbl_empty.setStyleSheet("color: #64748b; font-size: 11px;")
            self.layout_used_items.addWidget(lbl_empty)
            self.layout_used_items.addStretch()
            return

        for idx, p in enumerate(paths):
            pill = QFrame()
            pill.setStyleSheet(
                "background-color: #172237; border: 1px solid #2d3b50; border-radius: 6px; padding: 2px 6px;"
            )
            pill_layout = QHBoxLayout(pill)
            pill_layout.setContentsMargins(4, 2, 6, 2)
            pill_layout.setSpacing(6)

            file_path = Path(p)
            file_name = file_path.name
            if len(file_name) > 18:
                file_name = file_name[:15] + "..."

            # 缩略图
            lbl_icon = QLabel()
            lbl_icon.setFixedSize(32, 22)
            lbl_icon.setStyleSheet("background-color: #0d131f; border-radius: 3px;")
            lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            meta = self._library.get_metadata(p)
            thumb = self._library.get_thumbnail(p, max_size=64)
            if thumb and not thumb.isNull():
                lbl_icon.setPixmap(thumb.scaled(32, 22, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                lbl_icon.setText("图")
                lbl_icon.setStyleSheet("background-color: #18181b; color: #71717a; font-size: 10px; border-radius: 3px;")

            lbl_name = QLabel(file_name)
            lbl_name.setStyleSheet("color: #ffffff; font-size: 11px; font-weight: 500;")
            lbl_name.setToolTip(f"{p}\n分辨率: {meta.width if meta else '?'}x{meta.height if meta else '?'}")

            btn_reveal = QPushButton("定位")
            btn_reveal.setFixedSize(38, 22)
            btn_reveal.setObjectName("SecondaryBtn")
            btn_reveal.setToolTip("在 Windows 资源管理器中高亮定位该文件")
            btn_reveal.clicked.connect(lambda _, target=p: self._reveal_in_explorer(target))

            pill_layout.addWidget(lbl_icon)
            pill_layout.addWidget(lbl_name)
            pill_layout.addWidget(btn_reveal)
            self.layout_used_items.addWidget(pill)

        self.layout_used_items.addStretch()

    def _reveal_in_explorer(self, file_path: str) -> None:
        try:
            if os.path.exists(file_path):
                subprocess.Popen(["explorer.exe", f"/select,{os.path.normpath(file_path)}"])
        except Exception as exc:
            logger.warning("定位文件失败: %s", exc)

    def _on_shuffle_clicked(self) -> None:
        """随机挑选一组图片并重绘预览."""
        all_paths = self._library.get_all_paths()
        if not all_paths:
            QMessageBox.information(self, "提示", "当前图片库中尚无有效图片，请先添加图片文件夹并扫描。")
            return
        wp_cfg = self._wp_mgr._settings.config.wallpaper
        target_count = max(2, min(8, wp_cfg.collage_count)) if wp_cfg.collage_enabled else 1
        # 从随机选择器或列表打乱
        import random
        selected = random.sample(all_paths, min(len(all_paths), target_count))
        self.refresh_preview(override_paths=selected)

    def _on_save_clicked(self) -> None:
        """保存当前预览壁纸为高清图片文件."""
        if not self._rendered_pil:
            QMessageBox.warning(self, "提示", "暂无正在渲染的壁纸画面。")
            return
        default_name = f"wallflow_collage_{len(self._current_paths)}imgs.png"
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存当前合成壁纸为高清图片",
            default_name,
            "PNG 图片 (*.png);;JPEG 图片 (*.jpg);;BMP 位图 (*.bmp)",
        )
        if save_path:
            try:
                self._rendered_pil.save(save_path)
                QMessageBox.information(self, "成功", f"壁纸已成功保存到：\n{save_path}")
            except Exception as exc:
                QMessageBox.critical(self, "保存失败", f"保存图片出错: {exc}")

    def _on_expand_clicked(self) -> None:
        """全屏放大预览."""
        if not self._rendered_pixmap or self._rendered_pixmap.isNull():
            return
        lightbox = FullscreenLightbox(self._rendered_pixmap, "高清壁纸全屏效果", self)
        lightbox.exec()

    def _on_apply_clicked(self) -> None:
        """直接应用当前展示的图片."""
        if not self._current_paths:
            QMessageBox.warning(self, "提示", "当前没有选中的图片可供应用。")
            return
        self.applyRequested.emit(self._current_paths)

"""Windows 原生屏幕保护程序核心引擎 (`app/screensaver.py`).

支持：
- 命令行参数解析 (`parse_screensaver_args`)：完整兼容 `/s`、`/c`、`/c:<hwnd>`、`/p <hwnd>`、`/p:<hwnd>`、`-s`、`-c`、`-p` 及无参启动。
- 多显示器无边框全屏渲染 (`ScreensaverCanvasWidget`, `ScreensaverController`)：
  - 每个显示器独立创建无边框置顶窗口，或嵌入 `/p <hwnd>` 预览小窗
  - 支持各显示器独立图片选择与横屏/竖屏方向智能匹配
  - 支持不放回随机洗牌与顺序播放
  - 支持 Fill、Fit、Stretch、Center 布局及自定义背景色
  - 支持无过渡 (none)、淡入淡出 (fade)、平滑平移 (slide)、微距缩放 (zoom) 动画及过渡时长配置
  - 按实际显示器分辨率降采样解码 + 有界 LRU 缓存，过渡期间仅保留当前帧与下一帧
  - 遇到损坏图片自动跳过并继续播放，绝不崩溃退出
  - 支持 ESC、任意按键、鼠标点击、滚轮及鼠标移动退出，退出后彻底清理窗口与进程
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass
from typing import Callable

from PIL import Image
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QKeyEvent, QMouseEvent, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QApplication, QWidget

from app.image_library import BoundedByteLRUCache, ImageLibrary, ImageMetadata
from app.image_processor import load_image_for_target, parse_rgb_color, render_image_layout
from app.logger import get_logger
from app.random_selector import ImageSelector
from app.settings_manager import SettingsManager
from app.win32_integration import (
    embed_window_into_preview_hwnd,
    get_window_client_rect,
    is_valid_window_handle,
    set_thread_display_required,
)

logger = get_logger("screensaver")

ERROR_ALREADY_EXISTS = 183


class ScreensaverMutexGuard:
    """全局屏保单实例互斥锁，跨进程排他保护 (防止 Windows 原生 .scr 屏保与 WallFlow 看门狗同时启动两套全屏画布)."""

    _mutex_handle: int | None = None

    @classmethod
    def is_active(cls) -> bool:
        """检查全局是否已有屏保正在运行."""
        if sys.platform != "win32":
            return False
        if cls._mutex_handle is not None:
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenMutexW(SYNCHRONIZE, False, r"Local\WallFlow_Screensaver_Active_Mutex_v1")
            if handle:
                kernel32.CloseHandle(handle)
                return True
        except Exception:
            pass
        return False

    @classmethod
    def acquire(cls) -> bool:
        """尝试获取全局屏保锁。若已被持有返回 False."""
        if sys.platform != "win32":
            return True
        if cls._mutex_handle is not None:
            return True
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetLastError(0)
            handle = kernel32.CreateMutexW(None, False, r"Local\WallFlow_Screensaver_Active_Mutex_v1")
            last_err = kernel32.GetLastError()
            if last_err == ERROR_ALREADY_EXISTS:
                if handle:
                    kernel32.CloseHandle(handle)
                return False
            cls._mutex_handle = handle
            return True
        except Exception as exc:
            logger.debug("获取屏保全局互斥锁异常: %s", exc)
            return True

    @classmethod
    def release(cls) -> None:
        """释放全局屏保锁."""
        if sys.platform == "win32" and cls._mutex_handle is not None:
            try:
                kernel32 = ctypes.windll.kernel32
                kernel32.CloseHandle(cls._mutex_handle)
            except Exception:
                pass
            cls._mutex_handle = None


@dataclass(frozen=True)
class ScreensaverCommand:
    """解析后的 Windows 屏保启动指令."""

    mode: str  # "screensaver" | "config" | "preview"
    preview_hwnd: int | None = None
    parent_hwnd: int | None = None
    auto_close_ms: int | None = None


def parse_screensaver_args(argv: list[str]) -> ScreensaverCommand:
    """解析 Windows 屏幕保护程序命令行参数.

    兼容格式：
    - `/s`, `-s`, `/S`, `--screensaver` -> 启动全屏屏保
    - `/c`, `-c`, `/c:12345`, `-c:12345`, `/C` 或无参数 -> 打开屏保配置对话框
    - `/p 12345`, `/p:12345`, `-p 12345`, `-p:12345`, `/P 12345` -> 在预览窗口句柄中运行
    - `--auto-close-ms <ms>` -> 自动化测试专用定时退出参数
    """
    args = [str(a).strip() for a in argv if str(a).strip()]
    mode = "config"
    preview_hwnd: int | None = None
    parent_hwnd: int | None = None
    auto_close_ms: int | None = None

    i = 0
    while i < len(args):
        raw = args[i]
        lower = raw.lower()

        if lower == "--auto-close-ms" and i + 1 < len(args):
            try:
                auto_close_ms = max(50, int(args[i + 1]))
            except ValueError:
                pass
            i += 2
            continue
        if lower.startswith("--auto-close-ms="):
            try:
                auto_close_ms = max(50, int(lower.split("=", 1)[1]))
            except ValueError:
                pass
            i += 1
            continue

        # 剥离前导 / 或 - 或 --
        token = lower.lstrip("/-")
        if token == "s" or token == "screensaver":
            mode = "screensaver"
            i += 1
            continue

        if token == "c" or token == "config" or token == "configure":
            mode = "config"
            if i + 1 < len(args) and not args[i + 1].startswith(("/", "-")):
                try:
                    parent_hwnd = int(args[i + 1])
                    i += 2
                    continue
                except ValueError:
                    pass
            i += 1
            continue

        if token.startswith("c:"):
            mode = "config"
            hwnd_part = token[2:].strip()
            try:
                parent_hwnd = int(hwnd_part)
            except ValueError:
                pass
            i += 1
            continue

        if token == "p" or token == "preview" or token == "l":
            mode = "preview"
            if i + 1 < len(args):
                try:
                    preview_hwnd = int(args[i + 1])
                    i += 2
                    continue
                except ValueError:
                    pass
            i += 1
            continue

        if token.startswith("p:"):
            mode = "preview"
            hwnd_part = token[2:].strip()
            try:
                preview_hwnd = int(hwnd_part)
            except ValueError:
                pass
            i += 1
            continue

        i += 1

    return ScreensaverCommand(
        mode=mode,
        preview_hwnd=preview_hwnd,
        parent_hwnd=parent_hwnd,
        auto_close_ms=auto_close_ms,
    )


def pil_to_qpixmap(img: Image.Image) -> QPixmap:
    """将 RGB `PIL.Image` 高效转换为 `QPixmap`."""
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    qimg = QImage(data, rgb.width, rgb.height, rgb.width * 3, QImage.Format.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


class ScreensaverGlobalEventFilter(QObject):
    """全局屏保输入监听过滤器，拦截所有显示器和控件的键盘与鼠标操作，保证瞬间灵敏退出."""

    def __init__(
        self,
        exit_callback: Callable[[str], None],
        exit_on_mouse_move: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._exit_callback = exit_callback
        self._exit_on_mouse_move = exit_on_mouse_move
        self._start_monotonic = time.monotonic()
        self._initial_mouse_pos: QPoint | None = None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        etype = event.type()
        if etype in (
            QEvent.Type.KeyPress,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.Wheel,
        ):
            self._exit_callback(f"GlobalInput({etype.name})")
            return True

        if etype == QEvent.Type.MouseMove and self._exit_on_mouse_move:
            if time.monotonic() - self._start_monotonic < 0.4:
                if hasattr(event, "globalPosition"):
                    self._initial_mouse_pos = event.globalPosition().toPoint()
                return False
            if hasattr(event, "globalPosition"):
                pos = event.globalPosition().toPoint()
                if self._initial_mouse_pos is None:
                    self._initial_mouse_pos = pos
                elif (pos - self._initial_mouse_pos).manhattanLength() >= 8:
                    self._exit_callback("GlobalInput(MouseMove)")
                    return True
        return super().eventFilter(watched, event)


class ScreensaverCanvasWidget(QWidget):
    """单个显示器（或 `/p` 预览小窗）的屏保画布组件.

    仅保留当前帧 (`_current_pixmap`) 和过渡目标帧 (`_next_pixmap`)，
    确保高分辨率图片下内存占用恒定可控。
    """

    def __init__(
        self,
        target_width: int,
        target_height: int,
        is_preview: bool,
        background_color: str = "#000000",
        transition_effect: str = "fade",
        transition_duration_ms: int = 800,
        exit_on_mouse_move: bool = True,
        show_clock_overlay: bool = False,
        clock_size: str = "medium",
        clock_position: str = "bottom_left",
        on_user_exit: Callable[[str], None] | None = None,
        on_manual_step: Callable[[int], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._target_w = max(1, int(target_width))
        self._target_h = max(1, int(target_height))
        self._is_preview = bool(is_preview)
        self._bg_color = QColor(*parse_rgb_color(background_color))
        self._transition_effect = transition_effect if transition_effect in ("none", "fade", "slide", "zoom") else "fade"
        self._transition_duration_ms = max(0, int(transition_duration_ms))
        self._exit_on_mouse_move = bool(exit_on_mouse_move)
        self._show_clock_overlay = bool(show_clock_overlay)
        self._clock_size = clock_size if clock_size in ("small", "medium", "large", "xlarge") else "medium"
        self._clock_position = clock_position if clock_position in ("bottom_left", "center_top", "center_bottom") else "bottom_left"
        self._on_user_exit = on_user_exit
        self._on_manual_step = on_manual_step

        self._current_pixmap: QPixmap | None = None
        self._next_pixmap: QPixmap | None = None
        self._current_caption: str = ""
        self._in_transition: bool = False
        self._transition_start_ts: float = 0.0
        self._transition_progress: float = 1.0

        self._launch_monotonic: float = time.monotonic()
        self._initial_mouse_pos: QPoint | None = None

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)  # ~60 FPS 非阻塞过渡动画
        self._anim_timer.timeout.connect(self._on_anim_tick)

        self._clock_timer: QTimer | None = None
        if self._show_clock_overlay and not self._is_preview:
            self._clock_timer = QTimer(self)
            self._clock_timer.setInterval(1000)
            self._clock_timer.timeout.connect(self.update)
            self._clock_timer.start()

        if not self._is_preview:
            self.setWindowFlags(
                Qt.WindowType.Window
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
            )
            self.setCursor(Qt.CursorShape.BlankCursor)
            self.setMouseTracking(True)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        else:
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def show_image(self, pixmap: QPixmap, caption: str = "", immediate: bool = False) -> None:
        """切换到新图像，根据配置启动平滑过渡动画."""
        self._current_caption = caption
        if (
            immediate
            or self._current_pixmap is None
            or self._transition_effect == "none"
            or self._transition_duration_ms <= 20
        ):
            self._anim_timer.stop()
            self._current_pixmap = pixmap
            self._next_pixmap = None
            self._in_transition = False
            self._transition_progress = 1.0
            self.update()
            return

        # 如果上一次过渡尚未结束，先将上一帧目标提升为当前帧
        if self._in_transition and self._next_pixmap is not None:
            self._current_pixmap = self._next_pixmap

        self._next_pixmap = pixmap
        self._in_transition = True
        self._transition_progress = 0.0
        self._transition_start_ts = time.perf_counter()
        self._anim_timer.start()
        self.update()

    def _on_anim_tick(self) -> None:
        if not self._in_transition:
            self._anim_timer.stop()
            return
        elapsed_ms = (time.perf_counter() - self._transition_start_ts) * 1000.0
        t = min(1.0, max(0.0, elapsed_ms / max(1, self._transition_duration_ms)))
        # Smoothstep 缓动曲线: 3t^2 - 2t^3
        self._transition_progress = t * t * (3.0 - 2.0 * t)
        if t >= 1.0:
            self._current_pixmap = self._next_pixmap
            self._next_pixmap = None
            self._in_transition = False
            self._transition_progress = 1.0
            self._anim_timer.stop()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, self._bg_color)

        if self._current_pixmap is None and self._next_pixmap is None:
            painter.setPen(QColor(180, 180, 180))
            font = QFont("Microsoft YaHei", 10 if self._is_preview else 16)
            painter.setFont(font)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Gou Wallpaper 屏幕保护程序\n(请在设置中添加有效图片文件夹)",
            )
            painter.end()
            return

        if not self._in_transition or self._next_pixmap is None or self._current_pixmap is None:
            active = self._next_pixmap or self._current_pixmap
            if active is not None:
                painter.drawPixmap(self.rect(), active)
        else:
            p = self._transition_progress
            eff = self._transition_effect
            if eff == "fade":
                painter.setOpacity(1.0)
                painter.drawPixmap(self.rect(), self._current_pixmap)
                painter.setOpacity(p)
                painter.drawPixmap(self.rect(), self._next_pixmap)
                painter.setOpacity(1.0)
            elif eff == "slide":
                offset_x = int(round((1.0 - p) * w))
                painter.drawPixmap(offset_x - w, 0, w, h, self._current_pixmap)
                painter.drawPixmap(offset_x, 0, w, h, self._next_pixmap)
            elif eff == "zoom":
                painter.setOpacity(1.0)
                painter.drawPixmap(self.rect(), self._current_pixmap)
                painter.setOpacity(p)
                scale = 1.08 - 0.08 * p
                zw = w * scale
                zh = h * scale
                zx = (w - zw) / 2.0
                zy = (h - zh) / 2.0
                painter.drawPixmap(
                    QRectF(zx, zy, zw, zh),
                    self._next_pixmap,
                    QRectF(0, 0, self._next_pixmap.width(), self._next_pixmap.height()),
                )
                painter.setOpacity(1.0)
            else:
                painter.drawPixmap(self.rect(), self._next_pixmap)

        if self._show_clock_overlay and not self._is_preview:
            now_time = time.strftime("%H:%M")
            weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
            try:
                weekday_str = weekdays[time.localtime().tm_wday]
            except Exception:
                weekday_str = ""
            now_date = time.strftime("%m月%d日") + (f"  {weekday_str}" if weekday_str else "")

            # 动态根据设置的 clock_size 计算字体大小，以当前屏幕高度自适应
            # 档位：small, medium(默认当前大小), large, xlarge
            if self._clock_size == "small":
                time_pt = max(36, min(56, int(h * 0.045)))
                date_pt = max(13, min(18, int(time_pt * 0.32)))
            elif self._clock_size == "large":
                time_pt = max(96, min(156, int(h * 0.12)))
                date_pt = max(22, min(34, int(time_pt * 0.24)))
            elif self._clock_size == "xlarge":
                time_pt = max(130, min(210, int(h * 0.16)))
                date_pt = max(28, min(44, int(time_pt * 0.22)))
            else:  # "medium" 默认基准大小
                time_pt = max(64, min(108, int(h * 0.08)))
                date_pt = max(16, min(24, int(time_pt * 0.26)))

            clock_font = QFont("Segoe UI", time_pt, QFont.Weight.Bold)
            time_fm = QFontMetrics(clock_font)
            time_h = time_fm.height()
            time_w = time_fm.horizontalAdvance(now_time)

            date_font = QFont("Microsoft YaHei", date_pt, QFont.Weight.Medium)
            date_fm = QFontMetrics(date_font)
            date_h = date_fm.height()
            date_w = date_fm.horizontalAdvance(now_date)

            # 动态计算日期与时钟数字之间的垂直间距：将上下间距缩短一半
            # 时间数字基线位于 time_fm.ascent()，在此基础上间距精确缩短 50%，保持紧凑优雅且绝不重合
            old_gap_from_baseline = time_fm.descent() + max(8, int(time_pt * 0.14))
            gap = max(6, int(old_gap_from_baseline * 0.5))
            time_effective_h = time_fm.ascent()
            total_clock_h = time_effective_h + gap + date_h

            # 根据 clock_position 计算坐标与对齐方式
            # 用户要求：时钟数字的颜色与日期一样的白色；日期/星期的中心与时钟的中心严格保持一致（以时钟为准）
            if self._clock_position == "center_top":
                center_x = w / 2.0
                box_y = max(48, int(h * 0.12))
            elif self._clock_position == "center_bottom":
                center_x = w / 2.0
                box_y = h - total_clock_h - max(48, int(h * 0.07))
            else:  # "bottom_left"
                base_x = max(48, int(w * 0.04))
                # 以时钟为主：时钟起始位于 base_x，中心为 base_x + time_w / 2.0
                # 下方日期星期中心严格与时钟中心对齐，并在日期较宽时左侧预留安全屏幕边距
                center_x = max(base_x + time_w / 2.0, date_w / 2.0 + 24)
                box_y = h - total_clock_h - max(48, int(h * 0.06))

            box_w = max(time_w, date_w) + 20
            box_x = center_x - box_w / 2.0
            align = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop

            # 颜色规范：时钟数字与日期星期采用完全统一的柔白色彩 (#e4e4e7, alpha=230)
            clock_text_color = QColor(228, 228, 231, 230)
            clock_shadow_color = QColor(0, 0, 0, 160)

            # 双层投影渲染，无论任何浅色或复杂壁纸底色均清晰可见
            # 1. 渲染时间数字
            painter.setFont(clock_font)
            # 阴影层
            painter.setPen(clock_shadow_color)
            painter.drawText(
                QRectF(box_x + 2, box_y + 2, box_w, time_h + 4),
                align,
                now_time,
            )
            # 主文字层
            painter.setPen(clock_text_color)
            painter.drawText(
                QRectF(box_x, box_y, box_w, time_h + 4),
                align,
                now_time,
            )

            # 2. 渲染日期与星期（起始 y 坐标严格位于时间数字底部 + gap，间距缩短一半，绝对不发生任何遮挡）
            # 与上方时钟数字共用相同的 center_x 与 AlignHCenter，两者中心轴线严格一致
            date_y = box_y + time_effective_h + gap
            painter.setFont(date_font)
            # 阴影层
            painter.setPen(clock_shadow_color)
            painter.drawText(
                QRectF(box_x + 1, date_y + 1, box_w, date_h + 4),
                align,
                now_date,
            )
            # 主文字层
            painter.setPen(clock_text_color)
            painter.drawText(
                QRectF(box_x, date_y, box_w, date_h + 4),
                align,
                now_date,
            )

        painter.end()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self._is_preview:
            return
        if self._on_user_exit:
            self._on_user_exit(f"KeyPress({event.key()})")

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._is_preview:
            return
        if self._on_user_exit:
            self._on_user_exit("MousePress")

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self._is_preview:
            return
        if self._on_user_exit:
            self._on_user_exit("MouseWheel")

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._is_preview or not self._exit_on_mouse_move:
            return
        # 启动后前 0.6 秒忽略系统初始鼠标坐标同步抖动
        if time.monotonic() - self._launch_monotonic < 0.6:
            self._initial_mouse_pos = event.globalPosition().toPoint()
            return
        cur_pos = event.globalPosition().toPoint()
        if self._initial_mouse_pos is None:
            self._initial_mouse_pos = cur_pos
            return
        manhattan = (cur_pos - self._initial_mouse_pos).manhattanLength()
        if manhattan >= 12 and self._on_user_exit:
            self._on_user_exit(f"MouseMove({manhattan}px)")

    def cleanup(self) -> None:
        self._anim_timer.stop()
        if self._clock_timer is not None:
            self._clock_timer.stop()
            self._clock_timer = None
        self._current_pixmap = None
        self._next_pixmap = None


class ScreensaverController:
    """多显示器全屏屏保与 `/p` 预览窗口控制器."""

    def __init__(
        self,
        settings_manager: SettingsManager,
        image_library: ImageLibrary | None = None,
        on_closed: Callable[[], None] | None = None,
    ) -> None:
        self._logger = get_logger("screensaver_ctrl")
        self._settings = settings_manager
        self._on_closed = on_closed
        self._own_library = image_library is None
        self._library = image_library or ImageLibrary(
            thumbnail_cache_max_mb=self._settings.config.system.thumbnail_cache_max_mb,
            disk_cache_dir=self._settings.cache_dir / "thumbnails",
        )
        self._active_library: ImageLibrary = self._library

        # 按显示器分辨率缩放后的图像 LRU 缓存（上限 24 MB / 2 张，严格控制内存占用）
        self._scaled_cache = BoundedByteLRUCache(max_bytes=24 * 1024 * 1024, max_items=2)

        self._windows: list[ScreensaverCanvasWidget] = []
        self._selectors_by_window: dict[int, ImageSelector] = {}
        self._shared_selector: ImageSelector | None = None
        self._switch_timer: QTimer | None = None
        self._preview_watchdog_timer: QTimer | None = None
        self._auto_close_timer: QTimer | None = None
        self._timed_sleep_timer: QTimer | None = None
        self._timed_sleep_fired: bool = False
        self._event_filter: ScreensaverGlobalEventFilter | None = None
        self._preview_parent_hwnd: int | None = None
        self._running: bool = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def windows(self) -> list[ScreensaverCanvasWidget]:
        return list(self._windows)

    def _prepare_library_and_candidates(self) -> list[ImageMetadata]:
        """根据屏保配置准备图片库并返回有效图片元数据列表."""
        try:
            self._settings.load()
        except Exception:
            pass

        ss_cfg = self._settings.config.screensaver
        wp_cfg = self._settings.config.wallpaper

        has_custom_folders = bool(ss_cfg.folders and any(f.enabled for f in ss_cfg.folders))

        # 1. 独立屏保文件夹模式：若未启用共用壁纸库且配置了独立文件夹，优先扫描专属独立图片库
        if not ss_cfg.use_wallpaper_library and has_custom_folders:
            self._logger.info(
                "屏保正在使用独立图片来源，开始扫描独立屏保文件夹 (共 %d 个启用目录)...",
                len([f for f in ss_cfg.folders if f.enabled]),
            )
            custom_lib = ImageLibrary(
                thumbnail_cache_max_mb=self._settings.config.system.thumbnail_cache_max_mb,
                disk_cache_dir=self._settings.cache_dir / "thumbnails",
            )
            custom_lib.set_sources(ss_cfg.folders)
            custom_lib.scan()
            images = custom_lib.get_all_images()
            if images:
                self._active_library = custom_lib
                self._logger.info("独立屏保图片库扫描完成，候选图片数=%d", len(images))
                return images
            self._logger.warning("独立屏保文件夹中未扫描到有效图片，将自动回退使用桌面壁纸库")

        # 2. 壁纸库模式：若传入了外部图片库且已有缓存图片，直接复用以秒级启动
        if self._library is not None:
            existing = self._library.get_all_images()
            if existing:
                self._active_library = self._library
                return existing

        # 3. 若无现成图片，扫描桌面壁纸文件夹
        fallback_lib = ImageLibrary(
            thumbnail_cache_max_mb=self._settings.config.system.thumbnail_cache_max_mb,
            disk_cache_dir=self._settings.cache_dir / "thumbnails",
        )
        fallback_lib.set_sources(wp_cfg.folders)
        fallback_lib.scan()
        self._active_library = fallback_lib
        return self._active_library.get_all_images()

    def start_fullscreen(self, auto_close_ms: int | None = None) -> bool:
        """在全部目标显示器上启动无边框全屏屏保."""
        if self._running:
            return True

        # 全局唯一排他性互斥检查：防止系统原生 .scr 与主程序看门狗同时启动
        if not ScreensaverMutexGuard.acquire():
            self._logger.info("全局已有屏保正在运行，跳过本次启动以保持单实例唯一")
            return False

        ss_cfg = self._settings.config.screensaver
        self._timed_sleep_fired = False

        # 依据配置决定是否向 Windows 申请阻止系统休眠/息屏 (解决屏保阻止睡眠问题)
        if getattr(ss_cfg, "prevent_system_sleep", True):
            set_thread_display_required(True)
        else:
            set_thread_display_required(False)
        app = QApplication.instance()
        screens = app.screens() if app is not None else []

        self._windows.clear()
        self._selectors_by_window.clear()

        # 1. 极致响应：第一时间在所有屏幕上创建并展示纯黑无边框画布，实现瞬间启动
        if not screens:
            w = ScreensaverCanvasWidget(
                target_width=1920,
                target_height=1080,
                is_preview=False,
                background_color=ss_cfg.background_color,
                transition_effect=ss_cfg.transition_effect,
                transition_duration_ms=ss_cfg.transition_duration_ms,
                exit_on_mouse_move=ss_cfg.exit_on_mouse_move,
                show_clock_overlay=ss_cfg.show_clock_overlay,
                clock_size=getattr(ss_cfg, "clock_size", "medium"),
                clock_position=getattr(ss_cfg, "clock_position", "bottom_left"),
                on_user_exit=self.stop,
            )
            w.setGeometry(0, 0, 1920, 1080)
            self._windows.append(w)
        else:
            for screen in screens:
                geom = screen.geometry()
                sw = max(1, geom.width())
                sh = max(1, geom.height())
                canvas = ScreensaverCanvasWidget(
                    target_width=sw,
                    target_height=sh,
                    is_preview=False,
                    background_color=ss_cfg.background_color,
                    transition_effect=ss_cfg.transition_effect,
                    transition_duration_ms=ss_cfg.transition_duration_ms,
                    exit_on_mouse_move=ss_cfg.exit_on_mouse_move,
                    show_clock_overlay=ss_cfg.show_clock_overlay,
                    clock_size=getattr(ss_cfg, "clock_size", "medium"),
                    clock_position=getattr(ss_cfg, "clock_position", "bottom_left"),
                    on_user_exit=self.stop,
                )
                canvas.setGeometry(geom)
                self._windows.append(canvas)

        self._running = True
        for idx, win in enumerate(self._windows):
            win.showFullScreen()
            win.raise_()
            if idx == 0:
                win.activateWindow()

        # 2. 安装全局无死角输入监听器，任意键盘/鼠标操作瞬间退出
        if app is not None:
            self._event_filter = ScreensaverGlobalEventFilter(
                exit_callback=self.stop,
                exit_on_mouse_move=ss_cfg.exit_on_mouse_move,
            )
            app.installEventFilter(self._event_filter)

        # 3. 准备候选图片列表与选择器
        all_metas = self._prepare_library_and_candidates()
        all_paths = [m.path for m in all_metas]
        landscape_paths = [m.path for m in all_metas if m.is_landscape] or all_paths
        portrait_paths = [m.path for m in all_metas if m.is_portrait] or all_paths

        self._shared_selector = ImageSelector(candidates=all_paths, mode=ss_cfg.playback_mode)
        self._shared_selector.restore_from_dict(ss_cfg.selector_state)

        for idx, win in enumerate(self._windows):
            sw = max(1, win.width())
            sh = max(1, win.height())
            is_land = sw >= sh

            if ss_cfg.match_orientation:
                mon_candidates = landscape_paths if is_land else portrait_paths
            else:
                mon_candidates = all_paths

            if ss_cfg.independent_per_monitor:
                sel = ImageSelector(candidates=mon_candidates, mode=ss_cfg.playback_mode)
            else:
                sel = self._shared_selector
            self._selectors_by_window[idx] = sel

        # 4. 立即渲染首帧图片
        self._advance_all_windows(immediate=True)

        # 5. 启动内部轮播定时器
        interval_ms = max(3000, int(ss_cfg.interval_seconds) * 1000)
        self._switch_timer = QTimer()
        self._switch_timer.setInterval(interval_ms)
        self._switch_timer.timeout.connect(lambda: self._advance_all_windows(immediate=False))
        self._switch_timer.start()

        # 6. 定时进入睡眠/息屏计时器
        timed_mins = int(getattr(ss_cfg, "timed_sleep_minutes", 0))
        if timed_mins > 0:
            self._timed_sleep_timer = QTimer()
            self._timed_sleep_timer.setSingleShot(True)
            self._timed_sleep_timer.setInterval(timed_mins * 60 * 1000)
            self._timed_sleep_timer.timeout.connect(self._on_timed_sleep_due)
            self._timed_sleep_timer.start()
            self._logger.info(
                "已启动屏保定时睡眠计时器: %d 分钟后执行电源动作 (%s)",
                timed_mins,
                getattr(ss_cfg, "timed_sleep_action", "sleep"),
            )

        if auto_close_ms is not None and auto_close_ms > 0:
            self._auto_close_timer = QTimer()
            self._auto_close_timer.setSingleShot(True)
            self._auto_close_timer.timeout.connect(lambda: self.stop("AutoCloseTimer"))
            self._auto_close_timer.start(int(auto_close_ms))

        self._logger.info(
            "全屏屏幕保护程序已启动: 显示器数=%d, 候选图片数=%d, 切换间隔=%ds, 过渡=%s",
            len(self._windows),
            len(all_paths),
            ss_cfg.interval_seconds,
            ss_cfg.transition_effect,
        )
        return True

    def start_preview(self, preview_hwnd: int, auto_close_ms: int | None = None) -> bool:
        """在 Windows 屏保设置对话框的预览窗口 (`preview_hwnd`) 中启动内嵌预览."""
        if self._running:
            return True

        ss_cfg = self._settings.config.screensaver
        all_metas = self._prepare_library_and_candidates()
        all_paths = [m.path for m in all_metas]

        pw, ph = get_window_client_rect(preview_hwnd)
        self._preview_parent_hwnd = preview_hwnd
        self._shared_selector = ImageSelector(candidates=all_paths, mode=ss_cfg.playback_mode)

        preview_widget = ScreensaverCanvasWidget(
            target_width=pw,
            target_height=ph,
            is_preview=True,
            background_color=ss_cfg.background_color,
            transition_effect=ss_cfg.transition_effect,
            transition_duration_ms=min(500, ss_cfg.transition_duration_ms),
            exit_on_mouse_move=False,
            show_clock_overlay=False,
        )
        preview_widget.resize(pw, ph)
        preview_widget.show()

        if is_valid_window_handle(preview_hwnd):
            child_id = int(preview_widget.winId())
            embed_window_into_preview_hwnd(child_id, preview_hwnd)

        self._windows = [preview_widget]
        self._selectors_by_window = {0: self._shared_selector}
        self._running = True

        self._advance_all_windows(immediate=True)

        interval_ms = max(3000, int(ss_cfg.interval_seconds) * 1000)
        self._switch_timer = QTimer()
        self._switch_timer.setInterval(interval_ms)
        self._switch_timer.timeout.connect(lambda: self._advance_all_windows(immediate=False))
        self._switch_timer.start()

        # 定期检查父预览窗口是否已被关闭
        if preview_hwnd and preview_hwnd > 0:
            self._preview_watchdog_timer = QTimer()
            self._preview_watchdog_timer.setInterval(500)
            self._preview_watchdog_timer.timeout.connect(self._check_preview_parent_alive)
            self._preview_watchdog_timer.start()

        if auto_close_ms is not None and auto_close_ms > 0:
            self._auto_close_timer = QTimer()
            self._auto_close_timer.setSingleShot(True)
            self._auto_close_timer.timeout.connect(lambda: self.stop("PreviewAutoCloseTimer"))
            self._auto_close_timer.start(int(auto_close_ms))

        self._logger.info("屏保预览模式 (/p) 已启动: parent_hwnd=%s, size=%dx%d", preview_hwnd, pw, ph)
        return True

    def _check_preview_parent_alive(self) -> None:
        if self._preview_parent_hwnd and not is_valid_window_handle(self._preview_parent_hwnd):
            self.stop("PreviewParentClosed")

    def _advance_all_windows(self, immediate: bool = False) -> None:
        if not self._running:
            return
        ss_cfg = self._settings.config.screensaver
        # 仅在启用了阻止睡眠且未触发定时睡眠时维持 Windows 显示活跃
        if getattr(ss_cfg, "prevent_system_sleep", True) and not getattr(self, "_timed_sleep_fired", False):
            set_thread_display_required(True)
        shared_path: str | None = None
        if not ss_cfg.independent_per_monitor and self._shared_selector is not None:
            shared_path = self._draw_valid_path(self._shared_selector)

        for idx, win in enumerate(self._windows):
            try:
                tw = max(1, win.width())
                th = max(1, win.height())
                if not ss_cfg.independent_per_monitor and shared_path is not None:
                    chosen_path = shared_path
                else:
                    sel = self._selectors_by_window.get(idx) or self._shared_selector
                    chosen_path = self._draw_valid_path(sel) if sel else None

                if not chosen_path:
                    win.update()
                    continue

                pixmap = self._load_scaled_pixmap(
                    chosen_path,
                    tw,
                    th,
                    ss_cfg.display_mode,
                    ss_cfg.background_color,
                )
                if pixmap is not None:
                    win.show_image(pixmap, caption=chosen_path, immediate=immediate)
            except Exception as exc:
                self._logger.warning("屏保窗口 %d 切换图像异常 (已跳过): %s", idx, exc)

    def _draw_valid_path(self, selector: ImageSelector, max_attempts: int = 5) -> str | None:
        """从选择器抽取一张有效图片；若图片损坏或被删除，自动跳过并继续尝试."""
        for _ in range(max_attempts):
            if selector.candidate_count == 0:
                return None
            p = selector.next()
            if not p:
                return None
            if self._active_library.is_path_valid(p):
                return p
            self._logger.warning("屏保播放期间跳过损坏或不存在的图片: %s", p)
            selector.remove_candidate(p)
        return None

    def _load_scaled_pixmap(
        self,
        path: str,
        target_w: int,
        target_h: int,
        display_mode: str,
        background_color: str,
    ) -> QPixmap | None:
        """按显示器实际分辨率降采样解码并应用有界 LRU 缓存."""
        cache_key = f"{path}|{target_w}x{target_h}|{display_mode}|{background_color}"
        cached = self._scaled_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            src_img = load_image_for_target(path, target_size=(target_w, target_h))
            rendered = render_image_layout(
                src_img,
                target_w,
                target_h,
                display_mode=display_mode,
                background_color=background_color,
            )
            pixmap = pil_to_qpixmap(rendered)
            est_bytes = target_w * target_h * 4
            self._scaled_cache.put(cache_key, pixmap, est_bytes)
            return pixmap
        except Exception as exc:
            self._logger.warning("屏保图片解码渲染失败 [%s]: %s", path, exc)
            self._active_library.remove_missing_or_invalid(path)
            if self._shared_selector:
                self._shared_selector.remove_candidate(path)
            for sel in self._selectors_by_window.values():
                sel.remove_candidate(path)
            return None

    def _on_timed_sleep_due(self) -> None:
        """屏保运行达到设定的定时时长，执行电源动作 (系统睡眠/息屏)."""
        if not self._running:
            return
        self._timed_sleep_fired = True
        ss_cfg = self._settings.config.screensaver
        action = getattr(ss_cfg, "timed_sleep_action", "sleep")
        mins = getattr(ss_cfg, "timed_sleep_minutes", 0)
        self._logger.info("屏保运行已达到设定的定时时长 (%d 分钟)，执行电源动作: %s", mins, action)

        # 1. 释放对 Windows 休眠/熄屏的阻止
        set_thread_display_required(False)

        # 2. 执行对应电源动作
        if action == "sleep":
            from app.win32_integration import suspend_system
            self.stop(reason="TimedSleep")
            suspend_system(hibernate=False)
        elif action == "turn_off_screen":
            from app.win32_integration import turn_off_display
            turn_off_display()
        elif action == "hibernate":
            from app.win32_integration import suspend_system
            self.stop(reason="TimedHibernate")
            suspend_system(hibernate=True)
        elif action == "allow_sleep":
            # 仅解除阻止，由 Windows 系统的电源计划自然接管休眠
            pass

    def stop(self, reason: str = "UserStop") -> None:
        """停止屏保，彻底关闭全部无边框窗口并释放缓存资源."""
        if not self._running:
            return
        self._running = False
        ScreensaverMutexGuard.release()
        set_thread_display_required(False)
        self._logger.info("屏幕保护程序正在退出 (触发原因: %s)", reason)

        if self._event_filter is not None:
            app = QApplication.instance()
            if app is not None:
                try:
                    app.removeEventFilter(self._event_filter)
                except Exception:
                    pass
            self._event_filter = None

        for timer in (self._switch_timer, self._preview_watchdog_timer, self._auto_close_timer, self._timed_sleep_timer):
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass
        self._switch_timer = None
        self._preview_watchdog_timer = None
        self._auto_close_timer = None
        self._timed_sleep_timer = None
        if not reason.startswith("Timed"):
            self._timed_sleep_fired = False

        if self._shared_selector is not None:
            try:
                self._settings.config.screensaver.selector_state = self._shared_selector.to_dict()
                self._settings.save()
            except Exception:
                pass

        for win in list(self._windows):
            try:
                win.cleanup()
                win.hide()
                win.close()
                win.deleteLater()
            except Exception:
                pass
        self._windows.clear()
        self._selectors_by_window.clear()
        self._scaled_cache.clear()

        if hasattr(self, "_active_library") and self._active_library is not None and self._active_library is not self._library:
            try:
                self._active_library.shutdown()
            except Exception:
                pass

        if self._own_library:
            try:
                self._library.shutdown()
            except Exception:
                pass

        if self._on_closed:
            try:
                self._on_closed()
            except Exception:
                pass


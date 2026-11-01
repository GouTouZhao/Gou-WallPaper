"""WallFlow 桌面壁纸与屏保管理主窗口 (`app/ui/main_window.py`).

现代化科技感纯黑白灰高对比度界面：
1. 真实物理拟真多显示器画框舞台 (PreviewStageWidget)；
2. 现代卡片式图片画廊与选择器 (ImageGalleryWidget / ImagePickerDialog)；
3. 底部常驻核心持久化操作栏 (BottomActionBar)；
4. 深度契合《要求.md》的 8 大功能分区与完整的调度器、屏保、托盘、快捷键逻辑。
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QFont, QHideEvent, QIcon, QImage, QPixmap, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.hotkeys import (
    HOTKEY_ID_NEXT,
    HOTKEY_ID_PREV,
    HOTKEY_ID_RANDOM,
    HOTKEY_ID_TOGGLE_DESKTOP_ICONS,
    HOTKEY_ID_TOGGLE_PAUSE,
    GlobalHotkeyManager,
)
from app.icon_helper import get_app_qicon
from app.image_library import ImageLibrary, ImageMetadata, ScanSummary
from app.image_processor import clear_image_cache
from app.logger import get_log_file_path, get_logger, get_memory_handler
from app.random_selector import ImageSelector
from app.scheduler import SchedulerStatusSnapshot, WallpaperScheduler
from app.screensaver import ScreensaverController, ScreensaverMutexGuard
from app.settings_manager import FolderSourceConfig, ImageAdjustmentConfig, SettingsManager
from app.startup_manager import StartupManager
from app.tray_manager import TrayManager
from app.ui.action_bar import BottomActionBar
from app.ui.image_gallery_view import ImageGalleryWidget, ImagePickerDialog
from app.ui.preview_stage import PreviewStageWidget
from app.ui.screensaver_config_dialog import ScreensaverConfigDialog
from app.ui.styles import MAIN_STYLESHEET
from app.wallpaper_manager import ApplyResult, WallpaperManager, compute_virtual_desktop_bounds
from app.win32_integration import (
    check_system_media_and_busy_state,
    get_system_idle_seconds,
    get_system_screensaver_status,
    heal_screensaver_registry_path,
    install_screensaver_to_user_desktop_config,
    open_windows_screensaver_settings,
    set_thread_display_required,
    toggle_desktop_icons_visibility,
    trim_process_memory,
)

logger = get_logger("ui.main")


class NoWheelScrollForwarder(QObject):
    """拦截子控件的滚轮事件并将其转发给所属 QScrollArea，避免滚动页面时误触修改配置并引发重新渲染."""

    def __init__(self, scroll_area: QScrollArea | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent or scroll_area)
        self._scroll_area = scroll_area

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel:
            # 如果下拉框已展开弹出列表视图，允许在弹出列表中滚动浏览
            if isinstance(watched, QComboBox):
                view = watched.view()
                if view is not None and view.isVisible():
                    return False
            # 转发给滚动区域的视口以实现平滑流畅的页面滚动
            if self._scroll_area is not None:
                QApplication.sendEvent(self._scroll_area.viewport(), event)
            return True
        return super().eventFilter(watched, event)


class BackgroundScanWorker(QThread):
    """后台工作线程，执行图片库扫描，避免阻塞 GUI 主线程."""

    progress = Signal(int, int, str)
    finished_summary = Signal(object)

    def __init__(self, library: ImageLibrary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library = library

    def run(self) -> None:
        summary = self._library.scan(progress_callback=self._on_progress)
        self.finished_summary.emit(summary)

    def _on_progress(self, current: int, total: int, file_path: str) -> None:
        self.progress.emit(current, total, file_path)


class MainWindow(QMainWindow):
    """WallFlow 主窗口 (现代深色科技感风格)."""

    wallpaperApplied = Signal(object)

    def __init__(
        self,
        settings_manager: SettingsManager,
        image_library: ImageLibrary,
        selector: ImageSelector,
        wallpaper_manager: WallpaperManager,
        scheduler: WallpaperScheduler,
        startup_manager: StartupManager,
        hotkey_manager: GlobalHotkeyManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings_manager
        self._library = image_library
        self._selector = selector
        self._wp_mgr = wallpaper_manager
        self._scheduler = scheduler
        self._startup_mgr = startup_manager
        self._hotkey_mgr = hotkey_manager or GlobalHotkeyManager()

        self._tray_mgr: TrayManager | None = None
        self._screensaver_preview_ctrl: ScreensaverController | None = None
        self._scan_worker: BackgroundScanWorker | None = None
        self._is_quitting = False
        self._is_loading_settings = False
        self._system_keep_awake_active = False

        self.setWindowTitle("Gou Wallpaper —— 现代化 Windows 桌面壁纸与屏保管理")
        self.setWindowIcon(get_app_qicon())
        self.resize(1180, 820)
        self.setMinimumSize(960, 680)
        self.setStyleSheet(MAIN_STYLESHEET)

        self._build_ui()
        self._bind_events()
        self._load_all_settings_to_ui()

        # 全局安装滚轮事件拦截器，彻底根除所有 QComboBox、QSpinBox、QSlider 的滚轮修改与误触
        from app.ui.no_wheel_filter import install_global_no_wheel_filter
        install_global_no_wheel_filter()

        # 启动定时刷新界面状态（每 1 秒刷新一次倒计时与运行状态）
        self._ui_tick_timer = QTimer(self)
        self._ui_tick_timer.setInterval(1000)
        self._ui_tick_timer.timeout.connect(self._on_ui_tick)
        self._ui_tick_timer.start()

        # 初始化刷新一次预览舞台
        QTimer.singleShot(200, self._initial_refresh_preview)

    def set_tray_manager(self, tray_mgr: TrayManager) -> None:
        self._tray_mgr = tray_mgr

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_v = QVBoxLayout(central)
        root_v.setContentsMargins(0, 0, 0, 0)
        root_v.setSpacing(0)

        # 核心内容区 (左侧 Sidebar + 右侧内容区)
        body_h = QHBoxLayout()
        body_h.setContentsMargins(0, 0, 0, 0)
        body_h.setSpacing(0)

        # 1. 左侧侧边栏 (Sidebar)
        self.sidebar_frame = QFrame()
        self.sidebar_frame.setObjectName("SidebarFrame")
        self.sidebar_frame.setFixedWidth(210)
        side_layout = QVBoxLayout(self.sidebar_frame)
        side_layout.setContentsMargins(10, 16, 10, 14)
        side_layout.setSpacing(10)

        # 顶部品牌标识
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)

        lbl_logo = QLabel()
        logo_pix = get_app_qicon().pixmap(32, 32)
        if not logo_pix.isNull():
            lbl_logo.setPixmap(logo_pix)
        else:
            lbl_logo.setText("GW")
            lbl_logo.setStyleSheet("font-size: 16px; font-weight: 800; color: #ffffff;")

        brand_text_box = QVBoxLayout()
        brand_text_box.setSpacing(1)
        lbl_app_title = QLabel("Gou Wallpaper")
        lbl_app_title.setObjectName("AppHeaderTitle")
        self.lbl_mon_summary = QLabel("检测显示器中...")
        self.lbl_mon_summary.setObjectName("AppHeaderSubtitle")
        brand_text_box.addWidget(lbl_app_title)
        brand_text_box.addWidget(self.lbl_mon_summary)

        brand_row.addWidget(lbl_logo)
        brand_row.addLayout(brand_text_box)
        brand_row.addStretch()
        side_layout.addLayout(brand_row)

        # 导航列表项
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("NavList")

        nav_items = [
            "运行总览",
            "桌面壁纸",
            "静态图库",
            "屏幕保护",
            "定时调度",
            "高级设置",
            "运行日志",
            "关于软件",
        ]
        for name in nav_items:
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(180, 38))
            self.nav_list.addItem(item)

        side_layout.addWidget(self.nav_list, 1)

        # 侧边栏底部版本说明
        lbl_version = QLabel("Gou Wallpaper v1.0.0\nRelease Edition")
        lbl_version.setStyleSheet("color: #52525b; font-size: 11px; padding-left: 6px;")
        side_layout.addWidget(lbl_version)

        body_h.addWidget(self.sidebar_frame)

        # 2. 右侧主体区域 (包含顶部微 Header 与页面栈)
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # 顶部轻量 Header (展示当前页面名与全局保存按钮)
        header_bar = QFrame()
        header_bar.setStyleSheet("background-color: #000000; border-bottom: 1px solid #262626; padding: 6px 20px;")
        header_layout = QHBoxLayout(header_bar)
        header_layout.setContentsMargins(0, 0, 0, 0)

        self.lbl_curr_page_title = QLabel("运行总览")
        self.lbl_curr_page_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        header_layout.addWidget(self.lbl_curr_page_title)

        header_layout.addStretch()

        self.lbl_unsaved = QLabel("配置已同步")
        self.lbl_unsaved.setStyleSheet("color: #a1a1aa; font-size: 11px; margin-right: 8px;")
        header_layout.addWidget(self.lbl_unsaved)

        btn_save_all = QPushButton("保存配置")
        btn_save_all.setObjectName("SecondaryBtn")
        btn_save_all.setToolTip("将当前全部壁纸、调度与偏好设置保存至本地硬盘")
        btn_save_all.clicked.connect(self._save_all_settings_from_ui)
        header_layout.addWidget(btn_save_all)

        right_layout.addWidget(header_bar)

        # 页面栈
        self.page_stack = QStackedWidget()
        self.page_stack.addWidget(self._create_overview_page())   # 0
        self.page_stack.addWidget(self._create_wallpaper_page())  # 1
        self.page_stack.addWidget(self._create_library_page())    # 2
        self.page_stack.addWidget(self._create_screensaver_page())# 3
        self.page_stack.addWidget(self._create_scheduler_page())  # 4
        self.page_stack.addWidget(self._create_advanced_page())   # 5
        self.page_stack.addWidget(self._create_logs_page())       # 6
        self.page_stack.addWidget(self._create_about_page())      # 7

        right_layout.addWidget(self.page_stack, 1)
        body_h.addWidget(right_container, 1)

        root_v.addLayout(body_h, 1)

        # 3. 底部常驻 Action Bar
        self.action_bar = BottomActionBar(self)
        self.action_bar.prevClicked.connect(self._on_action_prev)
        self.action_bar.applyClicked.connect(self._on_action_apply_current)
        self.action_bar.nextClicked.connect(self._on_action_next)
        self.action_bar.togglePauseClicked.connect(self._on_action_toggle_pause)
        self.action_bar.randomClicked.connect(self._on_action_random)
        self.action_bar.screensaverClicked.connect(self._on_action_launch_screensaver)
        root_v.addWidget(self.action_bar)

        # 导航栏点击联动
        self.nav_list.currentRowChanged.connect(self._on_nav_row_changed)
        self.nav_list.setCurrentRow(0)

    def _on_nav_row_changed(self, row: int) -> None:
        self.page_stack.setCurrentIndex(row)
        page_titles = [
            "运行总览",
            "桌面壁纸与多屏策略",
            "静态图片库",
            "Windows 原生屏幕保护",
            "定时调度系统",
            "系统高级设置",
            "运行与诊断日志",
            "关于 WallFlow",
        ]
        if 0 <= row < len(page_titles):
            self.lbl_curr_page_title.setText(page_titles[row])

        # 按需加载与内存释放：仅渲染当前可见标签页，释放非活跃页面的重度视觉资源
        if row == 0:
            if hasattr(self, "overview_preview_stage"):
                self.overview_preview_stage.refresh_preview(force=True)
            if hasattr(self, "wallpaper_preview_stage"):
                self.wallpaper_preview_stage.release_preview_memory()
            if hasattr(self, "gallery_widget"):
                self.gallery_widget.release_gallery_memory()
            clear_image_cache()
            trim_process_memory()
        elif row == 1:
            if hasattr(self, "wallpaper_preview_stage"):
                self.wallpaper_preview_stage.refresh_preview(force=True)
            if hasattr(self, "overview_preview_stage"):
                self.overview_preview_stage.release_preview_memory()
            if hasattr(self, "gallery_widget"):
                self.gallery_widget.release_gallery_memory()
            clear_image_cache()
            trim_process_memory()
        elif row == 2:
            if hasattr(self, "gallery_widget"):
                self.gallery_widget.reload_items(force=True)
            if hasattr(self, "overview_preview_stage"):
                self.overview_preview_stage.release_preview_memory()
            if hasattr(self, "wallpaper_preview_stage"):
                self.wallpaper_preview_stage.release_preview_memory()
            clear_image_cache()
            trim_process_memory()
        else:
            if hasattr(self, "overview_preview_stage"):
                self.overview_preview_stage.release_preview_memory()
            if hasattr(self, "wallpaper_preview_stage"):
                self.wallpaper_preview_stage.release_preview_memory()
            if hasattr(self, "gallery_widget"):
                self.gallery_widget.release_gallery_memory()
            clear_image_cache()
            trim_process_memory()

    # =========================================================================
    # 页面 1：总览 (Overview)
    # =========================================================================
    def _create_overview_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        # 顶部调度模式状态条
        badge_row = QHBoxLayout()
        self.lbl_ov_badge = QLabel("系统时间戳调度 (≥6小时)")
        self.lbl_ov_badge.setObjectName("BadgeTimestamp")
        self.lbl_ov_summary = QLabel("调度器正常运行中")
        self.lbl_ov_summary.setStyleSheet("font-weight: 600; color: #ffffff; font-size: 13px;")
        badge_row.addWidget(self.lbl_ov_badge)
        badge_row.addWidget(self.lbl_ov_summary)
        badge_row.addStretch()
        layout.addLayout(badge_row)

        # 拟真多屏预览舞台
        self.overview_preview_stage = PreviewStageWidget(self._wp_mgr, self._library, w)
        self.overview_preview_stage.applyRequested.connect(self._on_stage_apply_requested)
        layout.addWidget(self.overview_preview_stage)

        # 下方信息卡片
        info_card = QFrame()
        info_card.setObjectName("Card")
        info_layout = QGridLayout(info_card)
        info_layout.setContentsMargins(18, 16, 18, 16)
        info_layout.setSpacing(12)

        def add_info_row(r: int, label_text: str, val_widget: QWidget) -> None:
            lbl = QLabel(label_text)
            lbl.setStyleSheet("color: #a1a1aa; font-size: 12px;")
            info_layout.addWidget(lbl, r, 0)
            info_layout.addWidget(val_widget, r, 1)

        self.lbl_ov_count = QLabel("0 张有效图片")
        add_info_row(0, "图片库容量：", self.lbl_ov_count)

        self.lbl_ov_mode = QLabel("不放回随机轮播")
        add_info_row(1, "播放顺序模式：", self.lbl_ov_mode)

        self.lbl_ov_interval = QLabel("1800 秒")
        add_info_row(2, "切换间隔时间：", self.lbl_ov_interval)

        self.lbl_ov_remaining = QLabel("计算中...")
        self.lbl_ov_remaining.setStyleSheet("font-weight: 700; color: #ffffff;")
        add_info_row(3, "距离下次计划切换：", self.lbl_ov_remaining)

        self.lbl_ov_next_due = QLabel("-")
        add_info_row(4, "下次计划切换时间：", self.lbl_ov_next_due)

        self.lbl_ov_last_success = QLabel("-")
        add_info_row(5, "最近成功切换时间：", self.lbl_ov_last_success)

        self.lbl_ov_failures = QLabel("0 次")
        add_info_row(6, "连续失败次数：", self.lbl_ov_failures)

        self.lbl_ov_error = QLabel("无")
        self.lbl_ov_error.setStyleSheet("color: #fca5a5;")
        self.lbl_ov_error.setWordWrap(True)
        add_info_row(7, "最近异常信息：", self.lbl_ov_error)

        layout.addWidget(info_card)

        # 快捷检查调度按钮
        btn_row = QHBoxLayout()
        btn_check_now = QPushButton("立即检查并切换")
        btn_check_now.setObjectName("SecondaryBtn")
        btn_check_now.clicked.connect(self._on_action_check_now)
        btn_row.addWidget(btn_check_now)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        scroll.setWidget(w)
        return scroll

    # =========================================================================
    # 页面 2：桌面壁纸 (Desktop Wallpaper)
    # =========================================================================
    def _create_wallpaper_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        # 1. 桌面壁纸专属预览舞台 (带拟真显示器与实时联动)
        self.wallpaper_preview_stage = PreviewStageWidget(self._wp_mgr, self._library, w)
        self.wallpaper_preview_stage.applyRequested.connect(self._on_stage_apply_requested)
        layout.addWidget(self.wallpaper_preview_stage)

        # 2. 显示模式与多显示器策略 Card
        mode_card = QFrame()
        mode_card.setObjectName("Card")
        mode_layout = QFormLayout(mode_card)
        mode_layout.setContentsMargins(16, 14, 16, 14)
        mode_layout.setSpacing(12)

        lbl_mode_head = QLabel("壁纸显示模式与多屏策略")
        lbl_mode_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff; padding-bottom: 4px;")
        mode_layout.addRow(lbl_mode_head)

        self.combo_wp_playback = QComboBox()
        self.combo_wp_playback.addItem("不放回随机轮播 (Random)", "random")
        self.combo_wp_playback.addItem("顺序播放 (Sequential)", "sequential")
        self.combo_wp_playback.currentIndexChanged.connect(self._on_wallpaper_config_changed)
        mode_layout.addRow("播放顺序模式：", self.combo_wp_playback)

        self.combo_wp_display = QComboBox()
        self.combo_wp_display.addItem("填充屏幕 (Fill - 保持比例等比裁切填满)", "fill")
        self.combo_wp_display.addItem("适应屏幕 (Fit - 保持比例四周留边)", "fit")
        self.combo_wp_display.addItem("拉伸填满 (Stretch)", "stretch")
        self.combo_wp_display.addItem("居中原图 (Center)", "center")
        self.combo_wp_display.addItem("平铺显示 (Tile)", "tile")
        self.combo_wp_display.addItem("跨显示器全景显示 (Span)", "span")
        self.combo_wp_display.currentIndexChanged.connect(self._on_wallpaper_config_changed)
        mode_layout.addRow("壁纸显示方式：", self.combo_wp_display)

        self.combo_wp_multi = QComboBox()
        self.combo_wp_multi.addItem("不同显示器分别使用独立随机图片", "different")
        self.combo_wp_multi.addItem("所有显示器使用同一张壁纸", "same")
        self.combo_wp_multi.addItem("选用跨显示器全景布局 (Span)", "span")
        self.combo_wp_multi.currentIndexChanged.connect(self._on_wallpaper_config_changed)
        mode_layout.addRow("多显示器策略：", self.combo_wp_multi)

        bg_row = QHBoxLayout()
        self.lbl_wp_bg_color = QLabel("#000000")
        self.lbl_wp_bg_color.setStyleSheet(
            "background-color: #000000; border: 1px solid #3f3f46; border-radius: 4px; padding: 2px 12px; font-family: monospace; color: #ffffff;"
        )
        btn_wp_bg = QPushButton("选择背景颜色")
        btn_wp_bg.setObjectName("SecondaryBtn")
        btn_wp_bg.clicked.connect(self._on_pick_wp_bg_color)
        bg_row.addWidget(self.lbl_wp_bg_color)
        bg_row.addWidget(btn_wp_bg)
        bg_row.addStretch()
        mode_layout.addRow("留边填充背景颜色：", bg_row)

        layout.addWidget(mode_card)

        # 3. 多图拼贴与色彩微调 Card
        fx_card = QFrame()
        fx_card.setObjectName("Card")
        fx_layout = QFormLayout(fx_card)
        fx_layout.setContentsMargins(16, 14, 16, 14)
        fx_layout.setSpacing(12)

        lbl_fx_head = QLabel("网格拼贴与画面微调")
        lbl_fx_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff; padding-bottom: 4px;")
        fx_layout.addRow(lbl_fx_head)

        coll_row = QHBoxLayout()
        self.chk_collage = QCheckBox("开启多图拼贴网格 (Collage)")
        self.chk_collage.toggled.connect(self._on_wallpaper_config_changed)
        self.spin_collage_count = QSpinBox()
        self.spin_collage_count.setRange(2, 8)
        self.spin_collage_count.setValue(2)
        self.spin_collage_count.valueChanged.connect(self._on_wallpaper_config_changed)
        coll_row.addWidget(self.chk_collage)
        coll_row.addWidget(QLabel("每屏拼贴张数："))
        coll_row.addWidget(self.spin_collage_count)
        coll_row.addStretch()
        fx_layout.addRow("网格拼贴模式：", coll_row)

        # 亮度滑块
        self.slider_bright = QSlider(Qt.Orientation.Horizontal)
        self.slider_bright.setRange(-100, 100)
        self.slider_bright.setValue(0)
        self.lbl_bright_val = QLabel("0")
        self.slider_bright.valueChanged.connect(lambda v: self._on_adj_slider_changed(self.lbl_bright_val, v))
        b_row = QHBoxLayout()
        b_row.addWidget(self.slider_bright, 1)
        b_row.addWidget(self.lbl_bright_val)
        fx_layout.addRow("亮度 (-100~+100)：", b_row)

        # 对比度滑块
        self.slider_contrast = QSlider(Qt.Orientation.Horizontal)
        self.slider_contrast.setRange(-100, 100)
        self.slider_contrast.setValue(0)
        self.lbl_contrast_val = QLabel("0")
        self.slider_contrast.valueChanged.connect(lambda v: self._on_adj_slider_changed(self.lbl_contrast_val, v))
        c_row = QHBoxLayout()
        c_row.addWidget(self.slider_contrast, 1)
        c_row.addWidget(self.lbl_contrast_val)
        fx_layout.addRow("对比度 (-100~+100)：", c_row)

        # 饱和度滑块
        self.slider_sat = QSlider(Qt.Orientation.Horizontal)
        self.slider_sat.setRange(-100, 100)
        self.slider_sat.setValue(0)
        self.lbl_sat_val = QLabel("0")
        self.slider_sat.valueChanged.connect(lambda v: self._on_adj_slider_changed(self.lbl_sat_val, v))
        s_row = QHBoxLayout()
        s_row.addWidget(self.slider_sat, 1)
        s_row.addWidget(self.lbl_sat_val)
        fx_layout.addRow("饱和度 (-100~+100)：", s_row)

        # 色温滑块
        self.slider_warmth = QSlider(Qt.Orientation.Horizontal)
        self.slider_warmth.setRange(-100, 100)
        self.slider_warmth.setValue(0)
        self.lbl_warmth_val = QLabel("0")
        self.slider_warmth.valueChanged.connect(lambda v: self._on_adj_slider_changed(self.lbl_warmth_val, v))
        w_row = QHBoxLayout()
        w_row.addWidget(self.slider_warmth, 1)
        w_row.addWidget(self.lbl_warmth_val)
        fx_layout.addRow("色温 (冷色/暖色)：", w_row)

        btn_reset_adj = QPushButton("重置全部色彩微调为 0")
        btn_reset_adj.setObjectName("SecondaryBtn")
        btn_reset_adj.clicked.connect(self._on_reset_adjustments)
        fx_layout.addRow("", btn_reset_adj)

        layout.addWidget(fx_card)

        # 4. 显示器物理拓扑检测 Card
        mon_card = QFrame()
        mon_card.setObjectName("Card")
        mon_layout = QVBoxLayout(mon_card)
        mon_layout.setContentsMargins(16, 14, 16, 14)
        mon_layout.setSpacing(10)

        lbl_mon_head = QLabel("显示器物理拓扑列表")
        lbl_mon_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        mon_layout.addWidget(lbl_mon_head)

        self.lbl_mon_list = QLabel("正在检测显示器拓扑...")
        self.lbl_mon_list.setStyleSheet("color: #a1a1aa; font-size: 12px; line-height: 1.5;")
        mon_layout.addWidget(self.lbl_mon_list)

        btn_refresh_mon = QPushButton("重新检测显示器拓扑")
        btn_refresh_mon.setObjectName("SecondaryBtn")
        btn_refresh_mon.clicked.connect(self._on_refresh_monitors)
        mon_layout.addWidget(btn_refresh_mon)

        layout.addWidget(mon_card)

        # 禁用选项控件的滚轮修改并转发给父级 QScrollArea，防止滚动时误触与页面卡顿
        fwd_wp = NoWheelScrollForwarder(scroll, w)
        for ctrl in [
            self.combo_wp_playback,
            self.combo_wp_display,
            self.combo_wp_multi,
            self.spin_collage_count,
            self.slider_bright,
            self.slider_contrast,
            self.slider_sat,
            self.slider_warmth,
        ]:
            ctrl.installEventFilter(fwd_wp)
            ctrl.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout.addStretch()
        scroll.setWidget(w)
        return scroll

    # =========================================================================
    # 页面 3：静态图片库 (Image Library - 全新卡片画廊)
    # =========================================================================
    def _create_library_page(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 1. 图片来源文件夹 Card
        folder_card = QFrame()
        folder_card.setObjectName("Card")
        folder_layout = QVBoxLayout(folder_card)
        folder_layout.setContentsMargins(16, 14, 16, 14)
        folder_layout.setSpacing(10)

        lbl_f_head = QLabel("图片来源文件夹")
        lbl_f_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        folder_layout.addWidget(lbl_f_head)

        self.tbl_lib_sources = QTableWidget(0, 4)
        self.tbl_lib_sources.setFixedHeight(120)
        self.tbl_lib_sources.setAlternatingRowColors(True)
        self.tbl_lib_sources.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl_lib_sources.setShowGrid(True)
        self.tbl_lib_sources.setHorizontalHeaderLabels(["文件夹路径", "启用状态", "递归子目录", "有效图片数"])
        self.tbl_lib_sources.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl_lib_sources.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_lib_sources.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_lib_sources.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        folder_layout.addWidget(self.tbl_lib_sources)

        src_btn_row = QHBoxLayout()
        btn_add_src = QPushButton("添加图片文件夹")
        btn_add_src.clicked.connect(self._on_add_library_folder)

        btn_remove_src = QPushButton("移除选中文件夹")
        btn_remove_src.setObjectName("SecondaryBtn")
        btn_remove_src.clicked.connect(self._on_remove_library_folder)

        self.btn_scan_now = QPushButton("扫描并刷新图片库")
        self.btn_scan_now.clicked.connect(self._on_start_library_scan)

        src_btn_row.addWidget(btn_add_src)
        src_btn_row.addWidget(btn_remove_src)
        src_btn_row.addWidget(self.btn_scan_now)
        src_btn_row.addStretch()
        folder_layout.addLayout(src_btn_row)

        self.scan_progress_bar = QProgressBar()
        self.scan_progress_bar.setVisible(False)
        self.lbl_scan_status = QLabel("就绪")
        self.lbl_scan_status.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        folder_layout.addWidget(self.scan_progress_bar)
        folder_layout.addWidget(self.lbl_scan_status)

        layout.addWidget(folder_card)

        # 2. 全新现代化卡片网格画廊组件
        self.gallery_widget = ImageGalleryWidget(self._library, w)
        self.gallery_widget.applyRequested.connect(self._on_apply_single_image_from_gallery)
        self.gallery_widget.imageDoubleClicked.connect(self._on_apply_single_image_from_gallery)
        layout.addWidget(self.gallery_widget, 1)

        return w

    # =========================================================================
    # 页面 4：屏幕保护程序 (Screensaver)
    # =========================================================================
    def _create_screensaver_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        sys_box = QFrame()
        sys_box.setObjectName("Card")
        sys_form = QFormLayout(sys_box)
        sys_form.setContentsMargins(16, 14, 16, 14)
        sys_form.setSpacing(12)

        lbl_s_head = QLabel("Windows 系统原生屏保集成状态")
        lbl_s_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff; padding-bottom: 4px;")
        sys_form.addRow(lbl_s_head)

        self.lbl_ss_reg_file = QLabel("未知")
        self.lbl_ss_reg_active = QLabel("未知")
        self.lbl_ss_reg_timeout = QLabel("未知")
        sys_form.addRow("系统登记屏保文件：", self.lbl_ss_reg_file)
        sys_form.addRow("屏保功能是否启用：", self.lbl_ss_reg_active)
        sys_form.addRow("空闲启动等待时间：", self.lbl_ss_reg_timeout)

        btn_sys_row = QHBoxLayout()
        btn_open_cpl = QPushButton("打开系统屏保面板")
        btn_open_cpl.setObjectName("SecondaryBtn")
        btn_open_cpl.clicked.connect(open_windows_screensaver_settings)

        btn_reg_scr = QPushButton("注册 GouWallpaperSaver.scr 为系统屏保")
        btn_reg_scr.clicked.connect(self._on_register_screensaver)

        btn_sys_row.addWidget(btn_open_cpl)
        btn_sys_row.addWidget(btn_reg_scr)
        btn_sys_row.addStretch()
        sys_form.addRow("", btn_sys_row)
        layout.addWidget(sys_box)

        # 自动空闲屏保看门狗 Card
        watch_box = QFrame()
        watch_box.setObjectName("Card")
        watch_layout = QVBoxLayout(watch_box)
        watch_layout.setContentsMargins(16, 14, 16, 14)
        watch_layout.setSpacing(10)

        lbl_w_head = QLabel("Gou Wallpaper 自动屏保看门狗")
        lbl_w_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        watch_layout.addWidget(lbl_w_head)

        lbl_w_desc = QLabel(
            "当检测到系统空闲且无键盘鼠标输入达到设定时间时，自动无缝启动全屏屏保；移动鼠标或按下键盘瞬间退出。\n"
            "即使系统未注册 .scr 屏保或受 Windows 权限限制，看门狗仍可 100% 毫秒级精准守护。"
        )
        lbl_w_desc.setStyleSheet("color: #a1a1aa; font-size: 12px; line-height: 1.6;")
        watch_layout.addWidget(lbl_w_desc)

        self.chk_ss_idle_enabled = QCheckBox("启用系统空闲无操作时自动启动全屏屏保")
        self.chk_ss_idle_enabled.toggled.connect(self._on_screensaver_idle_config_changed)
        watch_layout.addWidget(self.chk_ss_idle_enabled)

        self.chk_ss_suppress_media = QCheckBox("系统级智能免打扰：播放音频/视频 (如抖音、音乐、电影) 时自动阻止屏保")
        self.chk_ss_suppress_media.toggled.connect(self._on_screensaver_idle_config_changed)
        watch_layout.addWidget(self.chk_ss_suppress_media)

        self.chk_ss_suppress_fullscreen = QCheckBox("系统级智能免打扰：运行全屏应用 (如全屏游戏、全屏播放器、PPT演示) 时自动阻止屏保")
        self.chk_ss_suppress_fullscreen.toggled.connect(self._on_screensaver_idle_config_changed)
        watch_layout.addWidget(self.chk_ss_suppress_fullscreen)

        time_row = QHBoxLayout()
        self.spin_ss_idle_timeout = QSpinBox()
        self.spin_ss_idle_timeout.setRange(10, 86400)
        self.spin_ss_idle_timeout.setSuffix(" 秒")
        self.spin_ss_idle_timeout.valueChanged.connect(self._on_screensaver_idle_config_changed)

        btn_t1 = QPushButton("1 分钟 (60s)")
        btn_t1.setObjectName("SecondaryBtn")
        btn_t1.clicked.connect(lambda: self.spin_ss_idle_timeout.setValue(60))

        btn_t2 = QPushButton("2 分钟 (120s)")
        btn_t2.setObjectName("SecondaryBtn")
        btn_t2.clicked.connect(lambda: self.spin_ss_idle_timeout.setValue(120))

        btn_t5 = QPushButton("5 分钟 (300s)")
        btn_t5.setObjectName("SecondaryBtn")
        btn_t5.clicked.connect(lambda: self.spin_ss_idle_timeout.setValue(300))

        btn_sync_sys = QPushButton("从 Windows 系统设置同步")
        btn_sync_sys.setObjectName("SecondaryBtn")
        btn_sync_sys.clicked.connect(self._on_sync_screensaver_from_windows)

        time_row.addWidget(QLabel("空闲等待时间："))
        time_row.addWidget(self.spin_ss_idle_timeout)
        time_row.addWidget(btn_t1)
        time_row.addWidget(btn_t2)
        time_row.addWidget(btn_t5)
        time_row.addWidget(btn_sync_sys)
        time_row.addStretch()
        watch_layout.addLayout(time_row)

        # 屏保运行时的电源与休眠行为配置
        self.chk_ss_prevent_sleep = QCheckBox("屏保运行时阻止系统休眠与熄屏 (取消勾选则允许 Windows 电源策略自然休眠)")
        self.chk_ss_prevent_sleep.toggled.connect(self._on_screensaver_idle_config_changed)
        watch_layout.addWidget(self.chk_ss_prevent_sleep)

        sleep_row = QHBoxLayout()
        sleep_row.addWidget(QLabel("屏保定时睡眠："))
        self.combo_ss_timed_sleep = QComboBox()
        self.combo_ss_timed_sleep.addItem("从不 (一直保持屏保轮播)", 0)
        self.combo_ss_timed_sleep.addItem("运行 10 分钟后自动睡眠", 10)
        self.combo_ss_timed_sleep.addItem("运行 15 分钟后自动睡眠", 15)
        self.combo_ss_timed_sleep.addItem("运行 30 分钟后自动睡眠 (推荐)", 30)
        self.combo_ss_timed_sleep.addItem("运行 1 小时后自动睡眠", 60)
        self.combo_ss_timed_sleep.addItem("运行 2 小时后自动睡眠", 120)
        self.combo_ss_timed_sleep.currentIndexChanged.connect(self._on_screensaver_idle_config_changed)
        sleep_row.addWidget(self.combo_ss_timed_sleep)

        self.combo_ss_sleep_action = QComboBox()
        self.combo_ss_sleep_action.addItem("动作：系统睡眠 (推荐)", "sleep")
        self.combo_ss_sleep_action.addItem("动作：关闭显示器 (息屏省电)", "turn_off_screen")
        self.combo_ss_sleep_action.addItem("动作：仅解除阻止 (由 Windows 接管)", "allow_sleep")
        self.combo_ss_sleep_action.currentIndexChanged.connect(self._on_screensaver_idle_config_changed)
        sleep_row.addWidget(self.combo_ss_sleep_action)
        sleep_row.addStretch()
        watch_layout.addLayout(sleep_row)

        self.lbl_ss_idle_live = QLabel("实时空闲状态：检测中...")
        self.lbl_ss_idle_live.setStyleSheet("color: #71717a; font-size: 12px; font-weight: 500;")
        watch_layout.addWidget(self.lbl_ss_idle_live)

        layout.addWidget(watch_box)

        # 屏保图片来源与独立图库配置 Card
        src_box = QFrame()
        src_box.setObjectName("Card")
        src_layout = QVBoxLayout(src_box)
        src_layout.setContentsMargins(16, 14, 16, 14)
        src_layout.setSpacing(10)

        lbl_src_head = QLabel("屏保图片来源与独立图片库")
        lbl_src_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        src_layout.addWidget(lbl_src_head)

        self.chk_ss_use_wp_lib = QCheckBox("直接共用桌面壁纸的图片库文件夹（推荐）")
        self.chk_ss_use_wp_lib.toggled.connect(self._on_screensaver_source_mode_changed)
        src_layout.addWidget(self.chk_ss_use_wp_lib)

        self.lbl_ss_src_status = QLabel("当前来源状态：检测中...")
        self.lbl_ss_src_status.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        src_layout.addWidget(self.lbl_ss_src_status)

        btn_src_row = QHBoxLayout()
        btn_manage_folders = QPushButton("配置与管理独立屏保图片文件夹...")
        btn_manage_folders.setObjectName("SecondaryBtn")
        btn_manage_folders.clicked.connect(self._on_open_screensaver_config_dialog)
        btn_src_row.addWidget(btn_manage_folders)
        btn_src_row.addStretch()
        src_layout.addLayout(btn_src_row)

        layout.addWidget(src_box)

        # 屏保时钟外观配置 Card
        clock_box = QFrame()
        clock_box.setObjectName("Card")
        clock_form = QFormLayout(clock_box)
        clock_form.setContentsMargins(16, 14, 16, 14)
        clock_form.setSpacing(12)

        lbl_c_head = QLabel("屏保数字时钟设置")
        lbl_c_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        clock_form.addRow(lbl_c_head)

        self.chk_ss_clock = QCheckBox("在全屏屏保中显示极简数字时钟与日期")
        self.chk_ss_clock.toggled.connect(self._on_screensaver_clock_config_changed)
        clock_form.addRow("", self.chk_ss_clock)

        self.combo_ss_clock_size = QComboBox()
        self.combo_ss_clock_size.addItem("小 (Small)", "small")
        self.combo_ss_clock_size.addItem("中等 (Medium - 默认)", "medium")
        self.combo_ss_clock_size.addItem("大 (Large)", "large")
        self.combo_ss_clock_size.addItem("特大 (Extra Large)", "xlarge")
        self.combo_ss_clock_size.currentIndexChanged.connect(self._on_screensaver_clock_config_changed)
        clock_form.addRow("时钟字号大小：", self.combo_ss_clock_size)

        self.combo_ss_clock_pos = QComboBox()
        self.combo_ss_clock_pos.addItem("左下角 (Bottom Left - 默认)", "bottom_left")
        self.combo_ss_clock_pos.addItem("居中偏上 (Center Top - 锁屏风)", "center_top")
        self.combo_ss_clock_pos.addItem("居中底部 (Center Bottom)", "center_bottom")
        self.combo_ss_clock_pos.currentIndexChanged.connect(self._on_screensaver_clock_config_changed)
        clock_form.addRow("时钟摆放位置：", self.combo_ss_clock_pos)

        layout.addWidget(clock_box)

        cfg_box = QFrame()
        cfg_box.setObjectName("Card")
        cfg_layout = QVBoxLayout(cfg_box)
        cfg_layout.setContentsMargins(16, 14, 16, 14)
        cfg_layout.setSpacing(12)

        lbl_cmd_head = QLabel("屏保命令行模式与测试")
        lbl_cmd_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        cfg_layout.addWidget(lbl_cmd_head)

        cfg_desc = QLabel(
            "提示：Gou Wallpaper 原生屏幕保护程序完全兼容 Windows 原生 `.scr` 命令行模式：\n"
            "  • /s ：启动无边框全屏屏保 (任意键盘鼠标动作退出)\n"
            "  • /c ：打开独立屏保设置配置对话框\n"
            "  • /p <hwnd> ：嵌入 Windows 屏保设置小预览窗"
        )
        cfg_desc.setStyleSheet("color: #a1a1aa; font-size: 12px; line-height: 1.6;")
        cfg_layout.addWidget(cfg_desc)

        action_row = QHBoxLayout()
        btn_open_dialog = QPushButton("打开独立屏保设置对话框")
        btn_open_dialog.setObjectName("SecondaryBtn")
        btn_open_dialog.clicked.connect(self._on_open_screensaver_config_dialog)

        btn_test_fs = QPushButton("全屏测试屏保")
        btn_test_fs.clicked.connect(lambda: self._on_action_launch_screensaver(force=True))

        action_row.addWidget(btn_open_dialog)
        action_row.addWidget(btn_test_fs)
        action_row.addStretch()
        cfg_layout.addLayout(action_row)

        layout.addWidget(cfg_box)
        layout.addStretch()

        # 禁用屏保页面所有选项与微调框的滚轮修改并转发给父级 QScrollArea
        fwd_ss = NoWheelScrollForwarder(scroll, w)
        for ctrl in [
            self.spin_ss_idle_timeout,
            self.combo_ss_clock_size,
            self.combo_ss_clock_pos,
        ]:
            ctrl.installEventFilter(fwd_ss)

        scroll.setWidget(w)
        return scroll

    # =========================================================================
    # 页面 5：定时调度 (Scheduler)
    # =========================================================================
    def _create_scheduler_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        rule_box = QFrame()
        rule_box.setObjectName("Card")
        rule_layout = QVBoxLayout(rule_box)
        rule_layout.setContentsMargins(16, 14, 16, 14)
        rule_layout.setSpacing(8)

        lbl_r_head = QLabel("壁纸智能轮播调度机制说明")
        lbl_r_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        rule_layout.addWidget(lbl_r_head)

        lbl_rules = QLabel(
            "• 短间隔轮播模式（< 6 小时）：采用高精度单调时钟调度，毫秒级精准响应，电脑休眠唤醒后自动核算时差补偿切换。\n"
            "• 长周期轮播模式（≥ 6 小时）：采用高可靠 UTC 时间戳跨周期调度，完美支持 6小时、12小时、每天一图、每周一图。\n"
            "  即使关机、重启电脑或长时间休眠，开机自启后均能无缝承接上次进度并智能校准，绝不丢失进度，绝无多余闪屏刷图。"
        )
        lbl_rules.setStyleSheet("color: #a1a1aa; font-size: 12px; line-height: 1.6;")
        rule_layout.addWidget(lbl_rules)
        layout.addWidget(rule_box)

        # 预设间隔快捷选择 Card
        interval_box = QFrame()
        interval_box.setObjectName("Card")
        int_layout = QVBoxLayout(interval_box)
        int_layout.setContentsMargins(16, 14, 16, 14)
        int_layout.setSpacing(10)

        lbl_int_head = QLabel("快捷选择切换间隔")
        lbl_int_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        int_layout.addWidget(lbl_int_head)

        preset_row1 = QHBoxLayout()
        preset_buttons = [
            ("1 分钟", 60),
            ("5 分钟", 300),
            ("15 分钟", 900),
            ("30 分钟", 1800),
            ("1 小时", 3600),
            ("3 小时", 10800),
        ]
        for name, secs in preset_buttons:
            btn = QPushButton(name)
            btn.setObjectName("SecondaryBtn")
            btn.clicked.connect(lambda _, s=secs: self._set_scheduler_interval(s))
            preset_row1.addWidget(btn)
        int_layout.addLayout(preset_row1)

        preset_row2 = QHBoxLayout()
        long_preset_buttons = [
            ("5 小时 59 分 (短间隔边界)", 21540),
            ("6 小时 (系统时间戳调度)", 21600),
            ("12 小时", 43200),
            ("24 小时 (每日一图)", 86400),
            ("3 天", 259200),
            ("1 周 (7 天)", 604800),
        ]
        for name, secs in long_preset_buttons:
            btn = QPushButton(name)
            btn.setObjectName("SecondaryBtn")
            btn.clicked.connect(lambda _, s=secs: self._set_scheduler_interval(s))
            preset_row2.addWidget(btn)
        int_layout.addLayout(preset_row2)

        custom_row = QHBoxLayout()
        self.spin_hours = QSpinBox()
        self.spin_hours.setRange(0, 720)
        self.spin_hours.setSuffix(" 小时")
        self.spin_mins = QSpinBox()
        self.spin_mins.setRange(0, 59)
        self.spin_mins.setSuffix(" 分钟")
        self.spin_secs = QSpinBox()
        self.spin_secs.setRange(0, 59)
        self.spin_secs.setSuffix(" 秒")

        btn_apply_custom_time = QPushButton("应用自定义时长")
        btn_apply_custom_time.clicked.connect(self._on_apply_custom_interval)

        custom_row.addWidget(QLabel("自定义切换间隔："))
        custom_row.addWidget(self.spin_hours)
        custom_row.addWidget(self.spin_mins)
        custom_row.addWidget(self.spin_secs)
        custom_row.addWidget(btn_apply_custom_time)
        custom_row.addStretch()
        int_layout.addLayout(custom_row)

        layout.addWidget(interval_box)

        # 禁用时间微调框的滚轮修改并转发给父级 QScrollArea
        fwd_sched = NoWheelScrollForwarder(scroll, w)
        for spin in [self.spin_hours, self.spin_mins, self.spin_secs]:
            spin.installEventFilter(fwd_sched)
            spin.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout.addStretch()
        scroll.setWidget(w)
        return scroll

    # =========================================================================
    # 页面 6：高级设置 (Advanced)
    # =========================================================================
    def _create_advanced_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        # 开机自启与托盘行为 Card
        sys_card = QFrame()
        sys_card.setObjectName("Card")
        sys_layout = QVBoxLayout(sys_card)
        sys_layout.setContentsMargins(16, 14, 16, 14)
        sys_layout.setSpacing(10)

        lbl_adv_head = QLabel("系统托盘与开机自启动")
        lbl_adv_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        sys_layout.addWidget(lbl_adv_head)

        self.chk_startup = QCheckBox("随 Windows 登录自动启动 (当前用户级别注册表，无需管理员权限)")
        self.chk_startup.toggled.connect(self._on_toggle_startup)
        sys_layout.addWidget(self.chk_startup)

        self.chk_start_min = QCheckBox("开机自启后以后台静默方式运行 (不自动弹出主窗口)")
        self.chk_start_min.toggled.connect(self._on_system_config_changed)
        sys_layout.addWidget(self.chk_start_min)

        self.chk_close_to_tray = QCheckBox("点击窗口关闭按钮 [X] 时最小化到托盘，而非退出程序")
        self.chk_close_to_tray.toggled.connect(self._on_system_config_changed)
        sys_layout.addWidget(self.chk_close_to_tray)

        layout.addWidget(sys_card)

        # 全局快捷键 Card
        hk_card = QFrame()
        hk_card.setObjectName("Card")
        hk_layout = QFormLayout(hk_card)
        hk_layout.setContentsMargins(16, 14, 16, 14)
        hk_layout.setSpacing(10)

        lbl_hk_head = QLabel("全局快捷键绑定 (Windows API 级响应)")
        lbl_hk_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        hk_layout.addRow(lbl_hk_head)

        self.chk_hk_enabled = QCheckBox("启用全局热键功能")
        self.chk_hk_enabled.toggled.connect(self._on_hotkey_config_changed)
        hk_layout.addRow("功能开关：", self.chk_hk_enabled)

        self.edit_hk_next = QLineEdit("Ctrl+Alt+Right")
        self.edit_hk_next.textChanged.connect(self._on_hotkey_config_changed)
        hk_layout.addRow("下一张壁纸：", self.edit_hk_next)

        self.edit_hk_prev = QLineEdit("Ctrl+Alt+Left")
        self.edit_hk_prev.textChanged.connect(self._on_hotkey_config_changed)
        hk_layout.addRow("上一张壁纸：", self.edit_hk_prev)

        self.edit_hk_random = QLineEdit("Ctrl+Alt+R")
        self.edit_hk_random.textChanged.connect(self._on_hotkey_config_changed)
        hk_layout.addRow("立即随机切换：", self.edit_hk_random)

        self.edit_hk_pause = QLineEdit("Ctrl+Alt+S")
        self.edit_hk_pause.textChanged.connect(self._on_hotkey_config_changed)
        hk_layout.addRow("暂停/继续轮播：", self.edit_hk_pause)

        self.edit_hk_icons = QLineEdit("Ctrl+Alt+H")
        self.edit_hk_icons.textChanged.connect(self._on_hotkey_config_changed)
        hk_layout.addRow("隐藏/显示桌面图标：", self.edit_hk_icons)

        layout.addWidget(hk_card)

        # 缓存管理 Card
        cache_card = QFrame()
        cache_card.setObjectName("Card")
        cache_layout = QHBoxLayout(cache_card)
        cache_layout.setContentsMargins(16, 14, 16, 14)

        lbl_cache_desc = QLabel("清理多屏合成壁纸生成的临时位图缓存文件")
        lbl_cache_desc.setStyleSheet("color: #94a3b8; font-size: 12px;")
        btn_clear_cache = QPushButton("立即清理渲染缓存")
        btn_clear_cache.setObjectName("SecondaryBtn")
        btn_clear_cache.clicked.connect(self._on_clear_render_cache)

        cache_layout.addWidget(lbl_cache_desc)
        cache_layout.addStretch()
        cache_layout.addWidget(btn_clear_cache)
        layout.addWidget(cache_card)

        layout.addStretch()
        scroll.setWidget(w)
        return scroll

    # =========================================================================
    # 页面 7：日志与诊断 (Logs & Diagnostics)
    # =========================================================================
    def _create_logs_page(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        top_row = QHBoxLayout()
        lbl_log_head = QLabel("实时系统运行日志 (环形内存记录)")
        lbl_log_head.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        top_row.addWidget(lbl_log_head)
        top_row.addStretch()

        btn_copy_logs = QPushButton("复制全部日志")
        btn_copy_logs.setObjectName("SecondaryBtn")
        btn_copy_logs.clicked.connect(self._on_copy_logs)

        btn_clear_logs = QPushButton("清空显示")
        btn_clear_logs.setObjectName("SecondaryBtn")
        btn_clear_logs.clicked.connect(self._on_clear_logs)

        btn_open_folder = QPushButton("打开日志目录")
        btn_open_folder.setObjectName("SecondaryBtn")
        btn_open_folder.clicked.connect(self._on_open_log_folder)

        top_row.addWidget(btn_copy_logs)
        top_row.addWidget(btn_clear_logs)
        top_row.addWidget(btn_open_folder)
        layout.addLayout(top_row)

        self.txt_logs = QPlainTextEdit()
        self.txt_logs.setReadOnly(True)
        layout.addWidget(self.txt_logs, 1)

        # 跳过文件表格
        lbl_skip = QLabel("扫描跳过/无法解析的文件列表：")
        lbl_skip.setStyleSheet("font-size: 12px; font-weight: 600; color: #a1a1aa;")
        layout.addWidget(lbl_skip)

        self.tbl_skipped = QTableWidget(0, 3)
        self.tbl_skipped.setFixedHeight(120)
        self.tbl_skipped.setHorizontalHeaderLabels(["文件路径", "跳过原因", "检测类型"])
        self.tbl_skipped.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tbl_skipped.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_skipped.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.tbl_skipped)

        return w

    # =========================================================================
    # 页面 8：关于 (About)
    # =========================================================================
    def _create_about_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("background: transparent; border: none;")

        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        card = QFrame()
        card.setObjectName("Card")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(24, 20, 24, 20)
        c_layout.setSpacing(12)

        lbl_title = QLabel("WallFlow —— 专业的 Windows 桌面壁纸与原生屏幕保护程序")
        lbl_title.setStyleSheet("font-size: 16px; font-weight: 700; color: #ffffff;")
        c_layout.addWidget(lbl_title)

        lbl_desc = QLabel(
            "版本：v1.0.0 正式版 (Release)\n"
            "运行环境：Windows 10 / Windows 11 (64 位)\n\n"
            "【产品核心优势与功能说明】\n"
            "  • 超轻量与极低内存占用：针对 4K/8K 高清原图深度优化，后台运行时自动缩减物理工作集，轻量流畅无负担。\n"
            "  • 双模式可靠轮播调度：短间隔精准毫秒切换，长周期（如每日一图）跨休眠与重启不丢失进度。\n"
            "  • 原生多显示器支持：支持多屏独立壁纸、跨屏全景 (Span)、等比填充/居中以及多图网格拼贴。\n"
            "  • 原生全屏屏幕保护程序：无缝兼容 Windows 原生 .scr 规范，自带极简数字时钟与淡入/平移/微距缩放动效。\n"
            "  • 系统级智能防打扰：智能检测 Core Audio 音频播放与全屏应用，刷抖音、看影视作品或玩游戏时绝不误弹屏保。\n"
            "  • 智能电源管理：支持屏保常亮防息屏，并提供运行 10/15/30 分钟后自动进入系统睡眠或关闭显示器。\n"
            "  • 纯离线与零遥测：所有配置与图片均在本地处理，不上传任何隐私数据，绝不破坏修改原始图片文件。"
        )
        lbl_desc.setStyleSheet("color: #cbd5e1; font-size: 13px; line-height: 1.7;")
        c_layout.addWidget(lbl_desc)

        layout.addWidget(card)
        layout.addStretch()
        scroll.setWidget(w)
        return scroll

    # =========================================================================
    # 状态刷新与业务联动
    # =========================================================================
    def _initial_refresh_preview(self) -> None:
        self._on_refresh_monitors()
        if not self.isVisible():
            return
        cur_row = self.nav_list.currentRow() if hasattr(self, "nav_list") else 0
        if cur_row == 0:
            self.overview_preview_stage.refresh_preview()
        elif cur_row == 1:
            self.wallpaper_preview_stage.refresh_preview()
        elif cur_row == 2:
            self.gallery_widget.reload_items()

    def _on_ui_tick(self) -> None:
        """每秒刷新一次调度状态、空闲屏保看门狗与界面文字."""
        # 若处于未暂停、启用了自动切换且倒计时已到，主动唤醒执行切换，保证倒计时结束立即下一张
        snapshot = self._scheduler.get_snapshot()
        if snapshot.is_enabled and not snapshot.is_paused:
            if snapshot.remaining_seconds <= 0 and not snapshot.is_switching:
                self._scheduler.check_now(reason="ui_tick_due")
                snapshot = self._scheduler.get_snapshot()

        # 空闲屏保智能看门狗：当用户在规定空闲时间无操作且未启动屏保时，自动启动全屏屏保
        try:
            ss_cfg = self._settings.config.screensaver
            idle_enabled = getattr(ss_cfg, "idle_enabled", True)

            # 优先结合 Windows 屏保设置的超时（若系统设置了 60 秒等）
            sys_info = get_system_screensaver_status()
            sys_timeout = 0
            if sys_info.get("active") == "是":
                try:
                    sys_timeout = int(sys_info.get("timeout_seconds", "0"))
                except ValueError:
                    sys_timeout = 0

            if sys_timeout >= 10:
                idle_timeout = sys_timeout
            else:
                idle_timeout = max(10, int(getattr(ss_cfg, "timeout_seconds", 60)))

            sys_idle_sec = get_system_idle_seconds()

            # 系统级别智能状态识别：若检测到正在播放音频/视频 (如抖音/影视)、全屏应用或专注模式，智能阻止屏保
            suppress_m = getattr(ss_cfg, "suppress_when_media_playing", True)
            suppress_f = getattr(ss_cfg, "suppress_when_fullscreen", True)
            busy_state = check_system_media_and_busy_state(
                suppress_media=suppress_m,
                suppress_fullscreen=suppress_f,
            )

            # 核心防闪黑机制：
            # 若未处于全屏屏保中，当检测到多媒体音频播放或全屏应用时，
            # 主动通过 SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED | ES_SYSTEM_REQUIRED)
            # 通知 Windows 保持显示常亮并重置 Windows 原生空闲计时器；
            # 彻底杜绝 Windows 自身空闲超时后切屏拉起 .scr 又瞬间退出导致的“黑屏一瞬间”！
            is_ss_running = (
                self._screensaver_preview_ctrl is not None
                and self._screensaver_preview_ctrl.is_running
            )
            if not is_ss_running:
                if busy_state.is_busy:
                    if not getattr(self, "_system_keep_awake_active", False):
                        set_thread_display_required(True)
                        self._system_keep_awake_active = True
                        logger.info("系统多媒体/音频播放中，已向 Windows 申请保持显示常亮并重置系统屏保计时器 (%s)", busy_state.reason)
                else:
                    if getattr(self, "_system_keep_awake_active", False):
                        set_thread_display_required(False)
                        self._system_keep_awake_active = False
                        logger.info("多媒体/音频播放已结束且宽限期已过，已恢复 Windows 正常系统屏保与电源管理")

            if hasattr(self, "lbl_ss_idle_live") and self.isVisible():
                if not idle_enabled:
                    self.lbl_ss_idle_live.setText("实时空闲状态：自动屏保看门狗已禁用")
                elif busy_state.is_busy:
                    self.lbl_ss_idle_live.setText(
                        f"实时空闲状态：系统级忙碌保护中 ({busy_state.reason})，屏保已暂停等待"
                    )
                else:
                    rem = max(0, idle_timeout - int(sys_idle_sec))
                    if rem > 0:
                        self.lbl_ss_idle_live.setText(
                            f"实时空闲状态：已无操作空闲 {int(sys_idle_sec)} 秒 / 阈值 {idle_timeout} 秒 (倒计时 {rem} 秒后启动屏保)"
                        )
                    else:
                        self.lbl_ss_idle_live.setText(
                            f"实时空闲状态：已无操作空闲 {int(sys_idle_sec)} 秒 / 达到阈值 {idle_timeout} 秒，正在唤起全屏屏保..."
                        )

            now_mono = time.monotonic()
            cooldown_ok = (now_mono - getattr(self, "_last_screensaver_exit_mono", 0.0)) > 3.0

            if idle_enabled and not busy_state.is_busy and cooldown_ok and sys_idle_sec >= idle_timeout:
                if (
                    (self._screensaver_preview_ctrl is None or not self._screensaver_preview_ctrl.is_running)
                    and not ScreensaverMutexGuard.is_active()
                ):
                    logger.info("系统空闲已达到 %0.1fs (设定 %ds) 且系统处于空闲态，自动启动全屏屏保", sys_idle_sec, idle_timeout)
                    self._on_action_launch_screensaver()
        except Exception as exc:
            logger.warning("空闲屏保检测异常: %s", exc)

        # 核心内存优化：如果窗口处于后台或最小化不可见状态，跳过所有非必要控件重绘、日志抓取与富文本拼接
        if not self.isVisible():
            return

        # 更新底部 Action Bar
        is_ts = snapshot.mode == "timestamp"
        mode_text = "系统时间戳调度 (≥6小时)" if is_ts else "单调时钟调度 (<6小时)"
        self.action_bar.update_status(
            mode_text=mode_text,
            is_timestamp_mode=is_ts,
            countdown_text=snapshot.remaining_formatted,
            image_count=self._library.total_valid_count,
        )
        self.action_bar.set_paused_state(snapshot.is_paused)

        # 更新总览页
        self.lbl_ov_badge.setText(mode_text)
        self.lbl_ov_badge.setObjectName("BadgeTimestamp" if is_ts else "BadgeMonotonic")
        self.lbl_ov_badge.setStyle(self.lbl_ov_badge.style())
        self.lbl_ov_summary.setText("轮播已暂停" if snapshot.is_paused else ("正在应用壁纸..." if snapshot.is_switching else "调度器正常运行中"))

        self.lbl_ov_count.setText(f"{self._library.total_valid_count} 张有效图片")
        self.lbl_ov_interval.setText(f"{snapshot.interval_seconds} 秒")
        self.lbl_ov_remaining.setText(snapshot.remaining_formatted)
        self.lbl_ov_next_due.setText(snapshot.next_due_formatted)
        self.lbl_ov_last_success.setText(snapshot.last_success_formatted)
        self.lbl_ov_failures.setText(f"{snapshot.failure_count} 次")
        self.lbl_ov_error.setText(snapshot.last_error or "无")

        # 刷新日志查看器
        logs = get_memory_handler().get_logs()
        if len(logs) != len(self.txt_logs.toPlainText()):
            self.txt_logs.setPlainText(logs)
            self.txt_logs.verticalScrollBar().setValue(self.txt_logs.verticalScrollBar().maximum())

    def _notify_status(self, message: str, timeout_ms: int = 3000) -> None:
        """非阻塞式状态提示，更新底部状态栏并记录日志，杜绝模态弹窗打扰用户操作."""
        try:
            self.statusBar().showMessage(message, timeout_ms)
        except Exception:
            pass
        logger.info(message)

    def _on_stage_apply_requested(self, paths: list[str]) -> None:
        """在预览舞台点击了“应用到桌面”."""
        res = self._wp_mgr.apply_specific_images(paths)
        if res.success:
            self._scheduler.record_manual_switch_success()
            self._notify_status(f"壁纸已成功应用到桌面（共 {len(paths)} 张图片）")
            self.overview_preview_stage.refresh_preview()
            self.wallpaper_preview_stage.refresh_preview()
            self._on_ui_tick()
        else:
            self._notify_status(f"设置壁纸失败: {res.error_message}", 5000)

    def _on_apply_single_image_from_gallery(self, path: str) -> None:
        """从画廊中点击或双击了某张图片."""
        res = self._wp_mgr.apply_specific_images([path])
        if res.success:
            self._scheduler.record_manual_switch_success()
            self._notify_status(f"壁纸已成功设为：{Path(path).name}")
            self.overview_preview_stage.refresh_preview(override_paths=[path])
            self.wallpaper_preview_stage.refresh_preview(override_paths=[path])
            self._on_ui_tick()
        else:
            self._notify_status(f"设置壁纸失败: {res.error_message}", 5000)

    def _on_adj_slider_changed(self, label: QLabel, value: int) -> None:
        label.setText(str(value))
        if not self._is_loading_settings:
            self._save_adjustments_from_ui()
            # 实时联动预览舞台重绘！
            self.wallpaper_preview_stage.refresh_preview()

    def _on_reset_adjustments(self) -> None:
        self.slider_bright.setValue(0)
        self.slider_contrast.setValue(0)
        self.slider_sat.setValue(0)
        self.slider_warmth.setValue(0)
        self._save_adjustments_from_ui()
        self.wallpaper_preview_stage.refresh_preview()

    def _save_adjustments_from_ui(self) -> None:
        adj = ImageAdjustmentConfig(
            brightness=self.slider_bright.value(),
            contrast=self.slider_contrast.value(),
            saturation=self.slider_sat.value(),
            warmth=self.slider_warmth.value(),
        )
        self._settings.config.wallpaper.adjustments = adj
        self._settings.save()

    def _on_wallpaper_config_changed(self) -> None:
        if self._is_loading_settings:
            return
        playback = self.combo_wp_playback.currentData() or "random"
        disp = self.combo_wp_display.currentData() or "fill"
        multi = self.combo_wp_multi.currentData() or "different"
        collage = self.chk_collage.isChecked()
        c_count = self.spin_collage_count.value()

        wp = self._settings.config.wallpaper
        wp.playback_mode = playback
        wp.display_mode = disp
        wp.multi_monitor_mode = multi
        wp.collage_enabled = collage
        wp.collage_count = c_count
        self._settings.save()

        self._selector.set_mode(playback)
        self.wallpaper_preview_stage.refresh_preview()
        self.overview_preview_stage.refresh_preview()

    def _on_pick_wp_bg_color(self) -> None:
        cur_hex = self.lbl_wp_bg_color.text()
        col = QColorDialog.getColor(QColor(cur_hex), self, "选择壁纸边框背景颜色")
        if col.isValid():
            new_hex = col.name()
            self.lbl_wp_bg_color.setText(new_hex)
            self.lbl_wp_bg_color.setStyleSheet(
                f"background-color: {new_hex}; border: 1px solid #334155; border-radius: 4px; padding: 2px 12px; font-family: monospace;"
            )
            self._settings.config.wallpaper.background_color = new_hex
            self._settings.save()
            self.wallpaper_preview_stage.refresh_preview()

    def _on_refresh_monitors(self) -> None:
        monitors = self._wp_mgr.refresh_monitors()
        _, _, vw, vh = compute_virtual_desktop_bounds(monitors)
        self.lbl_mon_summary.setText(f"{len(monitors)} 显示器 · {vw}×{vh}")

        lines = []
        for i, m in enumerate(monitors):
            pri = " (主显示器)" if m.is_primary else ""
            lines.append(f"• 显示器 #{i + 1}: {m.width}×{m.height} @ ({m.x}, {m.y}) [{m.device_name}]{pri}")
        self.lbl_mon_list.setText("\n".join(lines) if lines else "未识别到显示器")
        self.wallpaper_preview_stage.refresh_preview()
        self.overview_preview_stage.refresh_preview()

    # 快捷动作
    def _on_action_prev(self) -> None:
        res = self._wp_mgr.apply_previous()
        if res.success:
            self._scheduler.record_manual_switch_success()
            self.overview_preview_stage.refresh_preview()
            self.wallpaper_preview_stage.refresh_preview()
            self._on_ui_tick()

    def _on_action_next(self) -> None:
        res = self._wp_mgr.apply_next()
        if res.success:
            self._scheduler.record_manual_switch_success()
            self.overview_preview_stage.refresh_preview()
            self.wallpaper_preview_stage.refresh_preview()
            self._on_ui_tick()

    def _on_action_random(self) -> None:
        res = self._wp_mgr.apply_random()
        if res.success:
            self._scheduler.record_manual_switch_success()
            self.overview_preview_stage.refresh_preview()
            self.wallpaper_preview_stage.refresh_preview()
            self._on_ui_tick()

    def _on_action_apply_current(self) -> None:
        res = self._wp_mgr.apply_current_or_next()
        if res.success:
            self._scheduler.record_manual_switch_success()
            self.overview_preview_stage.refresh_preview()
            self.wallpaper_preview_stage.refresh_preview()
            self._on_ui_tick()

    def _on_action_toggle_pause(self) -> None:
        is_paused = self._scheduler.toggle_pause()
        self.action_bar.set_paused_state(is_paused)

    def _on_action_check_now(self) -> None:
        self._scheduler.check_now(force=True)

    def _on_action_launch_screensaver(self, force: bool = False) -> None:
        if ScreensaverMutexGuard.is_active():
            return
        if self._screensaver_preview_ctrl and self._screensaver_preview_ctrl.is_running:
            return

        if not force:
            ss_cfg = self._settings.config.screensaver
            suppress_m = getattr(ss_cfg, "suppress_when_media_playing", True)
            suppress_f = getattr(ss_cfg, "suppress_when_fullscreen", True)
            busy_state = check_system_media_and_busy_state(
                suppress_media=suppress_m,
                suppress_fullscreen=suppress_f,
            )
            if busy_state.is_busy:
                logger.info("系统处于忙碌或多媒体播放状态 (%s)，抑制启动全屏屏保", busy_state.reason)
                return

        try:
            self._settings.load()
        except Exception:
            pass

        was_visible = self.isVisible()

        def _on_ss_closed() -> None:
            self._screensaver_preview_ctrl = None
            self._last_screensaver_exit_mono = time.monotonic()
            self._system_keep_awake_active = False
            if was_visible:
                self.showNormal()
                self.raise_()
                self.activateWindow()

        ctrl = ScreensaverController(
            settings_manager=self._settings,
            image_library=self._library,
            on_closed=_on_ss_closed,
        )
        if ctrl.start_fullscreen():
            self._screensaver_preview_ctrl = ctrl

    # 调度器与时间
    def _set_scheduler_interval(self, secs: int) -> None:
        self._scheduler.set_interval(secs)
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        self.spin_hours.setValue(h)
        self.spin_mins.setValue(m)
        self.spin_secs.setValue(s)
        self._notify_status(f"壁纸切换间隔已更新为 {secs} 秒")

    def _on_apply_custom_interval(self) -> None:
        secs = self.spin_hours.value() * 3600 + self.spin_mins.value() * 60 + self.spin_secs.value()
        if secs < 10:
            QMessageBox.warning(self, "提示", "切换间隔不得低于 10 秒。")
            return
        self._set_scheduler_interval(secs)

    # 图片文件夹操作
    def _on_add_library_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择图片来源文件夹")
        if not folder:
            return
        self._library.add_source(folder, recursive=True, enabled=True)
        self._reload_source_table()
        self._on_start_library_scan()

    def _on_remove_library_folder(self) -> None:
        row = self.tbl_lib_sources.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选中要移除的文件夹行。")
            return
        path_item = self.tbl_lib_sources.item(row, 0)
        if path_item:
            self._library.remove_source(path_item.text())
            self._reload_source_table()
            self._on_start_library_scan()

    def _reload_source_table(self) -> None:
        sources = self._library.get_sources()
        self.tbl_lib_sources.setRowCount(len(sources))
        for r, s in enumerate(sources):
            it_path = QTableWidgetItem(s.path)
            it_en = QTableWidgetItem("● 启用中" if s.enabled else "○ 已停用")
            it_en.setForeground(QColor("#34d399") if s.enabled else QColor("#94a3b8"))
            it_rec = QTableWidgetItem("● 是 (递归)" if s.recursive else "○ 否 (单层)")
            it_rec.setForeground(QColor("#60a5fa") if s.recursive else QColor("#94a3b8"))
            it_cnt = QTableWidgetItem(f"{s.valid_count} 张")
            it_cnt.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tbl_lib_sources.setItem(r, 0, it_path)
            self.tbl_lib_sources.setItem(r, 1, it_en)
            self.tbl_lib_sources.setItem(r, 2, it_rec)
            self.tbl_lib_sources.setItem(r, 3, it_cnt)

    def _on_start_library_scan(self) -> None:
        if self._scan_worker and self._scan_worker.isRunning():
            return
        self.btn_scan_now.setEnabled(False)
        self.scan_progress_bar.setVisible(True)
        self.scan_progress_bar.setValue(0)
        self.lbl_scan_status.setText("正在扫描图片库中...")

        self._scan_worker = BackgroundScanWorker(self._library, self)
        self._scan_worker.progress.connect(self._on_scan_progress)
        self._scan_worker.finished_summary.connect(self._on_scan_finished)
        self._scan_worker.start()

    def _on_scan_progress(self, current: int, total: int, file_path: str) -> None:
        pct = int(current * 100 / max(1, total))
        self.scan_progress_bar.setValue(pct)
        self.lbl_scan_status.setText(f"扫描进度 {current}/{total} : {Path(file_path).name}")

    def _on_scan_finished(self, summary: ScanSummary) -> None:
        self.btn_scan_now.setEnabled(True)
        self.scan_progress_bar.setVisible(False)
        self.lbl_scan_status.setText(
            f"扫描完成: 耗时 {summary.scan_duration_ms:.1f}ms, 发现 {summary.total_scanned} 个文件, 有效 {summary.valid_added} 张, 跳过 {summary.skipped_count} 个"
        )
        self._reload_source_table()
        self.gallery_widget.reload_items()
        self.overview_preview_stage.refresh_preview()
        self.wallpaper_preview_stage.refresh_preview()

        # 刷新跳过文件表格
        self.tbl_skipped.setRowCount(len(summary.skipped_files))
        for r, sf in enumerate(summary.skipped_files):
            self.tbl_skipped.setItem(r, 0, QTableWidgetItem(sf.path))
            self.tbl_skipped.setItem(r, 1, QTableWidgetItem(sf.reason))
            self.tbl_skipped.setItem(r, 2, QTableWidgetItem(sf.detected_type or "未知"))

    # 屏保相关
    def _on_screensaver_clock_config_changed(self) -> None:
        if getattr(self, "_is_loading_settings", False):
            return
        ss = self._settings.config.screensaver
        ss.show_clock_overlay = self.chk_ss_clock.isChecked()
        ss.clock_size = str(self.combo_ss_clock_size.currentData() or "medium")
        ss.clock_position = str(self.combo_ss_clock_pos.currentData() or "bottom_left")
        self._settings.save()

    def _on_screensaver_idle_config_changed(self) -> None:
        if self._is_loading_settings:
            return
        ss = self._settings.config.screensaver
        ss.idle_enabled = self.chk_ss_idle_enabled.isChecked()
        ss.timeout_seconds = self.spin_ss_idle_timeout.value()
        if hasattr(self, "chk_ss_suppress_media"):
            ss.suppress_when_media_playing = self.chk_ss_suppress_media.isChecked()
        if hasattr(self, "chk_ss_suppress_fullscreen"):
            ss.suppress_when_fullscreen = self.chk_ss_suppress_fullscreen.isChecked()
        if hasattr(self, "chk_ss_prevent_sleep"):
            ss.prevent_system_sleep = self.chk_ss_prevent_sleep.isChecked()
        if hasattr(self, "combo_ss_timed_sleep"):
            ss.timed_sleep_minutes = int(self.combo_ss_timed_sleep.currentData() or 0)
        if hasattr(self, "combo_ss_sleep_action"):
            ss.timed_sleep_action = str(self.combo_ss_sleep_action.currentData() or "sleep")
        self._settings.save()

    def _on_sync_screensaver_from_windows(self) -> None:
        info = get_system_screensaver_status()
        try:
            sec = int(info.get("timeout_seconds", "60"))
            if sec >= 10:
                self.spin_ss_idle_timeout.setValue(sec)
                self._notify_status(f"已从 Windows 系统屏保设置同步等待时间：{sec} 秒")
            else:
                self._notify_status(f"Windows 屏保设置的等待时间为 {sec} 秒，已保留现有设置")
        except ValueError:
            self._notify_status("未能识别 Windows 屏保等待时间")

    def _on_register_screensaver(self) -> None:
        root_dir = Path(__file__).resolve().parent.parent.parent
        candidates = [
            root_dir / "dist" / "GouWallpaperSaver.scr",
            root_dir / "GouWallpaperSaver.scr",
            root_dir / "dist" / "WallFlowSaver.scr",
            root_dir / "WallFlowSaver.scr",
        ]
        scr_path = next((p for p in candidates if p.exists()), None)
        if scr_path is None:
            QMessageBox.warning(self, "提示", "未找到屏保程序 GouWallpaperSaver.scr。\n请先运行 build.py 完成构建。")
            return
        to_sec = int(getattr(self._settings.config.screensaver, "timeout_seconds", 60))
        ok, err = install_screensaver_to_user_desktop_config(str(scr_path), timeout_seconds=to_sec)
        if ok:
            QMessageBox.information(self, "成功", f"已成功将 {scr_path.name} 注册为当前用户 Windows 屏保！\n空闲超时已同步设置为 {to_sec} 秒。")
            self._update_screensaver_sys_status()
        else:
            QMessageBox.critical(self, "失败", f"注册屏保失败: {err}")

    def _on_screensaver_source_mode_changed(self, checked: bool) -> None:
        if getattr(self, "_is_loading_settings", False):
            return
        ss = self._settings.config.screensaver
        ss.use_wallpaper_library = bool(checked)
        self._settings.save()
        self._update_screensaver_source_status_label()

    def _update_screensaver_source_status_label(self) -> None:
        if not hasattr(self, "lbl_ss_src_status"):
            return
        ss = self._settings.config.screensaver
        if ss.use_wallpaper_library:
            wp_count = self._library.total_valid_count if hasattr(self, "_library") else 0
            self.lbl_ss_src_status.setText(
                f"当前模式：共用桌面壁纸库（当前共 {wp_count} 张壁纸，随桌面壁纸同步更新）"
            )
        else:
            folder_cnt = len(ss.folders) if ss.folders else 0
            en_cnt = len([f for f in ss.folders if f.enabled]) if ss.folders else 0
            if folder_cnt == 0:
                self.lbl_ss_src_status.setText(
                    "当前模式：使用独立屏保图片文件夹（尚未添加任何独立文件夹，运行时将自动回退壁纸库）"
                )
            else:
                self.lbl_ss_src_status.setText(
                    f"当前模式：使用独立屏保图片文件夹（已添加 {folder_cnt} 个文件夹，启用 {en_cnt} 个）"
                )

    def _on_open_screensaver_config_dialog(self) -> None:
        dlg = ScreensaverConfigDialog(
            settings_manager=self._settings,
            parent=self,
            image_library=self._library,
        )
        dlg.exec()
        # 对话框关闭后从磁盘重新载入最新配置并刷新控件
        try:
            self._settings.load()
        except Exception:
            pass
        ss = self._settings.config.screensaver
        if hasattr(self, "chk_ss_use_wp_lib"):
            self.chk_ss_use_wp_lib.setChecked(getattr(ss, "use_wallpaper_library", True))
            self._update_screensaver_source_status_label()
        self.chk_ss_idle_enabled.setChecked(getattr(ss, "idle_enabled", True))
        self.spin_ss_idle_timeout.setValue(max(10, int(getattr(ss, "timeout_seconds", 60))))
        self.chk_ss_clock.setChecked(getattr(ss, "show_clock_overlay", False))
        idx_cs = self.combo_ss_clock_size.findData(getattr(ss, "clock_size", "medium"))
        if idx_cs >= 0:
            self.combo_ss_clock_size.setCurrentIndex(idx_cs)
        idx_cp = self.combo_ss_clock_pos.findData(getattr(ss, "clock_position", "bottom_left"))
        if idx_cp >= 0:
            self.combo_ss_clock_pos.setCurrentIndex(idx_cp)

    def _update_screensaver_sys_status(self) -> None:
        heal_screensaver_registry_path()
        info = get_system_screensaver_status()
        self.lbl_ss_reg_file.setText(info.get("scrnsave_exe", "(未设置)"))
        self.lbl_ss_reg_active.setText(info.get("active", "否"))
        timeout = info.get("timeout_seconds", "0")
        try:
            m = int(timeout) // 60
            s = int(timeout) % 60
            if s == 0:
                self.lbl_ss_reg_timeout.setText(f"{timeout} 秒 ({m} 分钟)")
            else:
                self.lbl_ss_reg_timeout.setText(f"{timeout} 秒 ({m} 分 {s} 秒)")
        except ValueError:
            self.lbl_ss_reg_timeout.setText(f"{timeout} 秒")

    # 系统高级设置
    def _on_toggle_startup(self, checked: bool) -> None:
        if self._is_loading_settings:
            return
        if checked:
            self._startup_mgr.enable(minimized=self.chk_start_min.isChecked())
        else:
            self._startup_mgr.disable()

    def _on_system_config_changed(self) -> None:
        if self._is_loading_settings:
            return
        self._settings.config.system.start_minimized = self.chk_start_min.isChecked()
        self._settings.config.system.close_to_tray = self.chk_close_to_tray.isChecked()
        self._settings.save()

    def _bind_events(self) -> None:
        self._rebind_hotkeys()
        self._wp_mgr.add_listener(lambda res: self.wallpaperApplied.emit(res))
        self.wallpaperApplied.connect(self._on_wallpaper_applied_externally)

    def _on_wallpaper_applied_externally(self, res: ApplyResult) -> None:
        if res and res.success:
            paths = res.applied_paths
            if paths:
                self.overview_preview_stage.refresh_preview(override_paths=paths)
                self.wallpaper_preview_stage.refresh_preview(override_paths=paths)

    def _rebind_hotkeys(self) -> None:
        sys_cfg = self._settings.config.system
        bindings = {
            HOTKEY_ID_NEXT: (sys_cfg.hotkey_next, self._on_action_next),
            HOTKEY_ID_PREV: (sys_cfg.hotkey_prev, self._on_action_prev),
            HOTKEY_ID_RANDOM: (sys_cfg.hotkey_random, self._on_action_random),
            HOTKEY_ID_TOGGLE_PAUSE: (sys_cfg.hotkey_toggle_pause, self._on_action_toggle_pause),
            HOTKEY_ID_TOGGLE_DESKTOP_ICONS: (
                getattr(sys_cfg, "hotkey_toggle_desktop_icons", "Ctrl+Alt+H"),
                self._on_action_toggle_desktop_icons,
            ),
        }
        self._hotkey_mgr.register_all(sys_cfg.hotkeys_enabled, bindings)

    def _on_action_toggle_desktop_icons(self) -> None:
        toggle_desktop_icons_visibility()

    def _on_hotkey_config_changed(self) -> None:
        if self._is_loading_settings:
            return
        sys_cfg = self._settings.config.system
        sys_cfg.hotkeys_enabled = self.chk_hk_enabled.isChecked()
        sys_cfg.hotkey_next = self.edit_hk_next.text().strip()
        sys_cfg.hotkey_prev = self.edit_hk_prev.text().strip()
        sys_cfg.hotkey_random = self.edit_hk_random.text().strip()
        sys_cfg.hotkey_toggle_pause = self.edit_hk_pause.text().strip()
        sys_cfg.hotkey_toggle_desktop_icons = self.edit_hk_icons.text().strip()
        self._settings.save()
        self._rebind_hotkeys()

    def _on_clear_render_cache(self) -> None:
        self._wp_mgr._cleanup_old_rendered_files()
        self._notify_status("临时渲染壁纸位图缓存已清理完成。")

    def _on_copy_logs(self) -> None:
        logs = self.txt_logs.toPlainText()
        QApplication.clipboard().setText(logs)
        self._notify_status("运行日志已复制到系统剪贴板。")

    def _on_clear_logs(self) -> None:
        get_memory_handler().clear()
        self.txt_logs.clear()

    def _on_open_log_folder(self) -> None:
        log_file = get_log_file_path()
        if log_file.exists():
            subprocess.Popen(["explorer.exe", f"/select,{os.path.normpath(str(log_file))}"])
        else:
            subprocess.Popen(["explorer.exe", str(log_file.parent)])

    def _load_all_settings_to_ui(self) -> None:
        """从持久化配置文件加载全部设置到 UI 组件."""
        self._is_loading_settings = True
        try:
            cfg = self._settings.config

            # 壁纸设置
            wp = cfg.wallpaper
            idx_p = self.combo_wp_playback.findData(wp.playback_mode)
            if idx_p >= 0:
                self.combo_wp_playback.setCurrentIndex(idx_p)
            idx_d = self.combo_wp_display.findData(wp.display_mode)
            if idx_d >= 0:
                self.combo_wp_display.setCurrentIndex(idx_d)
            idx_m = self.combo_wp_multi.findData(wp.multi_monitor_mode)
            if idx_m >= 0:
                self.combo_wp_multi.setCurrentIndex(idx_m)

            self.lbl_wp_bg_color.setText(wp.background_color)
            self.lbl_wp_bg_color.setStyleSheet(
                f"background-color: {wp.background_color}; border: 1px solid #334155; border-radius: 4px; padding: 2px 12px; font-family: monospace;"
            )

            self.chk_collage.setChecked(wp.collage_enabled)
            self.spin_collage_count.setValue(wp.collage_count)

            # 色彩微调
            self.slider_bright.setValue(wp.adjustments.brightness)
            self.lbl_bright_val.setText(str(wp.adjustments.brightness))
            self.slider_contrast.setValue(wp.adjustments.contrast)
            self.lbl_contrast_val.setText(str(wp.adjustments.contrast))
            self.slider_sat.setValue(wp.adjustments.saturation)
            self.lbl_sat_val.setText(str(wp.adjustments.saturation))
            self.slider_warmth.setValue(wp.adjustments.warmth)
            self.lbl_warmth_val.setText(str(wp.adjustments.warmth))

            # 调度间隔
            secs = cfg.scheduler.interval_seconds
            h = secs // 3600
            m = (secs % 3600) // 60
            s = secs % 60
            self.spin_hours.setValue(h)
            self.spin_mins.setValue(m)
            self.spin_secs.setValue(s)

            # 高级设置与热键
            sys_cfg = cfg.system
            self.chk_startup.setChecked(self._startup_mgr.is_enabled())
            self.chk_start_min.setChecked(sys_cfg.start_minimized)
            self.chk_close_to_tray.setChecked(sys_cfg.close_to_tray)

            self.chk_hk_enabled.setChecked(sys_cfg.hotkeys_enabled)
            self.edit_hk_next.setText(sys_cfg.hotkey_next)
            self.edit_hk_prev.setText(sys_cfg.hotkey_prev)
            self.edit_hk_random.setText(sys_cfg.hotkey_random)
            self.edit_hk_pause.setText(sys_cfg.hotkey_toggle_pause)
            self.edit_hk_icons.setText(getattr(sys_cfg, "hotkey_toggle_desktop_icons", "Ctrl+Alt+H"))

            # 屏保来源、时钟与看门狗设置
            ss = cfg.screensaver
            if hasattr(self, "chk_ss_use_wp_lib"):
                self.chk_ss_use_wp_lib.setChecked(getattr(ss, "use_wallpaper_library", True))
                self._update_screensaver_source_status_label()
            self.chk_ss_idle_enabled.setChecked(getattr(ss, "idle_enabled", True))
            if hasattr(self, "chk_ss_suppress_media"):
                self.chk_ss_suppress_media.setChecked(getattr(ss, "suppress_when_media_playing", True))
            if hasattr(self, "chk_ss_suppress_fullscreen"):
                self.chk_ss_suppress_fullscreen.setChecked(getattr(ss, "suppress_when_fullscreen", True))
            if hasattr(self, "chk_ss_prevent_sleep"):
                self.chk_ss_prevent_sleep.setChecked(getattr(ss, "prevent_system_sleep", True))
            if hasattr(self, "combo_ss_timed_sleep"):
                idx_ts = self.combo_ss_timed_sleep.findData(getattr(ss, "timed_sleep_minutes", 30))
                if idx_ts >= 0:
                    self.combo_ss_timed_sleep.setCurrentIndex(idx_ts)
            if hasattr(self, "combo_ss_sleep_action"):
                idx_sa = self.combo_ss_sleep_action.findData(getattr(ss, "timed_sleep_action", "sleep"))
                if idx_sa >= 0:
                    self.combo_ss_sleep_action.setCurrentIndex(idx_sa)
            self.spin_ss_idle_timeout.setValue(max(10, int(getattr(ss, "timeout_seconds", 60))))
            self.chk_ss_clock.setChecked(getattr(ss, "show_clock_overlay", False))
            idx_cs = self.combo_ss_clock_size.findData(getattr(ss, "clock_size", "medium"))
            if idx_cs >= 0:
                self.combo_ss_clock_size.setCurrentIndex(idx_cs)
            idx_cp = self.combo_ss_clock_pos.findData(getattr(ss, "clock_position", "bottom_left"))
            if idx_cp >= 0:
                self.combo_ss_clock_pos.setCurrentIndex(idx_cp)

            # 刷新来源文件夹列表与屏保状态
            self._reload_source_table()
            self._update_screensaver_sys_status()

        finally:
            self._is_loading_settings = False

    def _save_all_settings_from_ui(self) -> None:
        """用户点击顶部“保存配置”按钮."""
        self._settings.save()
        self._notify_status("Gou Wallpaper 全部壁纸与调度配置已成功持久化保存！")

    def _on_enter_background_mode(self) -> None:
        """进入后台/托盘模式，全面释放重量级 UI 渲染资源与内存缓存，保持极低内存驻留."""
        try:
            logger.debug("MainWindow entering background mode, releasing heavy UI resources...")
            if hasattr(self, "overview_preview_stage"):
                self.overview_preview_stage.release_preview_memory()
            if hasattr(self, "wallpaper_preview_stage"):
                self.wallpaper_preview_stage.release_preview_memory()
            if hasattr(self, "gallery_widget"):
                self.gallery_widget.release_gallery_memory()
            if hasattr(self, "_library"):
                self._library.clear_memory_cache()
            clear_image_cache()
            trim_process_memory()
        except Exception as exc:
            logger.warning("进入后台资源释放失败: %s", exc)

    def _on_leave_background_mode(self) -> None:
        """从后台恢复前台显示，按需仅重新渲染当前激活的标签页."""
        try:
            logger.debug("MainWindow leaving background mode, refreshing active tab...")
            cur_row = self.nav_list.currentRow() if hasattr(self, "nav_list") else 0
            if cur_row == 0 and hasattr(self, "overview_preview_stage"):
                self.overview_preview_stage.refresh_preview(force=True)
            elif cur_row == 1 and hasattr(self, "wallpaper_preview_stage"):
                self.wallpaper_preview_stage.refresh_preview(force=True)
            elif cur_row == 2 and hasattr(self, "gallery_widget"):
                self.gallery_widget.reload_items(force=True)
            self._on_ui_tick()
        except Exception as exc:
            logger.warning("恢复前台资源重载失败: %s", exc)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._on_leave_background_mode()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        super().hideEvent(event)
        if not getattr(self, "_is_quitting", False):
            self._on_enter_background_mode()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self._on_enter_background_mode()
            elif not self.isMinimized() and self.isVisible():
                self._on_leave_background_mode()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """处理窗口关闭事件：区分最小化到托盘与真正退出."""
        if not self._is_quitting and self._settings.config.system.close_to_tray:
            event.ignore()
            self.hide()
            self._on_enter_background_mode()
        else:
            if getattr(self, "_system_keep_awake_active", False):
                set_thread_display_required(False)
                self._system_keep_awake_active = False
            self._scheduler.stop()
            self._hotkey_mgr.unregister_all()
            event.accept()

    def quit_application(self) -> None:
        self._is_quitting = True
        self.close()
        QApplication.quit()

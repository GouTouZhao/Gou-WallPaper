"""Windows 屏幕保护程序独立配置对话框 (`app/ui/screensaver_config_dialog.py`).

当 Windows 以 `WallFlowSaver.scr /c` 或 `/c:<hwnd>` 调用屏保时弹出此对话框；
也可在主界面点击“打开屏保配置对话框”时直接复用。
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.icon_helper import get_app_qicon
from app.screensaver import ScreensaverController
from app.settings_manager import FolderSourceConfig, SettingsManager
from app.ui.styles import MAIN_STYLESHEET
from app.win32_integration import open_windows_screensaver_settings


class ScreensaverConfigDialog(QDialog):
    """独立屏保配置对话框（支持 `/c` 模式启动）."""

    def __init__(
        self,
        settings_manager: SettingsManager,
        auto_close_ms: int | None = None,
        parent: QWidget | None = None,
        image_library: Any = None,
    ) -> None:
        # 兼容处理：若调用端传入了 (settings, library, parent) 或其他参数形式
        if isinstance(auto_close_ms, QWidget) and parent is None:
            parent = auto_close_ms
            auto_close_ms = None
        elif not isinstance(auto_close_ms, (int, float)):
            if hasattr(auto_close_ms, "get_all_images"):
                image_library = auto_close_ms
            auto_close_ms = None

        super().__init__(parent)
        self._settings = settings_manager
        self._library = image_library
        self._preview_ctrl: ScreensaverController | None = None

        self.setWindowTitle("Gou Wallpaper 屏幕保护程序配置")
        self.setWindowIcon(get_app_qicon())
        self.resize(760, 680)
        self.setStyleSheet(MAIN_STYLESHEET)

        self._build_ui()
        self._load_from_settings()

        if auto_close_ms is not None and isinstance(auto_close_ms, (int, float)) and auto_close_ms > 0:
            QTimer.singleShot(int(auto_close_ms), self.accept)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        # 核心滚动区：保证高 DPI 或复杂表单下表格绝不被挤压坍塌
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(14)

        title = QLabel("Gou Wallpaper 原生屏幕保护程序设置")
        title.setObjectName("PageTitle")
        content_layout.addWidget(title)

        # 1. 图片来源配置
        src_group = QGroupBox("屏保图片来源")
        src_group.setObjectName("Card")
        src_layout = QVBoxLayout(src_group)
        src_layout.setContentsMargins(16, 14, 16, 14)
        src_layout.setSpacing(10)

        self.chk_use_wallpaper_lib = QCheckBox("直接共用桌面壁纸的图片库文件夹（推荐）")
        self.chk_use_wallpaper_lib.toggled.connect(self._on_source_mode_toggled)
        src_layout.addWidget(self.chk_use_wallpaper_lib)

        self.folder_table = QTableWidget(0, 3)
        self.folder_table.setAlternatingRowColors(True)
        self.folder_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.folder_table.setShowGrid(True)
        self.folder_table.setMinimumHeight(170)
        self.folder_table.verticalHeader().setVisible(False)
        self.folder_table.setHorizontalHeaderLabels(["文件夹路径", "启用状态", "递归扫描子目录"])
        self.folder_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.folder_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.folder_table.setColumnWidth(1, 100)
        self.folder_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.folder_table.setColumnWidth(2, 140)
        src_layout.addWidget(self.folder_table)

        btn_row = QHBoxLayout()
        self.btn_add_folder = QPushButton("添加图片文件夹")
        self.btn_add_folder.clicked.connect(self._on_add_folder)
        self.btn_remove_folder = QPushButton("移除选中文件夹")
        self.btn_remove_folder.setObjectName("SecondaryBtn")
        self.btn_remove_folder.clicked.connect(self._on_remove_folder)
        btn_row.addWidget(self.btn_add_folder)
        btn_row.addWidget(self.btn_remove_folder)
        btn_row.addStretch()
        src_layout.addLayout(btn_row)
        content_layout.addWidget(src_group)

        # 2. 播放与过渡效果设置
        opt_group = QGroupBox("显示方式、轮播间隔与过渡动画")
        opt_group.setObjectName("Card")
        form = QFormLayout(opt_group)

        self.combo_playback = QComboBox()
        self.combo_playback.addItem("不放回随机轮播 (Random)", "random")
        self.combo_playback.addItem("顺序播放 (Sequential)", "sequential")
        form.addRow("播放顺序模式：", self.combo_playback)

        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(3, 86400)
        self.spin_interval.setSuffix(" 秒")
        form.addRow("屏保内部切换间隔：", self.spin_interval)

        self.combo_display = QComboBox()
        self.combo_display.addItem("适应屏幕 (Fit - 保持比例留黑边)", "fit")
        self.combo_display.addItem("填充屏幕 (Fill - 等比裁剪填满)", "fill")
        self.combo_display.addItem("居中原图 (Center)", "center")
        self.combo_display.addItem("拉伸填满 (Stretch)", "stretch")
        form.addRow("图片显示布局：", self.combo_display)

        self.combo_transition = QComboBox()
        self.combo_transition.addItem("淡入淡出 (Fade)", "fade")
        self.combo_transition.addItem("平滑平移 (Slide)", "slide")
        self.combo_transition.addItem("微距缩放 (Zoom)", "zoom")
        self.combo_transition.addItem("无过渡直接切换 (None)", "none")
        form.addRow("图片过渡效果：", self.combo_transition)

        self.spin_trans_ms = QSpinBox()
        self.spin_trans_ms.setRange(0, 5000)
        self.spin_trans_ms.setSingleStep(100)
        self.spin_trans_ms.setSuffix(" 毫秒")
        form.addRow("过渡动画持续时间：", self.spin_trans_ms)

        bg_row = QHBoxLayout()
        self.lbl_bg_color = QLabel("#000000")
        self.btn_pick_color = QPushButton("选择背景色")
        self.btn_pick_color.setObjectName("SecondaryBtn")
        self.btn_pick_color.clicked.connect(self._on_pick_color)
        bg_row.addWidget(self.lbl_bg_color)
        bg_row.addWidget(self.btn_pick_color)
        bg_row.addStretch()
        form.addRow("背景填充颜色：", bg_row)

        self.chk_per_monitor = QCheckBox("多显示器分别显示不同的随机图片")
        form.addRow("", self.chk_per_monitor)

        self.chk_match_orientation = QCheckBox("智能匹配显示器方向（横屏优先横向图、竖屏优先纵向图）")
        form.addRow("", self.chk_match_orientation)

        self.chk_exit_mouse = QCheckBox("移动鼠标时立即退出屏保（取消勾选则仅在按键或点击鼠标时退出）")
        form.addRow("", self.chk_exit_mouse)

        self.chk_clock = QCheckBox("在屏保上显示简约数字时钟")
        form.addRow("", self.chk_clock)

        self.combo_clock_size = QComboBox()
        self.combo_clock_size.addItem("小 (Small)", "small")
        self.combo_clock_size.addItem("中等 (Medium - 默认)", "medium")
        self.combo_clock_size.addItem("大 (Large)", "large")
        self.combo_clock_size.addItem("特大 (Extra Large)", "xlarge")
        form.addRow("时钟字号大小：", self.combo_clock_size)

        self.combo_clock_pos = QComboBox()
        self.combo_clock_pos.addItem("左下角 (Bottom Left - 默认)", "bottom_left")
        self.combo_clock_pos.addItem("居中偏上 (Center Top - 锁屏风)", "center_top")
        self.combo_clock_pos.addItem("居中底部 (Center Bottom)", "center_bottom")
        form.addRow("时钟摆放位置：", self.combo_clock_pos)

        self.chk_idle_enabled = QCheckBox("启用系统空闲无操作时自动启动全屏屏保")
        form.addRow("", self.chk_idle_enabled)

        self.chk_suppress_media = QCheckBox("智能免打扰：播放音频/视频 (如抖音、音乐、电影) 时阻止屏保")
        form.addRow("", self.chk_suppress_media)

        self.chk_suppress_fullscreen = QCheckBox("智能免打扰：运行全屏应用 (如游戏、全屏播放器、PPT演示) 时阻止屏保")
        form.addRow("", self.chk_suppress_fullscreen)

        self.spin_idle_timeout = QSpinBox()
        self.spin_idle_timeout.setRange(10, 86400)
        self.spin_idle_timeout.setSuffix(" 秒")
        form.addRow("空闲自动启动等待：", self.spin_idle_timeout)

        self.chk_prevent_sleep = QCheckBox("屏保运行时阻止系统休眠与息屏")
        form.addRow("", self.chk_prevent_sleep)

        self.combo_timed_sleep = QComboBox()
        self.combo_timed_sleep.addItem("从不 (一直保持屏保轮播)", 0)
        self.combo_timed_sleep.addItem("运行 10 分钟后自动睡眠", 10)
        self.combo_timed_sleep.addItem("运行 15 分钟后自动睡眠", 15)
        self.combo_timed_sleep.addItem("运行 30 分钟后自动睡眠 (推荐)", 30)
        self.combo_timed_sleep.addItem("运行 1 小时后自动睡眠", 60)
        self.combo_timed_sleep.addItem("运行 2 小时后自动睡眠", 120)
        form.addRow("定时进入睡眠：", self.combo_timed_sleep)

        self.combo_sleep_action = QComboBox()
        self.combo_sleep_action.addItem("系统睡眠 (推荐)", "sleep")
        self.combo_sleep_action.addItem("关闭显示器 (息屏省电)", "turn_off_screen")
        self.combo_sleep_action.addItem("仅解除阻止 (由 Windows 接管)", "allow_sleep")
        form.addRow("定时电源动作：", self.combo_sleep_action)

        content_layout.addWidget(opt_group)
        scroll_area.setWidget(content_widget)
        root_layout.addWidget(scroll_area, 1)

        # 3. 底部动作按钮
        bottom_row = QHBoxLayout()
        btn_sys_ss = QPushButton("打开 Windows 系统屏保设置")
        btn_sys_ss.setObjectName("SecondaryBtn")
        btn_sys_ss.clicked.connect(open_windows_screensaver_settings)

        btn_test_ss = QPushButton("立即全屏预览屏保")
        btn_test_ss.setObjectName("SuccessBtn")
        btn_test_ss.clicked.connect(self._on_test_screensaver)

        btn_save = QPushButton("保存并关闭")
        btn_save.clicked.connect(self._on_save_and_close)

        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("SecondaryBtn")
        btn_cancel.clicked.connect(self.reject)

        bottom_row.addWidget(btn_sys_ss)
        bottom_row.addWidget(btn_test_ss)
        bottom_row.addStretch()
        bottom_row.addWidget(btn_save)
        bottom_row.addWidget(btn_cancel)
        root_layout.addLayout(bottom_row)

        # 拦截对话框内所有选项下拉框与数字微调框的滚轮事件，彻底根除滚轮误触
        from app.ui.main_window import NoWheelScrollForwarder
        fwd_dialog = NoWheelScrollForwarder(None, self)
        for ctrl in [
            self.combo_playback,
            self.spin_interval,
            self.combo_display,
            self.combo_transition,
            self.spin_trans_ms,
            self.combo_clock_size,
            self.combo_clock_pos,
            self.spin_idle_timeout,
        ]:
            ctrl.installEventFilter(fwd_dialog)

    def _load_from_settings(self) -> None:
        ss = self._settings.config.screensaver
        self.chk_use_wallpaper_lib.setChecked(ss.use_wallpaper_library)
        self._populate_folder_table(ss.folders)
        self._on_source_mode_toggled(ss.use_wallpaper_library)

        idx_play = self.combo_playback.findData(ss.playback_mode)
        if idx_play >= 0:
            self.combo_playback.setCurrentIndex(idx_play)

        self.spin_interval.setValue(ss.interval_seconds)

        idx_disp = self.combo_display.findData(ss.display_mode)
        if idx_disp >= 0:
            self.combo_display.setCurrentIndex(idx_disp)

        idx_trans = self.combo_transition.findData(ss.transition_effect)
        if idx_trans >= 0:
            self.combo_transition.setCurrentIndex(idx_trans)

        self.spin_trans_ms.setValue(ss.transition_duration_ms)
        self.lbl_bg_color.setText(ss.background_color)
        self.chk_per_monitor.setChecked(ss.independent_per_monitor)
        self.chk_match_orientation.setChecked(ss.match_orientation)
        self.chk_exit_mouse.setChecked(ss.exit_on_mouse_move)
        self.chk_clock.setChecked(ss.show_clock_overlay)
        idx_clock_size = self.combo_clock_size.findData(getattr(ss, "clock_size", "medium"))
        if idx_clock_size >= 0:
            self.combo_clock_size.setCurrentIndex(idx_clock_size)
        idx_clock_pos = self.combo_clock_pos.findData(getattr(ss, "clock_position", "bottom_left"))
        if idx_clock_pos >= 0:
            self.combo_clock_pos.setCurrentIndex(idx_clock_pos)
        self.chk_idle_enabled.setChecked(getattr(ss, "idle_enabled", True))
        self.chk_suppress_media.setChecked(getattr(ss, "suppress_when_media_playing", True))
        self.chk_suppress_fullscreen.setChecked(getattr(ss, "suppress_when_fullscreen", True))
        self.chk_prevent_sleep.setChecked(getattr(ss, "prevent_system_sleep", True))
        idx_ts = self.combo_timed_sleep.findData(getattr(ss, "timed_sleep_minutes", 30))
        if idx_ts >= 0:
            self.combo_timed_sleep.setCurrentIndex(idx_ts)
        idx_sa = self.combo_sleep_action.findData(getattr(ss, "timed_sleep_action", "sleep"))
        if idx_sa >= 0:
            self.combo_sleep_action.setCurrentIndex(idx_sa)
        self.spin_idle_timeout.setValue(max(10, int(getattr(ss, "timeout_seconds", 60))))

    def _populate_folder_table(self, folders: list[FolderSourceConfig]) -> None:
        self.folder_table.setRowCount(0)
        for f in folders:
            self._append_folder_row(f.path, f.enabled, f.recursive)

    def _append_folder_row(self, path: str, enabled: bool = True, recursive: bool = True) -> None:
        row = self.folder_table.rowCount()
        self.folder_table.insertRow(row)

        path_item = QTableWidgetItem(str(path))
        path_item.setToolTip(str(path))
        self.folder_table.setItem(row, 0, path_item)

        # 启用状态：居中复选框
        w_en = QWidget()
        lay_en = QHBoxLayout(w_en)
        lay_en.setContentsMargins(0, 0, 0, 0)
        lay_en.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk_en = QCheckBox()
        chk_en.setChecked(enabled)
        lay_en.addWidget(chk_en)
        self.folder_table.setCellWidget(row, 1, w_en)

        # 递归扫描：居中复选框
        w_rec = QWidget()
        lay_rec = QHBoxLayout(w_rec)
        lay_rec.setContentsMargins(0, 0, 0, 0)
        lay_rec.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chk_rec = QCheckBox()
        chk_rec.setChecked(recursive)
        lay_rec.addWidget(chk_rec)
        self.folder_table.setCellWidget(row, 2, w_rec)

        self.folder_table.setRowHeight(row, 36)

    def _collect_folders_from_table(self) -> list[FolderSourceConfig]:
        results: list[FolderSourceConfig] = []
        for r in range(self.folder_table.rowCount()):
            item = self.folder_table.item(r, 0)
            if not item or not item.text().strip():
                continue
            w_en = self.folder_table.cellWidget(r, 1)
            w_rec = self.folder_table.cellWidget(r, 2)
            chk_en = w_en if isinstance(w_en, QCheckBox) else (w_en.findChild(QCheckBox) if w_en else None)
            chk_rec = w_rec if isinstance(w_rec, QCheckBox) else (w_rec.findChild(QCheckBox) if w_rec else None)
            en = chk_en.isChecked() if chk_en else True
            rec = chk_rec.isChecked() if chk_rec else True
            results.append(FolderSourceConfig(path=item.text().strip(), enabled=en, recursive=rec))
        return results

    def _on_source_mode_toggled(self, use_wallpaper_lib: bool) -> None:
        self.folder_table.setEnabled(not use_wallpaper_lib)
        self.btn_add_folder.setEnabled(not use_wallpaper_lib)
        self.btn_remove_folder.setEnabled(not use_wallpaper_lib)

    def _on_add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择屏保图片文件夹")
        if folder:
            norm = os.path.normpath(folder)
            for r in range(self.folder_table.rowCount()):
                it = self.folder_table.item(r, 0)
                if it and os.path.normpath(it.text()) == norm:
                    self.folder_table.selectRow(r)
                    return
            self._append_folder_row(norm, True, True)
            new_row = self.folder_table.rowCount() - 1
            self.folder_table.selectRow(new_row)
            item = self.folder_table.item(new_row, 0)
            if item:
                self.folder_table.scrollToItem(item)

    def _on_remove_folder(self) -> None:
        row = self.folder_table.currentRow()
        if row >= 0:
            self.folder_table.removeRow(row)

    def _on_pick_color(self) -> None:
        color = QColorDialog.getColor(parent=self, title="选择屏保背景填充色")
        if color.isValid():
            self.lbl_bg_color.setText(color.name())

    def _apply_ui_to_settings(self) -> None:
        ss = self._settings.config.screensaver
        ss.use_wallpaper_library = self.chk_use_wallpaper_lib.isChecked()
        ss.folders = self._collect_folders_from_table()
        ss.playback_mode = str(self.combo_playback.currentData())
        ss.interval_seconds = int(self.spin_interval.value())
        ss.display_mode = str(self.combo_display.currentData())
        ss.transition_effect = str(self.combo_transition.currentData())
        ss.transition_duration_ms = int(self.spin_trans_ms.value())
        ss.background_color = self.lbl_bg_color.text().strip() or "#000000"
        ss.independent_per_monitor = self.chk_per_monitor.isChecked()
        ss.match_orientation = self.chk_match_orientation.isChecked()
        ss.exit_on_mouse_move = self.chk_exit_mouse.isChecked()
        ss.show_clock_overlay = self.chk_clock.isChecked()
        ss.clock_size = str(self.combo_clock_size.currentData() or "medium")
        ss.clock_position = str(self.combo_clock_pos.currentData() or "bottom_left")
        ss.idle_enabled = self.chk_idle_enabled.isChecked()
        ss.suppress_when_media_playing = self.chk_suppress_media.isChecked()
        ss.suppress_when_fullscreen = self.chk_suppress_fullscreen.isChecked()
        ss.prevent_system_sleep = self.chk_prevent_sleep.isChecked()
        ss.timed_sleep_minutes = int(self.combo_timed_sleep.currentData() or 0)
        ss.timed_sleep_action = str(self.combo_sleep_action.currentData() or "sleep")
        ss.timeout_seconds = int(self.spin_idle_timeout.value())
        self._settings.save()

    def _on_test_screensaver(self) -> None:
        self._apply_ui_to_settings()
        self._preview_ctrl = ScreensaverController(self._settings)
        self._preview_ctrl.start_fullscreen()

    def _on_save_and_close(self) -> None:
        self._apply_ui_to_settings()
        if not self._settings.config.screensaver.use_wallpaper_library and not self._settings.config.screensaver.folders:
            QMessageBox.information(
                self,
                "提示",
                "您取消了“共用桌面壁纸图片库”，但尚未添加独立屏保文件夹；屏保运行时将自动回退使用壁纸图片库。",
            )
        self.accept()


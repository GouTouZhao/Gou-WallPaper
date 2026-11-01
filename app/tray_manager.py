"""系统托盘与右键菜单管理模块 (`app/tray_manager.py`).

严格满足需求 8.2，托盘菜单包含：
- 打开主界面
- 下一张壁纸
- 上一张壁纸
- 立即随机切换
- 暂停或恢复轮播
- 打开屏保设置
- 检查下一次切换时间
- 退出程序
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from app.icon_helper import get_app_qicon
from app.logger import get_logger
from app.scheduler import SchedulerStatusSnapshot


class TrayManager:
    """系统托盘图标与交互管理器."""

    def __init__(
        self,
        parent_widget: QWidget | None = None,
        icon: QIcon | None = None,
        on_open_main: Callable[[], None] | None = None,
        on_next_wallpaper: Callable[[], None] | None = None,
        on_prev_wallpaper: Callable[[], None] | None = None,
        on_random_wallpaper: Callable[[], None] | None = None,
        on_toggle_pause: Callable[[], None] | None = None,
        on_open_screensaver_settings: Callable[[], None] | None = None,
        on_check_next_due: Callable[[], None] | None = None,
        on_quit_app: Callable[[], None] | None = None,
    ) -> None:
        self._logger = get_logger("tray")
        self._on_open_main = on_open_main
        self._on_next_wallpaper = on_next_wallpaper
        self._on_prev_wallpaper = on_prev_wallpaper
        self._on_random_wallpaper = on_random_wallpaper
        self._on_toggle_pause = on_toggle_pause
        self._on_open_screensaver_settings = on_open_screensaver_settings
        self._on_check_next_due = on_check_next_due
        self._on_quit_app = on_quit_app

        app_icon = icon or get_app_qicon()
        self._tray = QSystemTrayIcon(app_icon, parent_widget)
        self._tray.setToolTip("Gou Wallpaper —— 桌面壁纸与屏幕保护程序管理")

        self._menu = QMenu(parent_widget)
        self._build_menu()
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_tray_activated)

    @property
    def tray_icon(self) -> QSystemTrayIcon:
        return self._tray

    def _build_menu(self) -> None:
        self.act_open_main = QAction("打开主界面", self._menu)
        if self._on_open_main:
            self.act_open_main.triggered.connect(self._on_open_main)
        self._menu.addAction(self.act_open_main)

        self._menu.addSeparator()

        self.act_next = QAction("下一张壁纸", self._menu)
        if self._on_next_wallpaper:
            self.act_next.triggered.connect(self._on_next_wallpaper)
        self._menu.addAction(self.act_next)

        self.act_prev = QAction("上一张壁纸", self._menu)
        if self._on_prev_wallpaper:
            self.act_prev.triggered.connect(self._on_prev_wallpaper)
        self._menu.addAction(self.act_prev)

        self.act_random = QAction("立即随机切换", self._menu)
        if self._on_random_wallpaper:
            self.act_random.triggered.connect(self._on_random_wallpaper)
        self._menu.addAction(self.act_random)

        self.act_toggle_pause = QAction("暂停轮播", self._menu)
        if self._on_toggle_pause:
            self.act_toggle_pause.triggered.connect(self._on_toggle_pause)
        self._menu.addAction(self.act_toggle_pause)

        self._menu.addSeparator()

        self.act_screensaver = QAction("打开屏保设置", self._menu)
        if self._on_open_screensaver_settings:
            self.act_screensaver.triggered.connect(self._on_open_screensaver_settings)
        self._menu.addAction(self.act_screensaver)

        self.act_check_schedule = QAction("检查下一次切换时间", self._menu)
        if self._on_check_next_due:
            self.act_check_schedule.triggered.connect(self._on_check_next_due)
        self._menu.addAction(self.act_check_schedule)

        self._menu.addSeparator()

        self.act_quit = QAction("退出程序", self._menu)
        if self._on_quit_app:
            self.act_quit.triggered.connect(self._on_quit_app)
        self._menu.addAction(self.act_quit)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self._on_open_main:
                self._on_open_main()

    def update_from_scheduler_status(self, snapshot: SchedulerStatusSnapshot) -> None:
        """根据调度器状态动态更新“暂停/恢复轮播”菜单文本与托盘悬停提示."""
        if snapshot.paused:
            self.act_toggle_pause.setText("恢复轮播")
        else:
            self.act_toggle_pause.setText("暂停轮播")

        tooltip = (
            f"Gou Wallpaper ({snapshot.state_summary})\n"
            f"下次切换: {snapshot.next_due_at_utc or '未计划'}"
        )
        self._tray.setToolTip(tooltip)

    def show(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def show_notification(self, title: str, message: str, duration_ms: int = 3000) -> None:
        if self._tray.isVisible():
            self._tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, duration_ms)

    def show_message(self, title: str, message: str, duration_ms: int = 3000) -> None:
        """别名兼容 show_notification."""
        self.show_notification(title, message, duration_ms)


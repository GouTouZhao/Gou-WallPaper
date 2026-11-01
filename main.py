"""WallFlow 主程序入口 (`main.py`).

支持：
- 完整命令行参数：`--minimized`, `--startup`, `--next`, `--previous`, `--random`, `--apply`, `--check`, `--auto-close-ms`
- 单实例互斥保护 (`SingleInstanceGuard`) 与唤醒已有窗口
- Windows 系统消息钩子 (`QAbstractNativeEventFilter`) 实时接收 `WM_TIMECHANGE`, `WM_POWERBROADCAST`, `WM_DISPLAYCHANGE` 等
- 系统托盘与后台调度器生命周期管理
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from app.hotkeys import GlobalHotkeyManager
from app.icon_helper import ensure_ico_file, get_app_qicon
from app.image_library import ImageLibrary
from app.logger import get_logger, install_global_exception_hook, setup_logger
from app.random_selector import ImageSelector
from app.scheduler import WallpaperScheduler
from app.settings_manager import SettingsManager
from app.single_instance import SingleInstanceGuard
from app.startup_manager import StartupManager
from app.tray_manager import TrayManager
from app.ui.main_window import MainWindow
from app.win32_integration import (
    WM_DISPLAYCHANGE,
    WM_HOTKEY,
    WM_POWERBROADCAST,
    WM_SETTINGCHANGE,
    WM_TIMECHANGE,
    WM_WTSSESSION_CHANGE,
    Win32MessageDispatcher,
    enable_per_monitor_dpi_awareness,
)


class SystemNativeEventFilter(QAbstractNativeEventFilter):
    """Qt 原生 Win32 消息过滤器，捕获系统底层电源、时间、显示器插拔与全局热键消息."""

    def __init__(self, dispatcher: Win32MessageDispatcher) -> None:
        super().__init__()
        self._dispatcher = dispatcher

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if event_type == b"windows_generic_MSG" or event_type == "windows_generic_MSG":
            # 在 64 位 Windows 下，message 传入的是指向 MSG 结构体的指针整数
            try:
                import ctypes
                import ctypes.wintypes as wintypes

                class MSG(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", wintypes.HWND),
                        ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM),
                        ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD),
                        ("pt", wintypes.POINT),
                    ]

                msg_ptr = int(message)
                if msg_ptr:
                    msg_obj = MSG.from_address(msg_ptr)
                    msg_id = int(msg_obj.message)
                    if msg_id in (
                        WM_TIMECHANGE,
                        WM_DISPLAYCHANGE,
                        WM_POWERBROADCAST,
                        WM_WTSSESSION_CHANGE,
                        WM_SETTINGCHANGE,
                        WM_HOTKEY,
                    ):
                        self._dispatcher.handle_win_message(
                            msg_id=msg_id,
                            wparam=int(msg_obj.wParam),
                            lparam=int(msg_obj.lParam),
                        )
            except Exception:
                pass
        return False, 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gou Wallpaper 桌面壁纸与原生屏幕保护程序管理软件")
    parser.add_argument("--minimized", action="store_true", help="启动后最小化到托盘，不显示主窗口")
    parser.add_argument("--startup", action="store_true", help="开机自启模式，保持后台运行")
    parser.add_argument("--next", action="store_true", help="立即切换到下一张壁纸并退出")
    parser.add_argument("--previous", action="store_true", help="立即切换到上一张壁纸并退出")
    parser.add_argument("--random", action="store_true", help="立即随机切换壁纸并退出")
    parser.add_argument("--apply", action="store_true", help="立即应用当前壁纸并退出")
    parser.add_argument("--check", action="store_true", help="立即检查轮播调度并退出")
    parser.add_argument("--auto-close-ms", type=int, default=None, help="自动化测试专用：运行指定毫秒后安全退出")
    return parser.parse_args()


def main() -> int:
    enable_per_monitor_dpi_awareness()
    install_global_exception_hook()

    args = parse_arguments()
    logger = setup_logger()
    logger.info("Gou Wallpaper 正在启动... (命令行参数: %s)", sys.argv[1:])

    # 初始化配置
    settings_mgr = SettingsManager()
    cfg = settings_mgr.config

    # 单实例守护
    single_guard = SingleInstanceGuard()
    if not single_guard.acquire():
        logger.info("检测到 Gou Wallpaper 已有实例在运行")
        if args.next or args.previous or args.random or args.apply or args.check:
            # 命令行动作交由新实例直接执行或退出
            pass
        else:
            single_guard.notify_existing_instance("SHOW_WINDOW")
            logger.info("已通知运行中实例激活主窗口，本进程退出")
            return 0

    # 准备图标文件
    ensure_ico_file(Path(__file__).parent / "assets" / "icon.ico")

    # 创建 QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(get_app_qicon())

    # 全局安装滚轮事件拦截器，彻底根除所有 QComboBox、QSpinBox、QSlider 的滚轮修改与误触
    from app.ui.no_wheel_filter import install_global_no_wheel_filter
    install_global_no_wheel_filter(app)

    # 核心组件初始化
    image_lib = ImageLibrary(
        thumbnail_cache_max_mb=cfg.system.thumbnail_cache_max_mb,
        disk_cache_dir=settings_mgr.cache_dir / "thumbnails",
    )
    image_lib.set_sources(cfg.wallpaper.folders)
    # 首次启动静默快速检查元数据
    image_lib.scan()

    all_paths = image_lib.get_all_paths()
    selector = ImageSelector(candidates=all_paths, mode=cfg.wallpaper.playback_mode)
    selector.restore_from_dict(cfg.wallpaper.selector_state)

    from app.wallpaper_manager import WallpaperManager

    wp_mgr = WallpaperManager(
        settings_manager=settings_mgr,
        image_library=image_lib,
        selector=selector,
    )

    def _scheduler_apply_callback() -> tuple[bool, str]:
        res = wp_mgr.apply_next()
        return res.success, res.error_message

    scheduler = WallpaperScheduler(
        settings_manager=settings_mgr,
        apply_callback=_scheduler_apply_callback,
    )

    startup_mgr = StartupManager()
    hotkey_mgr = GlobalHotkeyManager()

    try:
        # 处理纯命令行直接切换动作
        if args.next:
            res = wp_mgr.apply_next()
            scheduler.record_manual_switch_success()
            return 0 if res.success else 1
        if args.previous:
            res = wp_mgr.apply_previous()
            scheduler.record_manual_switch_success()
            return 0 if res.success else 1
        if args.random:
            res = wp_mgr.apply_random()
            scheduler.record_manual_switch_success()
            return 0 if res.success else 1
        if args.apply:
            res = wp_mgr.apply_current_or_next()
            scheduler.record_manual_switch_success()
            return 0 if res.success else 1
        if args.check:
            ok = scheduler.check_now(reason="cli_check")
            return 0 if ok else 0

        # 创建主窗口
        main_win = MainWindow(
            settings_manager=settings_mgr,
            image_library=image_lib,
            selector=selector,
            wallpaper_manager=wp_mgr,
            scheduler=scheduler,
            startup_manager=startup_mgr,
            hotkey_manager=hotkey_mgr,
        )

        # 托盘管理器
        def _on_open_main():
            main_win.showNormal()
            main_win.raise_()
            main_win.activateWindow()

        def _on_tray_next():
            wp_mgr.apply_next()
            scheduler.record_manual_switch_success()

        def _on_tray_prev():
            wp_mgr.apply_previous()
            scheduler.record_manual_switch_success()

        def _on_tray_random():
            wp_mgr.apply_random()
            scheduler.record_manual_switch_success()

        def _on_tray_toggle_pause():
            if cfg.scheduler.paused:
                scheduler.resume()
            else:
                scheduler.pause()

        def _on_tray_screensaver():
            main_win._on_open_screensaver_config_dialog()

        def _on_tray_check_due():
            scheduler.check_now(reason="tray_check")

        tray_mgr = TrayManager(
            parent_widget=main_win,
            icon=get_app_qicon(),
            on_open_main=_on_open_main,
            on_next_wallpaper=_on_tray_next,
            on_prev_wallpaper=_on_tray_prev,
            on_random_wallpaper=_on_tray_random,
            on_toggle_pause=_on_tray_toggle_pause,
            on_open_screensaver_settings=_on_tray_screensaver,
            on_check_next_due=_on_tray_check_due,
            on_quit_app=main_win.quit_application,
        )
        main_win.set_tray_manager(tray_mgr)
        tray_mgr.show()

        # 监听二次启动激活通知
        single_guard.start_activation_listener(_on_open_main)

        # 挂载 Win32 消息过滤器
        msg_dispatcher = Win32MessageDispatcher(
            on_time_change=scheduler.on_time_change,
            on_display_change=lambda: (wp_mgr.refresh_monitors(), scheduler.on_system_resume()),
            on_power_resume=lambda _: scheduler.on_system_resume(),
            on_session_unlock=scheduler.on_system_resume,
            on_hotkey=hotkey_mgr.handle_hotkey_id,
        )
        event_filter = SystemNativeEventFilter(msg_dispatcher)
        app.installNativeEventFilter(event_filter)

        # 启动后台调度器检查线程
        scheduler.start_background_worker()

        # 判断是否最小化启动
        start_minimized = args.minimized or args.startup or cfg.system.start_minimized
        if not start_minimized:
            main_win.show()
        else:
            logger.info("已按最小化模式启动，保持托盘后台轮播")
            main_win._on_enter_background_mode()

        # 自动化测试定时退出支持
        if args.auto_close_ms is not None and args.auto_close_ms > 0:
            QTimer.singleShot(int(args.auto_close_ms), main_win.quit_application)

        return app.exec()
    finally:
        single_guard.release()


if __name__ == "__main__":
    sys.exit(main())


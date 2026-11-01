"""Windows 原生屏幕保护程序独立入口 (`screensaver_entry.py`).

通过 PyInstaller 打包生成 `dist/WallFlowSaver.scr`。
完整处理 Windows 屏保系统的标准调用模式：
- `/s` 或 `-s`：全屏启动屏保
- `/p <hwnd>` 或 `/p:<hwnd>`：嵌入 Windows 屏保设置预览框
- `/c` 或 `/c:<hwnd>` 或无参数：打开屏保配置对话框
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.icon_helper import ensure_ico_file, get_app_qicon
from app.logger import install_global_exception_hook, setup_logger
from app.screensaver import ScreensaverController, parse_screensaver_args
from app.settings_manager import SettingsManager
from app.ui.screensaver_config_dialog import ScreensaverConfigDialog
from app.win32_integration import enable_per_monitor_dpi_awareness


def main() -> int:
    enable_per_monitor_dpi_awareness()
    install_global_exception_hook()

    logger = setup_logger(log_filename="wallflow_screensaver.log")
    cmd = parse_screensaver_args(sys.argv[1:])
    logger.info("GouWallpaperSaver 启动: raw_args=%s, mode=%s, preview_hwnd=%s", sys.argv[1:], cmd.mode, cmd.preview_hwnd)

    if cmd.mode == "screensaver":
        from app.screensaver import ScreensaverMutexGuard
        if ScreensaverMutexGuard.is_active():
            logger.info("检测到已有 Gou Wallpaper 屏保正在运行，极速退出以保证全局唯一且不干扰显示")
            return 0

        # 系统级别智能状态识别：若检测到正在播放音频/视频 (如抖音/影视)、全屏游戏或演示模式，智能退出
        try:
            from app.win32_integration import check_system_media_and_busy_state
            ss_cfg = SettingsManager().config.screensaver
            suppress_m = getattr(ss_cfg, "suppress_when_media_playing", True)
            suppress_f = getattr(ss_cfg, "suppress_when_fullscreen", True)
            busy_state = check_system_media_and_busy_state(
                suppress_media=suppress_m,
                suppress_fullscreen=suppress_f,
            )
            if busy_state.is_busy:
                logger.info("系统正处于多媒体播放或专注忙碌状态 (%s)，抑制本次屏保启动", busy_state.reason)
                return 0
        except Exception as exc:
            logger.debug("屏保启动前系统状态检测异常: %s", exc)

    ensure_ico_file(Path(__file__).parent / "assets" / "icon.ico")
    app = QApplication.instance() or QApplication(sys.argv)
    app.setWindowIcon(get_app_qicon())

    from app.ui.no_wheel_filter import install_global_no_wheel_filter
    install_global_no_wheel_filter(app)

    settings_mgr = SettingsManager()

    if cmd.mode == "config":
        dialog = ScreensaverConfigDialog(
            settings_manager=settings_mgr,
            auto_close_ms=cmd.auto_close_ms,
        )
        dialog.exec()
        return 0

    def _safe_screensaver_exit() -> None:
        try:
            from app.screensaver import ScreensaverMutexGuard
            ScreensaverMutexGuard.release()
        except Exception:
            pass
        try:
            app.quit()
        except Exception:
            pass
        # 40ms 后无条件强行终止进程，防止任何底层 COM 或线程阻塞导致屏保挂在后台
        QTimer.singleShot(40, lambda: os._exit(0))

    controller = ScreensaverController(
        settings_manager=settings_mgr,
        on_closed=_safe_screensaver_exit,
    )

    if cmd.mode == "preview":
        if cmd.preview_hwnd is None or cmd.preview_hwnd <= 0:
            logger.warning("预览模式缺少有效的 preview_hwnd，退出")
            return 0
        controller.start_preview(cmd.preview_hwnd, auto_close_ms=cmd.auto_close_ms)
        return app.exec()

    # mode == "screensaver" 全屏模式
    from app.screensaver import ScreensaverMutexGuard
    if not ScreensaverMutexGuard.acquire():
        logger.info("检测到已有 Gou Wallpaper 屏保正在运行，退出当前启动实例保证全局唯一")
        return 0

    started = controller.start_fullscreen(auto_close_ms=cmd.auto_close_ms)
    if not started:
        ScreensaverMutexGuard.release()
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())


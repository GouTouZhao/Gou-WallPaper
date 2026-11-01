"""开机自启、Win32 消息分发与多显示器拓扑测试 (`tests/test_startup_and_win32.py`)."""

from __future__ import annotations

from pathlib import Path

from app.startup_manager import build_startup_command
from app.win32_integration import (
    MonitorInfo,
    WM_DISPLAYCHANGE,
    WM_POWERBROADCAST,
    WM_TIMECHANGE,
    Win32MessageDispatcher,
    compute_virtual_desktop_bounds,
)


def test_build_startup_command_escaping():
    cmd = build_startup_command(
        executable_path=r"C:\Program Files (x86)\壁纸 工具\WallFlow.exe",
        minimized=True,
    )
    # 必须包裹双引号并带有 --minimized 参数
    assert cmd.startswith('"')
    assert r'C:\Program Files (x86)\壁纸 工具\WallFlow.exe"' in cmd
    assert cmd.endswith("--minimized")


def test_virtual_desktop_bounds_calculation():
    # 模拟双显示器拓扑：主屏 1920x1080 @ (0, 0)，副屏 2560x1440 @ (1920, -360)
    mon1 = MonitorInfo(index=0, device_name=r"\\.\DISPLAY1", device_id="D1", x=0, y=0, width=1920, height=1080, is_primary=True)
    mon2 = MonitorInfo(index=1, device_name=r"\\.\DISPLAY2", device_id="D2", x=1920, y=-360, width=2560, height=1440, is_primary=False)

    min_x, min_y, total_w, total_h = compute_virtual_desktop_bounds([mon1, mon2])
    assert min_x == 0
    assert min_y == -360
    assert total_w == 1920 + 2560
    assert total_h == 1080 - (-360)  # 1440


def test_win32_message_dispatcher():
    time_changed = False
    display_changed = False
    power_resumed = False

    def on_time():
        nonlocal time_changed
        time_changed = True

    def on_display():
        nonlocal display_changed
        display_changed = True

    def on_power(wparam):
        nonlocal power_resumed
        power_resumed = True

    dispatcher = Win32MessageDispatcher(
        on_time_change=on_time,
        on_display_change=on_display,
        on_power_resume=on_power,
    )

    assert dispatcher.handle_win_message(WM_TIMECHANGE) is True
    assert time_changed is True

    assert dispatcher.handle_win_message(WM_DISPLAYCHANGE) is True
    assert display_changed is True

    # PBT_APMRESUMEAUTOMATIC = 0x0012
    assert dispatcher.handle_win_message(WM_POWERBROADCAST, wparam=0x0012) is True
    assert power_resumed is True


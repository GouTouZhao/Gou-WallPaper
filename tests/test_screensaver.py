"""屏幕保护程序核心与参数解析测试 (`tests/test_screensaver.py`)."""

from __future__ import annotations

from pathlib import Path

from app.screensaver import parse_screensaver_args


def test_parse_screensaver_args_fullscreen():
    cmd1 = parse_screensaver_args(["/s"])
    assert cmd1.mode == "screensaver"

    cmd2 = parse_screensaver_args(["/S"])
    assert cmd2.mode == "screensaver"

    cmd3 = parse_screensaver_args(["-s", "--auto-close-ms=1500"])
    assert cmd3.mode == "screensaver"
    assert cmd3.auto_close_ms == 1500


def test_parse_screensaver_args_config():
    # 默认无参数 -> 打开配置
    cmd1 = parse_screensaver_args([])
    assert cmd1.mode == "config"

    cmd2 = parse_screensaver_args(["/c"])
    assert cmd2.mode == "config"

    # 带父窗口句柄
    cmd3 = parse_screensaver_args(["/c:65536"])
    assert cmd3.mode == "config"
    assert cmd3.parent_hwnd == 65536

    cmd4 = parse_screensaver_args(["/c", "99999"])
    assert cmd4.mode == "config"
    assert cmd4.parent_hwnd == 99999


def test_parse_screensaver_args_preview():
    # Windows 预览框调用：/p 123456 或 /p:123456
    cmd1 = parse_screensaver_args(["/p", "123456"])
    assert cmd1.mode == "preview"
    assert cmd1.preview_hwnd == 123456

    cmd2 = parse_screensaver_args(["/p:789012"])
    assert cmd2.mode == "preview"
    assert cmd2.preview_hwnd == 789012

    cmd3 = parse_screensaver_args(["-P:55555", "--auto-close-ms", "800"])
    assert cmd3.mode == "preview"
    assert cmd3.preview_hwnd == 55555
    assert cmd3.auto_close_ms == 800


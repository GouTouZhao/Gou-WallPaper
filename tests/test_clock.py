"""可替换时钟抽象单元测试 (`tests/test_clock.py`)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.clock import (
    SystemClock,
    VirtualClock,
    format_utc_iso,
    parse_utc_iso,
)


def test_system_clock_returns_timezone_aware_utc():
    clk = SystemClock()
    now = clk.now_utc()
    assert now.tzinfo is not None
    assert now.tzinfo == timezone.utc
    mono = clk.monotonic()
    assert isinstance(mono, float)
    assert mono > 0


def test_virtual_clock_advance():
    start = datetime(2026, 10, 29, 8, 0, 0, tzinfo=timezone.utc)
    vclk = VirtualClock(initial_utc=start, initial_monotonic=500.0)

    assert vclk.now_utc() == start
    assert vclk.monotonic() == 500.0

    vclk.advance(3600.0)  # 前进 1 小时
    assert vclk.monotonic() == 4100.0
    assert vclk.now_utc().hour == 9


def test_virtual_clock_simulate_sleep():
    start = datetime(2026, 10, 29, 0, 0, 0, tzinfo=timezone.utc)
    vclk = VirtualClock(initial_utc=start, initial_monotonic=100.0)

    # 模拟电脑休眠 8 小时 (28800 秒)，单调时钟被冻结
    vclk.simulate_sleep(28800.0, freeze_monotonic=True)
    assert vclk.now_utc().hour == 8
    assert vclk.monotonic() == 100.0


def test_virtual_clock_shift_system_clock():
    start = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
    vclk = VirtualClock(initial_utc=start, initial_monotonic=100.0)

    # 模拟系统时钟被向后回拨 2 小时
    vclk.shift_system_clock(-7200.0)
    assert vclk.now_utc().hour == 10
    assert vclk.monotonic() == 100.0


def test_utc_iso_serialization_roundtrip():
    dt = datetime(2026, 10, 29, 15, 30, 45, tzinfo=timezone.utc)
    iso_str = format_utc_iso(dt)
    assert "+00:00" in iso_str or iso_str.endswith("Z")

    parsed = parse_utc_iso(iso_str)
    assert parsed is not None
    assert parsed.year == 2026
    assert parsed.hour == 15
    assert parsed.tzinfo == timezone.utc


def test_parse_utc_iso_edge_cases():
    assert parse_utc_iso("") is None
    assert parse_utc_iso(None) is None
    assert parse_utc_iso("invalid-date-string") is None
    # 兼容尾部 Z
    parsed_z = parse_utc_iso("2026-10-29T12:00:00Z")
    assert parsed_z is not None
    assert parsed_z.hour == 12


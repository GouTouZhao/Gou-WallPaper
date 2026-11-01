"""长间隔与短间隔壁纸轮播调度系统专项自动化测试 (`tests/test_scheduler.py`).

严格验证需求 12.3 核心场景表格：
1. 间隔 5 小时 59 分 (21540s) -> 采用短间隔单调时钟调度
2. 间隔 6 小时 (21600s) -> 采用系统时间戳调度 (timestamp_utc)
3. 间隔 12 小时 (43200s) -> 采用系统时间戳调度
4. 间隔 24 小时 (86400s) -> 采用系统时间戳调度
5. 计划时间到达前重启 -> 保持等待，不提前切换
6. 计划时间已过后启动 -> 执行且仅执行一次补偿切换，更新计划时间
7. 系统睡眠/休眠期间错过时间 -> 唤醒后立即检查真实系统时间并单次补偿
8. 系统时间向前或向后调整 -> 重新校准避免长久挂起
9. 壁纸应用失败 -> 不伪造成功状态，有限退避重试，下次继续调度
10. 修改切换间隔 -> 实时更新模式并持久化下一次计划时间
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.clock import VirtualClock, format_utc_iso, parse_utc_iso
from app.scheduler import (
    LONG_INTERVAL_THRESHOLD_SECONDS,
    MODE_MONOTONIC,
    MODE_TIMESTAMP_UTC,
    WallpaperScheduler,
)
from app.settings_manager import SettingsManager


def test_scheduler_strategy_switching_at_6_hours(temp_dir: Path, virtual_clock: VirtualClock):
    """场景：5 小时 59 分 vs 6 小时调度模式严格分流."""
    settings = SettingsManager(config_dir=temp_dir)

    apply_count = 0

    def mock_apply():
        nonlocal apply_count
        apply_count += 1
        return True, ""

    # 1. 设置 5 小时 59 分 (21540 秒)
    scheduler = WallpaperScheduler(settings, mock_apply, clock=virtual_clock)
    scheduler.set_interval(21540)
    assert scheduler.scheduler_mode == MODE_MONOTONIC
    assert settings.config.scheduler.scheduler_mode == MODE_MONOTONIC

    # 2. 切换到 6 小时 (21600 秒)
    scheduler.set_interval(LONG_INTERVAL_THRESHOLD_SECONDS)
    assert scheduler.scheduler_mode == MODE_TIMESTAMP_UTC
    assert settings.config.scheduler.scheduler_mode == MODE_TIMESTAMP_UTC

    # 3. 切换到 12 小时 (43200 秒)
    scheduler.set_interval(43200)
    assert scheduler.scheduler_mode == MODE_TIMESTAMP_UTC

    # 4. 切换到 24 小时 (86400 秒)
    scheduler.set_interval(86400)
    assert scheduler.scheduler_mode == MODE_TIMESTAMP_UTC


def test_monotonic_short_interval_execution(temp_dir: Path, virtual_clock: VirtualClock):
    """短间隔单调时钟推进切换测试."""
    settings = SettingsManager(config_dir=temp_dir)
    apply_count = 0

    def mock_apply():
        nonlocal apply_count
        apply_count += 1
        return True, ""

    scheduler = WallpaperScheduler(settings, mock_apply, clock=virtual_clock)
    scheduler.set_interval(300)  # 5 分钟
    assert scheduler.scheduler_mode == MODE_MONOTONIC

    # 前进 100 秒，未到期
    virtual_clock.advance(100.0)
    assert scheduler.check_now("test") is False
    assert apply_count == 0

    # 再前进 210 秒（累计 310 秒 > 300 秒），触发切换
    virtual_clock.advance(210.0)
    assert scheduler.check_now("test") is True
    assert apply_count == 1
    assert settings.config.scheduler.last_success_at_utc != ""


def test_restart_before_due_time_keeps_waiting(temp_dir: Path, virtual_clock: VirtualClock):
    """长间隔（6小时）：程序在计划时间之前重启，保持持久化时间，不提前切换."""
    settings = SettingsManager(config_dir=temp_dir)
    apply_count = 0

    def mock_apply():
        nonlocal apply_count
        apply_count += 1
        return True, ""

    scheduler1 = WallpaperScheduler(settings, mock_apply, clock=virtual_clock)
    scheduler1.set_interval(21600)  # 6 小时
    due_str_1 = settings.config.scheduler.next_due_at_utc

    # 推进 2 小时 (7200 秒)
    virtual_clock.advance(7200.0)

    # 模拟程序退出，创建新实例（模拟重启）
    settings_reloaded = SettingsManager(config_dir=temp_dir)
    scheduler2 = WallpaperScheduler(settings_reloaded, mock_apply, clock=virtual_clock)
    scheduler2.on_startup_check()

    # 预期：未到期，不触发切换，保持原有到期时间
    assert apply_count == 0
    assert settings_reloaded.config.scheduler.next_due_at_utc == due_str_1
    snap = scheduler2.get_status_snapshot()
    assert snap.remaining_seconds > 0


def test_restart_after_due_time_triggers_single_compensatory_switch(temp_dir: Path, virtual_clock: VirtualClock):
    """长间隔（12小时）：关机数天或越过计划时间后重启，执行且仅执行一次补偿切换."""
    settings = SettingsManager(config_dir=temp_dir)
    apply_count = 0

    def mock_apply():
        nonlocal apply_count
        apply_count += 1
        return True, ""

    scheduler1 = WallpaperScheduler(settings, mock_apply, clock=virtual_clock)
    scheduler1.set_interval(43200)  # 12 小时

    # 模拟电脑关机 36 小时（越过了 3 个周期）
    virtual_clock.advance(36 * 3600.0)

    # 重启加载
    settings_reloaded = SettingsManager(config_dir=temp_dir)
    scheduler2 = WallpaperScheduler(settings_reloaded, mock_apply, clock=virtual_clock)
    ok = scheduler2.on_startup_check()

    # 预期：只补偿切换 1 次，绝不连续闪屏执行 3 次
    assert ok is True
    assert apply_count == 1

    # 下一次计划时间应从本次实际切换时间顺延 12 小时
    now_utc = virtual_clock.now_utc()
    next_due_dt = parse_utc_iso(settings_reloaded.config.scheduler.next_due_at_utc)
    expected_due = now_utc + timedelta(seconds=43200)
    assert abs((next_due_dt - expected_due).total_seconds()) < 2.0


def test_sleep_simulation_misses_due_time_and_recovers_on_wake(temp_dir: Path, virtual_clock: VirtualClock):
    """模拟休眠冻结单调时钟，唤醒后通过 UTC 时间戳识别并补偿切换."""
    settings = SettingsManager(config_dir=temp_dir)
    apply_count = 0

    def mock_apply():
        nonlocal apply_count
        apply_count += 1
        return True, ""

    scheduler = WallpaperScheduler(settings, mock_apply, clock=virtual_clock)
    scheduler.set_interval(21600)  # 6 小时

    # 模拟电脑休眠 8 小时 (单调时钟被挂起)
    virtual_clock.simulate_sleep(8 * 3600.0, freeze_monotonic=True)

    # 唤醒事件
    ok = scheduler.on_system_resume()
    assert ok is True
    assert apply_count == 1
    assert settings.config.scheduler.consecutive_failures == 0


def test_short_interval_sleep_drift_compensation(temp_dir: Path, virtual_clock: VirtualClock):
    """短间隔（如 30 分钟）在休眠 2 小时后唤醒，也能检测真实时间并补偿切换."""
    settings = SettingsManager(config_dir=temp_dir)
    apply_count = 0

    def mock_apply():
        nonlocal apply_count
        apply_count += 1
        return True, ""

    scheduler = WallpaperScheduler(settings, mock_apply, clock=virtual_clock)
    scheduler.set_interval(1800)  # 30 分钟

    # 睡眠 2 小时 (单调时钟冻结)
    virtual_clock.simulate_sleep(7200.0, freeze_monotonic=True)

    # 收到唤醒恢复信号
    ok = scheduler.on_system_resume()
    assert ok is True
    assert apply_count == 1


def test_system_clock_backwards_shift_recalibrates(temp_dir: Path, virtual_clock: VirtualClock):
    """系统时间被大幅往回调整时，自动校准 next_due，防止永久挂起."""
    settings = SettingsManager(config_dir=temp_dir)
    scheduler = WallpaperScheduler(settings, lambda: (True, ""), clock=virtual_clock)
    scheduler.set_interval(21600)  # 6 小时

    # 模拟用户将系统时钟往前拨了 30 天 (负向回拨)
    virtual_clock.shift_system_clock(-30 * 86400.0)

    scheduler.on_time_change()
    now_utc = virtual_clock.now_utc()
    next_due_dt = parse_utc_iso(settings.config.scheduler.next_due_at_utc)

    # 重新校准为当前真实时间的 6 小时之后，不至于等待 30 天
    diff = (next_due_dt - now_utc).total_seconds()
    assert 21590 <= diff <= 21610


def test_apply_failure_does_not_fake_success_and_retries_with_backoff(temp_dir: Path, virtual_clock: VirtualClock):
    """壁纸应用失败测试：不更新 last_success_at_utc，记录错误并递增失败计数."""
    settings = SettingsManager(config_dir=temp_dir)
    should_fail = True

    def mock_apply():
        if should_fail:
            return False, "模拟 Windows API 报错: 0x80070005 拒绝访问"
        return True, ""

    scheduler = WallpaperScheduler(settings, mock_apply, clock=virtual_clock)
    scheduler.set_interval(21600)

    # 推进 6 小时触发检查
    virtual_clock.advance(21605.0)
    ok1 = scheduler.check_now("test_fail")
    assert ok1 is False

    # 核心断言：未伪造成功时间
    assert settings.config.scheduler.last_success_at_utc == ""
    assert settings.config.scheduler.consecutive_failures == 1
    assert "0x80070005" in settings.config.scheduler.last_error

    # 处于退避期内再次检查，不立即重复报错
    virtual_clock.advance(5.0)
    assert scheduler.check_now("too_fast") is False

    # 退避时间结束，修复错误后重试成功
    virtual_clock.advance(30.0)
    should_fail = False
    ok2 = scheduler.check_now("retry")
    assert ok2 is True
    assert settings.config.scheduler.consecutive_failures == 0
    assert settings.config.scheduler.last_success_at_utc != ""


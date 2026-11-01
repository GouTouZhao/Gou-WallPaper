"""可替换时钟抽象模块 (`app/clock.py`).

提供生产环境 `SystemClock` 与自动化测试专用的 `VirtualClock`，
支持单调时钟 (`monotonic`) 与带时区的 UTC 时间 (`now_utc`) 解耦测试。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """时间源抽象接口."""

    def now_utc(self) -> datetime:
        """返回带 UTC 时区的当前系统时间 (`timezone.utc`)."""
        ...

    def monotonic(self) -> float:
        """返回单调递增时钟秒数."""
        ...


class SystemClock:
    """生产环境真实系统时钟."""

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


class VirtualClock:
    """可编程虚拟时钟，用于快速测试 6h/12h/24h 长间隔调度、系统休眠与系统时间跳变."""

    def __init__(
        self,
        initial_utc: datetime | None = None,
        initial_monotonic: float = 1000.0,
    ) -> None:
        if initial_utc is None:
            initial_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
        elif initial_utc.tzinfo is None:
            initial_utc = initial_utc.replace(tzinfo=timezone.utc)
        else:
            initial_utc = initial_utc.astimezone(timezone.utc)
        self._utc: datetime = initial_utc
        self._monotonic: float = float(initial_monotonic)

    def now_utc(self) -> datetime:
        return self._utc

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        """正常流逝指定秒数（同时推进 UTC 时间和单调时钟）."""
        self._utc = self._utc + timedelta(seconds=seconds)
        self._monotonic += float(seconds)

    def simulate_sleep(self, seconds: float, freeze_monotonic: bool = True) -> None:
        """模拟电脑进入睡眠/休眠状态.

        在 Windows 休眠/深睡眠期间，真实 UTC 时间照常流逝，
        而单调计时器可能暂停或未触发回调。
        """
        self._utc = self._utc + timedelta(seconds=seconds)
        if not freeze_monotonic:
            self._monotonic += float(seconds)

    def set_utc(self, new_utc: datetime) -> None:
        """模拟用户或 NTP 手动修改系统墙上时钟（单调时钟不变）."""
        if new_utc.tzinfo is None:
            new_utc = new_utc.replace(tzinfo=timezone.utc)
        else:
            new_utc = new_utc.astimezone(timezone.utc)
        self._utc = new_utc

    def shift_system_clock(self, delta_seconds: float) -> None:
        """模拟系统时间向前（正数）或向后回拨（负数）调整，单调时钟保持不变."""
        self._utc = self._utc + timedelta(seconds=delta_seconds)


def format_utc_iso(dt: datetime | None) -> str:
    """将 datetime 格式化为带 UTC 时区的 ISO-8601 字符串."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def parse_utc_iso(value: str | None) -> datetime | None:
    """解析带时区的 ISO-8601 时间字符串并统一转换为 UTC 时区.

    如果传入的字符串不含时区信息或格式非法，安全处理或返回 None。
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            # 兼容迁移：若旧数据无时区，视为 UTC
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


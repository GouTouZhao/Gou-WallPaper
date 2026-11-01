"""可靠的壁纸轮播双模式调度器模块 (`app/scheduler.py`).

核心调度架构规范：
- 阈值：`LONG_INTERVAL_THRESHOLD_SECONDS = 21600` (6 小时)
- 规则 A (`interval_seconds < 21600`)：使用基于单调时钟 (`Clock.monotonic()`) 的短间隔调度，并结合睡眠恢复真实时间漂移补偿。
- 规则 B (`interval_seconds >= 21600`)：使用持久化的带时区 UTC 系统时间戳 (`last_success_at_utc`, `next_due_at_utc`) 调度。
  - 每次检查均读取真实 `clock.now_utc()` 并判断 `now_utc >= next_due_at_utc`。
  - 绝不通过累加轮询次数推算时间，也绝不使用 6h/12h/24h 一次性长计时器作为唯一调度机制。
- 只有在壁纸实际应用成功之后才更新 `last_success_at_utc` 与 `next_due_at_utc`。
- 支持开机/重启检查、休眠恢复补偿（只补偿一次，不连续闪屏补放）、系统时间/时区跳变自愈、以及失败有限退避重试。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from app.clock import Clock, SystemClock, format_utc_iso, parse_utc_iso
from app.logger import get_logger
from app.settings_manager import SettingsManager

LONG_INTERVAL_THRESHOLD_SECONDS = 6 * 60 * 60  # 21600 秒 (6 小时)

MODE_MONOTONIC = "monotonic"
MODE_TIMESTAMP_UTC = "timestamp_utc"


@dataclass(frozen=True)
class SchedulerStatusSnapshot:
    """供主界面和托盘展示的调度器实时状态快照."""

    enabled: bool
    paused: bool
    interval_seconds: int
    scheduler_mode: str  # "monotonic" | "timestamp_utc"
    scheduler_mode_label: str  # "短间隔单调时钟调度" | "系统时间戳调度"
    last_success_at_utc: str
    next_due_at_utc: str
    last_check_at_utc: str
    remaining_seconds: float
    consecutive_failures: int
    last_error: str
    state_summary: str
    is_switching: bool = False

    @property
    def remaining_formatted(self) -> str:
        if self.paused:
            return "已暂停"
        if not self.enabled:
            return "已禁用"
        if self.is_switching:
            return "正在切换..."
        if self.remaining_seconds <= 0:
            return "即将切换..."
        secs = int(round(self.remaining_seconds))
        h = secs // 3600
        m = (secs % 3600) // 60
        s = secs % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @property
    def next_due_formatted(self) -> str:
        if not self.next_due_at_utc:
            return "-"
        try:
            dt = parse_utc_iso(self.next_due_at_utc)
            local_dt = dt.astimezone()
            return local_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return self.next_due_at_utc

    @property
    def last_success_formatted(self) -> str:
        if not self.last_success_at_utc:
            return "尚未切换"
        try:
            dt = parse_utc_iso(self.last_success_at_utc)
            local_dt = dt.astimezone()
            return local_dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return self.last_success_at_utc

    @property
    def is_paused(self) -> bool:
        return self.paused

    @property
    def is_enabled(self) -> bool:
        return self.enabled

    @property
    def mode(self) -> str:
        return "timestamp" if self.scheduler_mode == MODE_TIMESTAMP_UTC else "monotonic"

    @property
    def failure_count(self) -> int:
        return self.consecutive_failures


class WallpaperScheduler:
    """支持可注入 `Clock` 的双模式可靠壁纸调度器."""

    def __init__(
        self,
        settings_manager: SettingsManager,
        apply_callback: Callable[[], tuple[bool, str]],
        clock: Clock | None = None,
    ) -> None:
        self._logger = get_logger("scheduler")
        self._settings = settings_manager
        self._apply_callback = apply_callback
        self._clock: Clock = clock or SystemClock()

        self._lock = threading.RLock()
        self._apply_in_progress = False

        # 短间隔单调时钟状态
        self._monotonic_last_anchor: float | None = None
        self._monotonic_next_due: float | None = None

        # 失败退避重试截止时间 (UTC)
        self._retry_backoff_until_utc: datetime | None = None

        # 状态变更监听器
        self._status_listeners: list[Callable[[SchedulerStatusSnapshot], None]] = []

        # 后台轻量轮询线程控制（不依赖主窗口存活）
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

        self._initialize_schedule_state()

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def scheduler_mode(self) -> str:
        with self._lock:
            interval = int(self._settings.config.scheduler.interval_seconds)
            return MODE_TIMESTAMP_UTC if interval >= LONG_INTERVAL_THRESHOLD_SECONDS else MODE_MONOTONIC

    def add_status_listener(self, callback: Callable[[SchedulerStatusSnapshot], None]) -> None:
        with self._lock:
            if callback not in self._status_listeners:
                self._status_listeners.append(callback)

    def _initialize_schedule_state(self) -> None:
        """初始化或恢复调度状态."""
        with self._lock:
            sched = self._settings.config.scheduler
            interval = max(5, int(sched.interval_seconds))
            sched.interval_seconds = interval
            sched.scheduler_mode = (
                MODE_TIMESTAMP_UTC if interval >= LONG_INTERVAL_THRESHOLD_SECONDS else MODE_MONOTONIC
            )

            now_utc = self._clock.now_utc()
            now_mono = self._clock.monotonic()

            next_due_dt = parse_utc_iso(sched.next_due_at_utc)
            last_success_dt = parse_utc_iso(sched.last_success_at_utc)

            if sched.scheduler_mode == MODE_TIMESTAMP_UTC:
                # 长间隔模式：若之前从未持久化过 next_due_at_utc，则根据 last_success_at_utc 或当前时间初始化
                if next_due_dt is None:
                    base_dt = last_success_dt if last_success_dt is not None else now_utc
                    next_due_dt = base_dt + timedelta(seconds=interval)
                    sched.next_due_at_utc = format_utc_iso(next_due_dt)
                    self._settings.save()
                self._monotonic_last_anchor = now_mono
                self._monotonic_next_due = None
            else:
                # 短间隔模式：建立单调时钟基准，同时维护 next_due_at_utc 供 UI 展示和休眠恢复参考
                self._monotonic_last_anchor = now_mono
                if next_due_dt is not None and next_due_dt <= now_utc:
                    # 计划时间已过，允许启动检查立即补偿
                    self._monotonic_next_due = now_mono
                elif next_due_dt is not None:
                    remaining = max(0.0, (next_due_dt - now_utc).total_seconds())
                    if remaining <= interval:
                        self._monotonic_next_due = now_mono + remaining
                    else:
                        self._monotonic_next_due = now_mono + interval
                        sched.next_due_at_utc = format_utc_iso(now_utc + timedelta(seconds=interval))
                else:
                    self._monotonic_next_due = now_mono + interval
                    sched.next_due_at_utc = format_utc_iso(now_utc + timedelta(seconds=interval))
                self._settings.save()

    def start_background_worker(self) -> None:
        """启动后台轻量级检查线程（短间隔每 1 秒检查一次，长间隔每 15 秒检查一次，支持随时唤醒）."""
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._stop_event.clear()
            self._wake_event.clear()
            self._worker_thread = threading.Thread(
                target=self._background_loop,
                name="WallFlowSchedulerWorker",
                daemon=True,
            )
            self._worker_thread.start()
            self._logger.info(
                "后台调度器已启动: mode=%s, interval=%ds, next_due=%s",
                self.scheduler_mode,
                self._settings.config.scheduler.interval_seconds,
                self._settings.config.scheduler.next_due_at_utc,
            )

    def stop(self) -> None:
        """停止调度器后台工作线程 (别名)."""
        self.stop_background_worker()

    def stop_background_worker(self) -> None:
        """停止后台检查线程并清理资源."""
        self._stop_event.set()
        self._wake_event.set()
        thread = self._worker_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._worker_thread = None
        self._logger.info("后台调度器已停止")

    def _background_loop(self) -> None:
        # 首次启动时执行一次恢复检查
        try:
            self.on_startup_check()
        except Exception as exc:
            self._logger.error("启动恢复检查异常: %s", exc)

        while not self._stop_event.is_set():
            wait_sec = 15.0 if self.scheduler_mode == MODE_TIMESTAMP_UTC else 1.0
            self._wake_event.wait(timeout=wait_sec)
            self._wake_event.clear()
            if self._stop_event.is_set():
                break
            try:
                self.check_now(reason="periodic_poll")
            except Exception as exc:
                self._logger.error("定时调度检查异常: %s", exc)

    def on_startup_check(self) -> bool:
        """程序启动或重启时的恢复检查.

        若持久化的 `next_due_at_utc` 已经过去，立即执行一次补偿切换。
        """
        self._logger.info("执行程序启动调度恢复检查...")
        return self.check_now(reason="startup_recovery")

    def on_system_resume(self) -> bool:
        """系统从睡眠/休眠或会话锁定中恢复时调用.

        主动重新读取真实系统时间，处理休眠期间错过的计划时间。
        """
        self._logger.info("检测到系统从睡眠/休眠恢复，立即检查真实系统时间...")
        with self._lock:
            sched = self._settings.config.scheduler
            now_utc = self._clock.now_utc()
            now_mono = self._clock.monotonic()

            if sched.scheduler_mode == MODE_MONOTONIC:
                # 短间隔休眠补偿：若真实 UTC 时间距上次成功切换或超出了 next_due_at_utc，将单调到期时间设为当前
                next_due_dt = parse_utc_iso(sched.next_due_at_utc)
                last_success_dt = parse_utc_iso(sched.last_success_at_utc)
                if next_due_dt is not None and now_utc >= next_due_dt:
                    self._monotonic_next_due = now_mono
                elif (
                    last_success_dt is not None
                    and (now_utc - last_success_dt).total_seconds() >= sched.interval_seconds
                ):
                    self._monotonic_next_due = now_mono
                elif next_due_dt is not None:
                    rem = max(0.0, (next_due_dt - now_utc).total_seconds())
                    self._monotonic_next_due = now_mono + min(float(sched.interval_seconds), rem)

        return self.check_now(reason="system_resume")

    def on_time_change(self) -> bool:
        """当收到 `WM_TIMECHANGE`（系统时间或时区变更）时调用."""
        self._logger.info("检测到系统时间或时区变更，开始校准调度计划时间...")
        with self._lock:
            sched = self._settings.config.scheduler
            now_utc = self._clock.now_utc()
            next_due_dt = parse_utc_iso(sched.next_due_at_utc)
            interval = sched.interval_seconds

            # 若系统时间被大幅往回拨（导致 next_due 距离现在超过 1 个完整周期），重新校准 next_due
            if next_due_dt is not None and (next_due_dt - now_utc).total_seconds() > interval:
                recalibrated = now_utc + timedelta(seconds=interval)
                sched.next_due_at_utc = format_utc_iso(recalibrated)
                self._settings.save()
                self._logger.warning(
                    "检测到系统时间向后回拨，已重新校准 next_due_at_utc 为 %s",
                    sched.next_due_at_utc,
                )

        return self.check_now(reason="time_change")

    def set_interval(self, interval_seconds: int) -> None:
        """修改切换间隔，自动在短间隔单调时钟模式与长间隔系统时间戳模式之间切换并持久化."""
        interval = max(5, int(interval_seconds))
        with self._lock:
            sched = self._settings.config.scheduler
            old_mode = sched.scheduler_mode
            sched.interval_seconds = interval
            new_mode = MODE_TIMESTAMP_UTC if interval >= LONG_INTERVAL_THRESHOLD_SECONDS else MODE_MONOTONIC
            sched.scheduler_mode = new_mode

            now_utc = self._clock.now_utc()
            now_mono = self._clock.monotonic()

            last_success_dt = parse_utc_iso(sched.last_success_at_utc)
            if last_success_dt is not None and last_success_dt <= now_utc:
                candidate_due = last_success_dt + timedelta(seconds=interval)
                if candidate_due <= now_utc:
                    next_due_dt = now_utc + timedelta(seconds=interval)
                else:
                    next_due_dt = candidate_due
            else:
                next_due_dt = now_utc + timedelta(seconds=interval)

            sched.next_due_at_utc = format_utc_iso(next_due_dt)
            rem_seconds = max(0.0, (next_due_dt - now_utc).total_seconds())
            self._monotonic_last_anchor = now_mono
            self._monotonic_next_due = now_mono + rem_seconds if new_mode == MODE_MONOTONIC else None
            self._retry_backoff_until_utc = None

            self._settings.save()
            self._logger.info(
                "已更新轮播间隔为 %d 秒 (模式: %s -> %s, 下次切换: %s)",
                interval,
                old_mode,
                new_mode,
                sched.next_due_at_utc,
            )
            self._wake_event.set()
        self._notify_status()

    def pause(self) -> None:
        with self._lock:
            self._settings.config.scheduler.paused = True
            self._settings.save()
            self._logger.info("已暂停自动壁纸轮播")
        self._notify_status()

    def resume(self) -> None:
        with self._lock:
            sched = self._settings.config.scheduler
            sched.paused = False
            now_utc = self._clock.now_utc()
            now_mono = self._clock.monotonic()
            next_due_dt = parse_utc_iso(sched.next_due_at_utc)
            if sched.scheduler_mode == MODE_MONOTONIC:
                if next_due_dt is not None and next_due_dt > now_utc:
                    self._monotonic_next_due = now_mono + (next_due_dt - now_utc).total_seconds()
                else:
                    self._monotonic_next_due = now_mono
            self._settings.save()
            self._logger.info("已恢复自动壁纸轮播")
            self._wake_event.set()
        self.check_now(reason="resume")
        self._notify_status()

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._settings.config.scheduler.enabled = bool(enabled)
            self._settings.save()
            self._wake_event.set()
        self._notify_status()

    def record_manual_switch_success(self) -> None:
        """当用户手动点击“下一张/上一张/随机切换”成功后，同步顺延下一次自动切换时间."""
        with self._lock:
            sched = self._settings.config.scheduler
            now_utc = self._clock.now_utc()
            now_mono = self._clock.monotonic()
            interval = sched.interval_seconds

            next_due_dt = now_utc + timedelta(seconds=interval)
            sched.last_success_at_utc = format_utc_iso(now_utc)
            sched.next_due_at_utc = format_utc_iso(next_due_dt)
            sched.last_check_at_utc = format_utc_iso(now_utc)
            sched.consecutive_failures = 0
            sched.last_error = ""

            self._monotonic_last_anchor = now_mono
            self._monotonic_next_due = now_mono + interval
            self._retry_backoff_until_utc = None
            self._settings.save()
        self._notify_status()

    def check_now(self, reason: str = "manual_check") -> bool:
        """立即检查是否到达切换时间；若到达则执行一次壁纸切换.

        返回值：若本次触发并成功完成了切换，返回 `True`，否则返回 `False`。
        """
        with self._lock:
            if self._apply_in_progress:
                return False

            sched = self._settings.config.scheduler
            now_utc = self._clock.now_utc()
            now_mono = self._clock.monotonic()
            sched.last_check_at_utc = format_utc_iso(now_utc)

            if not sched.enabled or sched.paused:
                self._notify_status()
                return False

            # 检查是否处于失败退避冷却期
            if self._retry_backoff_until_utc is not None and now_utc < self._retry_backoff_until_utc:
                self._notify_status()
                return False

            interval = max(5, int(sched.interval_seconds))
            mode = MODE_TIMESTAMP_UTC if interval >= LONG_INTERVAL_THRESHOLD_SECONDS else MODE_MONOTONIC
            sched.scheduler_mode = mode

            # 校准因系统时间大幅往回拨导致的异常远期 next_due_at_utc
            next_due_dt = parse_utc_iso(sched.next_due_at_utc)
            if next_due_dt is not None and (next_due_dt - now_utc).total_seconds() > interval:
                next_due_dt = now_utc + timedelta(seconds=interval)
                sched.next_due_at_utc = format_utc_iso(next_due_dt)
                if mode == MODE_MONOTONIC:
                    self._monotonic_next_due = now_mono + interval
                self._settings.save()

            is_due = False
            if mode == MODE_TIMESTAMP_UTC:
                # 规则 B：>= 6 小时，严格基于持久化 UTC 系统时间戳比较
                if next_due_dt is None:
                    next_due_dt = now_utc + timedelta(seconds=interval)
                    sched.next_due_at_utc = format_utc_iso(next_due_dt)
                    self._settings.save()
                elif now_utc >= next_due_dt:
                    is_due = True
            else:
                # 规则 A：< 6 小时，基于单调时钟比较，兼顾休眠/重启后的真实 UTC 到期检测
                if self._monotonic_next_due is None:
                    if next_due_dt is not None and now_utc >= next_due_dt:
                        self._monotonic_next_due = now_mono
                    else:
                        self._monotonic_next_due = now_mono + interval
                if now_mono >= self._monotonic_next_due:
                    is_due = True
                elif next_due_dt is not None and now_utc >= next_due_dt and reason in (
                    "startup_recovery",
                    "system_resume",
                    "time_change",
                ):
                    is_due = True

            if not is_due:
                self._notify_status()
                return False

            self._apply_in_progress = True

        # 在锁外或可重入上下文中执行实际壁纸切换
        try:
            self._logger.info(
                "触发自动壁纸轮播切换 (reason=%s, mode=%s, interval=%ds)",
                reason,
                mode,
                interval,
            )
            ok, err_msg = self._apply_callback()
        except Exception as exc:
            ok = False
            err_msg = f"调用壁纸切换回调异常: {exc}"
            self._logger.error(err_msg)

        with self._lock:
            self._apply_in_progress = False
            finish_utc = self._clock.now_utc()
            finish_mono = self._clock.monotonic()
            sched = self._settings.config.scheduler
            interval = max(5, int(sched.interval_seconds))

            if ok:
                # 只有在壁纸实际应用成功之后，才更新 last_success_at_utc 与下一次计划时间
                next_due_dt = finish_utc + timedelta(seconds=interval)
                sched.last_success_at_utc = format_utc_iso(finish_utc)
                sched.next_due_at_utc = format_utc_iso(next_due_dt)
                sched.last_check_at_utc = format_utc_iso(finish_utc)
                sched.consecutive_failures = 0
                sched.last_error = ""

                self._monotonic_last_anchor = finish_mono
                self._monotonic_next_due = finish_mono + interval
                self._retry_backoff_until_utc = None
                self._settings.save()
                self._logger.info(
                    "自动壁纸切换成功: last_success=%s, next_due=%s",
                    sched.last_success_at_utc,
                    sched.next_due_at_utc,
                )
                self._notify_status()
                return True
            else:
                # 失败后绝不伪造切换成功状态，记录错误并设置指数退避重试时间
                sched.consecutive_failures = int(sched.consecutive_failures) + 1
                sched.last_error = str(err_msg or "未知壁纸应用错误")
                sched.last_check_at_utc = format_utc_iso(finish_utc)

                backoff_secs = min(300, 15 * (2 ** min(5, sched.consecutive_failures - 1)))
                self._retry_backoff_until_utc = finish_utc + timedelta(seconds=backoff_secs)
                if sched.scheduler_mode == MODE_MONOTONIC:
                    self._monotonic_next_due = finish_mono + backoff_secs

                self._settings.save()
                self._logger.warning(
                    "自动壁纸切换失败 (连续失败 %d 次, 将在 %d 秒后重试): %s",
                    sched.consecutive_failures,
                    backoff_secs,
                    sched.last_error,
                )
                self._notify_status()
                return False

    def get_snapshot(self) -> SchedulerStatusSnapshot:
        """获取当前调度器状态快照 (别名，供 UI 高频轮询)."""
        return self.get_status_snapshot()

    def get_status_snapshot(self) -> SchedulerStatusSnapshot:
        """获取当前调度器状态快照."""
        with self._lock:
            sched = self._settings.config.scheduler
            now_utc = self._clock.now_utc()
            now_mono = self._clock.monotonic()
            interval = max(5, int(sched.interval_seconds))
            mode = MODE_TIMESTAMP_UTC if interval >= LONG_INTERVAL_THRESHOLD_SECONDS else MODE_MONOTONIC
            mode_label = (
                "系统时间戳调度 (≥6小时持久化)"
                if mode == MODE_TIMESTAMP_UTC
                else "单调时钟调度 (<6小时短间隔)"
            )

            next_due_dt = parse_utc_iso(sched.next_due_at_utc)
            if mode == MODE_TIMESTAMP_UTC:
                remaining = (next_due_dt - now_utc).total_seconds() if next_due_dt else float(interval)
            else:
                if self._monotonic_next_due is not None:
                    remaining = self._monotonic_next_due - now_mono
                elif next_due_dt is not None:
                    remaining = (next_due_dt - now_utc).total_seconds()
                else:
                    remaining = float(interval)

            remaining = max(0.0, remaining)

            if not sched.enabled:
                state_summary = "已禁用自动轮播"
            elif sched.paused:
                state_summary = "已暂停"
            elif sched.consecutive_failures > 0:
                state_summary = f"异常重试等待中 (连续失败 {sched.consecutive_failures} 次)"
            else:
                state_summary = f"运行中 ({mode_label})"

            return SchedulerStatusSnapshot(
                enabled=sched.enabled,
                paused=sched.paused,
                interval_seconds=interval,
                scheduler_mode=mode,
                scheduler_mode_label=mode_label,
                last_success_at_utc=sched.last_success_at_utc,
                next_due_at_utc=sched.next_due_at_utc,
                last_check_at_utc=sched.last_check_at_utc,
                remaining_seconds=remaining,
                consecutive_failures=sched.consecutive_failures,
                last_error=sched.last_error,
                state_summary=state_summary,
                is_switching=bool(self._apply_in_progress),
            )

    def _notify_status(self) -> None:
        snapshot = self.get_status_snapshot()
        with self._lock:
            listeners = list(self._status_listeners)
        for cb in listeners:
            try:
                cb(snapshot)
            except Exception:
                pass


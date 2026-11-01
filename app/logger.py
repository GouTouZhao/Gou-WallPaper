"""日志、异常追踪与故障诊断模块 (`app/logger.py`).

支持：
- `%LOCALAPPDATA%\\WallFlow\\logs\\wallflow.log` 自动轮转日志（RotatingFileHandler）
- 内存环形缓冲区，供 GUI“日志与诊断”页面实时查看与导出
- 隐私保护：不联网、不记录无关敏感信息
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable


def get_default_appdata_dir() -> Path:
    """获取默认的应用数据目录 `%LOCALAPPDATA%\\WallFlow`."""
    override = os.environ.get("WALLFLOW_APPDATA_DIR")
    if override:
        return Path(override)
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "WallFlow"
    return Path.home() / "AppData" / "Local" / "WallFlow"


class MemoryRingHandler(logging.Handler):
    """保存最近 N 条格式化日志到内存双端队列，供 GUI 诊断界面展示."""

    def __init__(self, max_records: int = 1000) -> None:
        super().__init__()
        self._records: deque[str] = deque(maxlen=max_records)
        self._lock = threading.Lock()
        self._listeners: list[Callable[[str], None]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._lock:
                self._records.append(msg)
                listeners = list(self._listeners)
            for callback in listeners:
                try:
                    callback(msg)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)

    def get_records(self) -> list[str]:
        with self._lock:
            return list(self._records)

    def get_logs(self) -> str:
        """获取全部环形缓冲日志文本，以换行符连接."""
        with self._lock:
            return "\n".join(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def add_listener(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)


_LOGGER_INITIALIZED = False
_MEMORY_HANDLER: MemoryRingHandler | None = None
_LOG_FILE_PATH: Path | None = None
_INIT_LOCK = threading.Lock()


def setup_logger(
    log_dir: Path | str | None = None,
    level: str = "INFO",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
    log_filename: str = "wallflow.log",
) -> logging.Logger:
    """初始化全局 WallFlow 日志记录器."""
    global _LOGGER_INITIALIZED, _MEMORY_HANDLER, _LOG_FILE_PATH

    with _INIT_LOCK:
        logger = logging.getLogger("wallflow")
        numeric_level = getattr(logging, level.upper(), logging.INFO)
        logger.setLevel(numeric_level)
        logger.propagate = False

        if log_dir is None:
            target_dir = get_default_appdata_dir() / "logs"
        else:
            target_dir = Path(log_dir)

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            target_dir = Path.cwd() / "logs"
            target_dir.mkdir(parents=True, exist_ok=True)

        log_file = target_dir / log_filename

        # 若已初始化且路径相同，仅更新日志级别
        if _LOGGER_INITIALIZED and _LOG_FILE_PATH == log_file:
            for handler in logger.handlers:
                handler.setLevel(numeric_level)
            return logger

        # 清理旧 handlers
        for handler in list(logger.handlers):
            try:
                handler.close()
            except Exception:
                pass
            logger.removeHandler(handler)

        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(threadName)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # 1. 轮转文件日志
        try:
            file_handler = RotatingFileHandler(
                filename=str(log_file),
                maxBytes=max(64 * 1024, int(max_bytes)),
                backupCount=max(1, int(backup_count)),
                encoding="utf-8",
            )
            file_handler.setLevel(numeric_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            _LOG_FILE_PATH = log_file
        except OSError:
            pass

        # 2. 内存环形缓冲区日志
        if _MEMORY_HANDLER is None:
            _MEMORY_HANDLER = MemoryRingHandler(max_records=1500)
        _MEMORY_HANDLER.setLevel(numeric_level)
        _MEMORY_HANDLER.setFormatter(formatter)
        logger.addHandler(_MEMORY_HANDLER)

        # 3. 控制台日志（仅在有标准输出流时挂载）
        if sys.stderr is not None and hasattr(sys.stderr, "write"):
            console_handler = logging.StreamHandler(sys.stderr)
            console_handler.setLevel(numeric_level)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

        _LOGGER_INITIALIZED = True
        return logger


def get_logger(name: str = "") -> logging.Logger:
    """获取子模块日志器."""
    if not _LOGGER_INITIALIZED:
        setup_logger()
    base = logging.getLogger("wallflow")
    return base.getChild(name) if name else base


def get_memory_handler() -> MemoryRingHandler:
    """获取内存日志处理器."""
    if not _LOGGER_INITIALIZED or _MEMORY_HANDLER is None:
        setup_logger()
    assert _MEMORY_HANDLER is not None
    return _MEMORY_HANDLER


def get_log_file_path() -> Path:
    """返回当前日志文件路径."""
    if _LOG_FILE_PATH is None:
        setup_logger()
    return _LOG_FILE_PATH or (get_default_appdata_dir() / "logs" / "wallflow.log")


def install_global_exception_hook() -> None:
    """安装全局未捕获异常钩子，确保崩溃或异常退出时完整记录调用栈."""
    logger = get_logger("exception")

    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.critical("捕获到未处理的全局异常:\n%s", tb_str)

    sys.excepthook = _excepthook
    if hasattr(threading, "excepthook"):
        def _thread_excepthook(args):
            tb_str = "".join(
                traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
            )
            logger.error("后台线程 [%s] 发生未捕获异常:\n%s", getattr(args.thread, "name", "unknown"), tb_str)

        threading.excepthook = _thread_excepthook


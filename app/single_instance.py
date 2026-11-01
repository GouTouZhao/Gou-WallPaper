"""单实例进程互斥与跨进程唤醒模块 (`app/single_instance.py`).

避免用户重复启动多个后台轮播器；当用户第二次点击启动图标时，
通知已有实例显示主窗口并安全退出新进程。
"""

from __future__ import annotations

import ctypes
import socket
import sys
import threading
from typing import Callable

from app.logger import get_logger

ERROR_ALREADY_EXISTS = 183
DEFAULT_IPC_PORT = 47891


class SingleInstanceGuard:
    """基于 Win32 命名互斥体 (`CreateMutexW`) 与本地回环唤醒通道的单实例守护器."""

    def __init__(
        self,
        mutex_name: str = r"Local\WallFlow_SingleInstance_Mutex_v1",
        ipc_port: int = DEFAULT_IPC_PORT,
    ) -> None:
        self._logger = get_logger("single_instance")
        self._mutex_name = mutex_name
        self._ipc_port = ipc_port
        self._mutex_handle: int | None = None
        self._already_running: bool = False
        self._server_socket: socket.socket | None = None
        self._stop_event = threading.Event()
        self._activate_callback: Callable[[], None] | None = None

    @property
    def is_already_running(self) -> bool:
        return self._already_running

    def acquire(self) -> bool:
        """尝试获取单实例锁。若当前是首个实例返回 `True`，若已有实例在运行返回 `False`."""
        if sys.platform == "win32":
            kernel32 = ctypes.windll.kernel32
            kernel32.SetLastError(0)
            handle = kernel32.CreateMutexW(None, False, self._mutex_name)
            last_err = kernel32.GetLastError()
            self._mutex_handle = handle
            if last_err == ERROR_ALREADY_EXISTS:
                self._already_running = True
                return False
            self._already_running = False
            return True
        return True

    def notify_existing_instance(self, command: str = "SHOW_WINDOW") -> bool:
        """向已在运行的实例发送激活指令."""
        try:
            with socket.create_connection(("127.0.0.1", self._ipc_port), timeout=1.0) as sock:
                sock.sendall(command.encode("utf-8"))
            return True
        except OSError:
            return False

    def start_activation_listener(self, on_activate: Callable[[], None]) -> None:
        """在主实例中启动本地回环监听，收到第二个实例唤醒请求时触发 `on_activate`."""
        self._activate_callback = on_activate
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", self._ipc_port))
            srv.listen(4)
            srv.settimeout(1.0)
            self._server_socket = srv
        except OSError as exc:
            self._logger.debug("单实例唤醒端口绑定跳过: %s", exc)
            return

        def _listen_loop() -> None:
            while not self._stop_event.is_set() and self._server_socket is not None:
                try:
                    conn, _ = self._server_socket.accept()
                    with conn:
                        data = conn.recv(128).decode("utf-8", errors="ignore").strip()
                        if data == "SHOW_WINDOW" and self._activate_callback:
                            self._activate_callback()
                except socket.timeout:
                    continue
                except OSError:
                    break

        t = threading.Thread(target=_listen_loop, name="WallFlowSingleInstanceIPC", daemon=True)
        t.start()

    def release(self) -> None:
        """释放单实例互斥体与监听套接字."""
        self._stop_event.set()
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None

        if sys.platform == "win32" and self._mutex_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            except Exception:
                pass
            self._mutex_handle = None


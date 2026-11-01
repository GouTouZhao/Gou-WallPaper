"""Windows 全局快捷键注册与解析模块 (`app/hotkeys.py`)."""

from __future__ import annotations

import ctypes
import sys
from typing import Callable

from app.logger import get_logger

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

HOTKEY_ID_NEXT = 1001
HOTKEY_ID_PREV = 1002
HOTKEY_ID_RANDOM = 1003
HOTKEY_ID_TOGGLE_PAUSE = 1004
HOTKEY_ID_TOGGLE_DESKTOP_ICONS = 1005

VK_MAP: dict[str, int] = {
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "SPACE": 0x20,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
}


def parse_hotkey_string(hotkey_str: str) -> tuple[int, int] | None:
    """将形如 `Ctrl+Alt+Right` 的快捷键字符串解析为 `(modifiers, vk_code)`."""
    if not hotkey_str or not hotkey_str.strip():
        return None
    parts = [p.strip().upper() for p in hotkey_str.split("+") if p.strip()]
    if not parts:
        return None

    modifiers = MOD_NOREPEAT
    vk = 0
    for token in parts:
        if token in ("CTRL", "CONTROL"):
            modifiers |= MOD_CONTROL
        elif token == "ALT":
            modifiers |= MOD_ALT
        elif token == "SHIFT":
            modifiers |= MOD_SHIFT
        elif token in ("WIN", "SUPER", "META"):
            modifiers |= MOD_WIN
        elif token in VK_MAP:
            vk = VK_MAP[token]
        elif len(token) == 1 and token.isalnum():
            vk = ord(token)
        else:
            return None

    if vk == 0:
        return None
    return (modifiers, vk)


class GlobalHotkeyManager:
    """基于 Win32 `RegisterHotKey` 的全局快捷键管理器."""

    def __init__(self) -> None:
        self._logger = get_logger("hotkeys")
        self._registered_ids: set[int] = set()
        self._callbacks: dict[int, Callable[[], None]] = {}

    def register_all(
        self,
        enabled: bool,
        bindings: dict[int, tuple[str, Callable[[], None]]],
    ) -> None:
        """重新注册所有全局快捷键."""
        self.unregister_all()
        if not enabled or sys.platform != "win32":
            return

        for hk_id, (hk_str, callback) in bindings.items():
            parsed = parse_hotkey_string(hk_str)
            if parsed is None:
                continue
            mods, vk = parsed
            try:
                ok = ctypes.windll.user32.RegisterHotKey(None, int(hk_id), int(mods), int(vk))
                if ok:
                    self._registered_ids.add(int(hk_id))
                    self._callbacks[int(hk_id)] = callback
                    self._logger.info("已注册全局快捷键 [%s] (id=%d)", hk_str, hk_id)
                else:
                    self._logger.warning("全局快捷键已被其他程序占用，跳过注册: %s", hk_str)
            except Exception as exc:
                self._logger.warning("注册全局快捷键异常 [%s]: %s", hk_str, exc)

    def handle_hotkey_id(self, hk_id: int) -> bool:
        cb = self._callbacks.get(int(hk_id))
        if cb is not None:
            try:
                cb()
                return True
            except Exception as exc:
                self._logger.error("执行快捷键回调异常 (id=%d): %s", hk_id, exc)
        return False

    def unregister_all(self) -> None:
        if sys.platform == "win32":
            for hk_id in list(self._registered_ids):
                try:
                    ctypes.windll.user32.UnregisterHotKey(None, int(hk_id))
                except Exception:
                    pass
        self._registered_ids.clear()
        self._callbacks.clear()


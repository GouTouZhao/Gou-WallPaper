"""Windows 开机自启管理模块 (`app/startup_manager.py`).

满足需求 8.1：
- 使用当前用户级别注册表 `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`，无需管理员权限。
- 启动后支持以最小化/后台模式运行 (`--minimized`)，不强制弹出主窗口。
- 正确为包含空格、中文和特殊字符的路径包裹双引号。
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

from app.logger import get_logger

RUN_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_VALUE_NAME = "WallFlow"


def build_startup_command(
    executable_path: str | Path | None = None,
    script_path: str | Path | None = None,
    minimized: bool = True,
) -> str:
    """构建安全的 Windows 开机自启命令行字符串，正确转义并包裹空格、中文和特殊字符路径."""
    flag = " --minimized" if minimized else ""

    if executable_path is not None:
        exe_str = str(Path(executable_path).resolve())
        if script_path is not None:
            scr_str = str(Path(script_path).resolve())
            return f'"{exe_str}" "{scr_str}"{flag}'
        return f'"{exe_str}"{flag}'

    if getattr(sys, "frozen", False):
        exe_str = str(Path(sys.executable).resolve())
        return f'"{exe_str}"{flag}'

    # 开发环境下优先检测 pythonw.exe 防止弹出黑框，否则使用当前解释器 + main.py
    py_exe = Path(sys.executable).resolve()
    pythonw = py_exe.with_name("pythonw.exe")
    runner = str(pythonw if pythonw.exists() else py_exe)
    main_script = str((Path(__file__).resolve().parent.parent / "main.py").resolve())
    return f'"{runner}" "{main_script}"{flag}'


class StartupManager:
    """管理当前用户 `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` 下的开机自启项."""

    def __init__(self, value_name: str = APP_VALUE_NAME) -> None:
        self._logger = get_logger("startup")
        self._value_name = value_name

    @property
    def value_name(self) -> str:
        return self._value_name

    def is_enabled(self) -> bool:
        """检查开机自启是否已开启."""
        cmd = self.get_registered_command()
        return bool(cmd and cmd.strip())

    def get_registered_command(self) -> str:
        """读取注册表中当前登记的自启命令."""
        if sys.platform != "win32":
            return ""
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                RUN_REGISTRY_KEY,
                0,
                winreg.KEY_READ,
            ) as key:
                val, _ = winreg.QueryValueEx(key, self._value_name)
                return str(val or "")
        except FileNotFoundError:
            return ""
        except OSError as exc:
            self._logger.debug("读取开机自启注册表失败: %s", exc)
            return ""

    def enable(
        self,
        executable_path: str | Path | None = None,
        script_path: str | Path | None = None,
        minimized: bool = True,
    ) -> tuple[bool, str]:
        """开启开机自启."""
        if sys.platform != "win32":
            return False, "当前非 Windows 平台"
        cmd = build_startup_command(
            executable_path=executable_path,
            script_path=script_path,
            minimized=minimized,
        )
        try:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                RUN_REGISTRY_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.SetValueEx(key, self._value_name, 0, winreg.REG_SZ, cmd)
            self._logger.info("已启用开机自启 [%s] = %s", self._value_name, cmd)
            return True, cmd
        except OSError as exc:
            err = f"写入开机自启注册表失败: {exc}"
            self._logger.error(err)
            return False, err

    def disable(self) -> tuple[bool, str]:
        """关闭开机自启."""
        if sys.platform != "win32":
            return False, "当前非 Windows 平台"
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                RUN_REGISTRY_KEY,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                try:
                    winreg.DeleteValue(key, self._value_name)
                except FileNotFoundError:
                    pass
            self._logger.info("已关闭开机自启 [%s]", self._value_name)
            return True, ""
        except OSError as exc:
            err = f"删除开机自启注册表项失败: {exc}"
            self._logger.error(err)
            return False, err

    def set_enabled(
        self,
        enabled: bool,
        executable_path: str | Path | None = None,
        script_path: str | Path | None = None,
        minimized: bool = True,
    ) -> tuple[bool, str]:
        if enabled:
            return self.enable(executable_path=executable_path, script_path=script_path, minimized=minimized)
        return self.disable()


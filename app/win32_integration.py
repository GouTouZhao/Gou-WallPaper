"""Windows 原生 API 集成模块 (`app/win32_integration.py`).

封装：
- `IDesktopWallpaper` COM 接口（多显示器独立壁纸、显示器设备路径、位置模式）
- `SystemParametersInfoW(SPI_SETDESKWALLPAPER)` 与注册表 `Control Panel\\Desktop` 兼容性回退
- `EnumDisplayMonitors` + `GetMonitorInfoW` 多显示器物理分辨率、坐标、主副屏与方向识别
- 屏保 `/p <hwnd>` 预览窗口嵌入 (`SetParent` + `WS_CHILD`) 与系统屏保控制面板调用 (`desk.cpl`)
- Windows 系统事件过滤器 (`WM_TIMECHANGE`, `WM_DISPLAYCHANGE`, `WM_SETTINGCHANGE`, `WM_POWERBROADCAST`, `WM_WTSSESSION_CHANGE`, `WM_HOTKEY`)
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import subprocess
import sys
import threading
import time
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.logger import get_logger

logger = get_logger("win32")

# Win32 常量定义
SPI_SETDESKWALLPAPER = 0x0014
SPI_GETDESKWALLPAPER = 0x0073
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDWININICHANGE = 0x02

GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CLIPCHILDREN = 0x02000000
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

# Windows 系统消息常量
WM_TIMECHANGE = 0x001E
WM_SETTINGCHANGE = 0x001A
WM_DISPLAYCHANGE = 0x007E
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
WM_HOTKEY = 0x0312

PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMECRITICAL = 0x0006

WTS_SESSION_LOGON = 0x0005
WTS_SESSION_LOCK = 0x0007
WTS_SESSION_UNLOCK = 0x0008

MONITORINFOF_PRIMARY = 0x00000001
CCHDEVICENAME = 32

# IDesktopWallpaper DESKTOP_WALLPAPER_POSITION 枚举
DWPOS_CENTER = 0
DWPOS_TILE = 1
DWPOS_STRETCH = 2
DWPOS_FIT = 3
DWPOS_FILL = 4
DWPOS_SPAN = 5

DISPLAY_MODE_TO_DWPOS: dict[str, int] = {
    "center": DWPOS_CENTER,
    "tile": DWPOS_TILE,
    "stretch": DWPOS_STRETCH,
    "fit": DWPOS_FIT,
    "fill": DWPOS_FILL,
    "span": DWPOS_SPAN,
}

# 注册表 Control Panel\Desktop WallpaperStyle / TileWallpaper 映射
DISPLAY_MODE_TO_REGISTRY: dict[str, tuple[str, str]] = {
    "center": ("0", "0"),
    "tile": ("0", "1"),
    "stretch": ("2", "0"),
    "fit": ("6", "0"),
    "fill": ("10", "0"),
    "span": ("22", "0"),
}


@dataclass(frozen=True)
class MonitorInfo:
    """单个显示器拓扑与几何信息."""

    index: int
    device_name: str  # 如 \\.\DISPLAY1
    device_id: str  # IDesktopWallpaper MonitorID 或设备路径
    x: int
    y: int
    width: int
    height: int
    is_primary: bool = False

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def is_landscape(self) -> bool:
        return self.width >= self.height

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width

    @property
    def orientation_text(self) -> str:
        return "横屏 (Landscape)" if self.is_landscape else "竖屏 (Portrait)"

    @property
    def display_label(self) -> str:
        primary_tag = " [主屏]" if self.is_primary else ""
        return f"显示器 {self.index + 1} ({self.device_name}){primary_tag}: {self.width}×{self.height} @ ({self.x},{self.y})"


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * CCHDEVICENAME),
    ]


def enable_per_monitor_dpi_awareness() -> None:
    """开启 Per-Monitor DPI v2 感知，确保获取物理像素分辨率而非缩放后的逻辑像素."""
    if sys.platform != "win32":
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def enumerate_display_monitors() -> list[MonitorInfo]:
    """使用 Win32 `EnumDisplayMonitors` + `GetMonitorInfoW` 枚举当前所有活动显示器."""
    if sys.platform != "win32":
        return [
            MonitorInfo(
                index=0,
                device_name="DISPLAY1",
                device_id="DISPLAY1",
                x=0,
                y=0,
                width=1920,
                height=1080,
                is_primary=True,
            )
        ]

    raw_monitors: list[tuple[str, int, int, int, int, bool]] = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def _callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if ctypes.windll.user32.GetMonitorInfoW(hMonitor, ctypes.byref(info)):
            rc = info.rcMonitor
            w = int(rc.right - rc.left)
            h = int(rc.bottom - rc.top)
            if w > 0 and h > 0:
                is_prim = bool(info.dwFlags & MONITORINFOF_PRIMARY)
                raw_monitors.append((str(info.szDevice), int(rc.left), int(rc.top), w, h, is_prim))
        return True

    try:
        enum_proc = MonitorEnumProc(_callback)
        ctypes.windll.user32.EnumDisplayMonitors(None, None, enum_proc, 0)
    except Exception as exc:
        logger.warning("EnumDisplayMonitors 调用异常: %s", exc)

    if not raw_monitors:
        try:
            w = int(ctypes.windll.user32.GetSystemMetrics(0)) or 1920
            h = int(ctypes.windll.user32.GetSystemMetrics(1)) or 1080
        except Exception:
            w, h = 1920, 1080
        return [
            MonitorInfo(
                index=0,
                device_name=r"\\.\DISPLAY1",
                device_id=r"\\.\DISPLAY1",
                x=0,
                y=0,
                width=w,
                height=h,
                is_primary=True,
            )
        ]

    # 主显示器优先，其次按 x, y 排序
    raw_monitors.sort(key=lambda item: (0 if item[5] else 1, item[1], item[2]))

    # 尝试映射 IDesktopWallpaper 的 monitorID
    com_map = _query_com_monitor_rects()

    results: list[MonitorInfo] = []
    for idx, (dev_name, x, y, w, h, is_prim) in enumerate(raw_monitors):
        matched_id = dev_name
        for com_id, (cx, cy, cw, ch) in com_map.items():
            if cx == x and cy == y and cw == w and ch == h:
                matched_id = com_id
                break
        results.append(
            MonitorInfo(
                index=idx,
                device_name=dev_name,
                device_id=matched_id,
                x=x,
                y=y,
                width=w,
                height=h,
                is_primary=is_prim,
            )
        )
    return results


def compute_virtual_desktop_bounds(monitors: list[MonitorInfo]) -> tuple[int, int, int, int]:
    """计算所有显示器的虚拟桌面总包围盒 `(min_x, min_y, total_width, total_height)`."""
    if not monitors:
        return (0, 0, 1920, 1080)
    min_x = min(m.x for m in monitors)
    min_y = min(m.y for m in monitors)
    max_x = max(m.right for m in monitors)
    max_y = max(m.bottom for m in monitors)
    return (min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y))


# ============================================================================
# IDesktopWallpaper COM 接口定义与调用
# ============================================================================

def _create_desktop_wallpaper_com():
    """通过 `comtypes` 创建 Windows `IDesktopWallpaper` COM 实例."""
    import comtypes
    import comtypes.client
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    CLSID_DesktopWallpaper = GUID("{C2CF3110-460E-4fc1-B9D0-8A1C0C9CC4BD}")
    IID_IDesktopWallpaper = GUID("{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}")

    class IDesktopWallpaper(IUnknown):
        _case_insensitive_ = True
        _iid_ = IID_IDesktopWallpaper
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "SetWallpaper",
                (["in"], wintypes.LPCWSTR, "monitorID"),
                (["in"], wintypes.LPCWSTR, "wallpaper"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "GetWallpaper",
                (["in"], wintypes.LPCWSTR, "monitorID"),
                (["out"], ctypes.POINTER(wintypes.LPWSTR), "wallpaper"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "GetMonitorDevicePathAt",
                (["in"], wintypes.UINT, "monitorIndex"),
                (["out"], ctypes.POINTER(wintypes.LPWSTR), "monitorID"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "GetMonitorDevicePathCount",
                (["out"], ctypes.POINTER(wintypes.UINT), "count"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "GetMonitorRECT",
                (["in"], wintypes.LPCWSTR, "monitorID"),
                (["out"], ctypes.POINTER(wintypes.RECT), "displayRect"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "SetBackgroundColor",
                (["in"], wintypes.COLORREF, "color"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "GetBackgroundColor",
                (["out"], ctypes.POINTER(wintypes.COLORREF), "color"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "SetPosition",
                (["in"], ctypes.c_int, "position"),
            ),
            COMMETHOD(
                [],
                HRESULT,
                "GetPosition",
                (["out"], ctypes.POINTER(ctypes.c_int), "position"),
            ),
        ]

    comtypes.CoInitialize()
    return comtypes.client.CreateObject(CLSID_DesktopWallpaper, interface=IDesktopWallpaper)


def _query_com_monitor_rects() -> dict[str, tuple[int, int, int, int]]:
    """查询 `IDesktopWallpaper` 中当前活动显示器的 `(monitorID -> (x, y, w, h))`."""
    if sys.platform != "win32":
        return {}
    mapping: dict[str, tuple[int, int, int, int]] = {}
    try:
        dw = _create_desktop_wallpaper_com()
        count = dw.GetMonitorDevicePathCount()
        for i in range(int(count)):
            try:
                mon_id = dw.GetMonitorDevicePathAt(i)
                if not mon_id:
                    continue
                rc = dw.GetMonitorRECT(mon_id)
                w = int(rc.right - rc.left)
                h = int(rc.bottom - rc.top)
                if w > 0 and h > 0:
                    mapping[str(mon_id)] = (int(rc.left), int(rc.top), w, h)
            except Exception:
                # 断开连接的显示器在 GetMonitorRECT 时会抛出 COMError，直接忽略
                continue
    except Exception as exc:
        logger.debug("查询 IDesktopWallpaper 显示器映射跳过: %s", exc)
    return mapping


def set_wallpaper_via_com(
    monitor_wallpapers: dict[str | None, str],
    display_mode: str = "fill",
    background_rgb: tuple[int, int, int] = (0, 0, 0),
) -> tuple[bool, str]:
    """优先使用 Windows `IDesktopWallpaper` COM 接口设置单显示器或多显示器壁纸.

    参数 `monitor_wallpapers` 键为 `monitor_id`（若为 `None` 表示应用到所有显示器），值为图片绝对路径。
    返回 `(success, error_message)`。
    """
    if sys.platform != "win32":
        return False, "当前非 Windows 平台"

    try:
        dw = _create_desktop_wallpaper_com()
        pos_enum = DISPLAY_MODE_TO_DWPOS.get(display_mode.lower(), DWPOS_FILL)
        dw.SetPosition(pos_enum)

        r, g, b = background_rgb
        colorref = (int(r) & 0xFF) | ((int(g) & 0xFF) << 8) | ((int(b) & 0xFF) << 16)
        try:
            dw.SetBackgroundColor(colorref)
        except Exception:
            pass

        # 获取 COM 已知的有效 monitorID 集合
        active_com_ids: list[str] = []
        try:
            count = int(dw.GetMonitorDevicePathCount())
            for i in range(count):
                try:
                    mid = dw.GetMonitorDevicePathAt(i)
                    if mid:
                        rc = dw.GetMonitorRECT(mid)
                        if (rc.right - rc.left) > 0 and (rc.bottom - rc.top) > 0:
                            active_com_ids.append(str(mid))
                except Exception:
                    continue
        except Exception:
            pass

        for target_id, img_path in monitor_wallpapers.items():
            abs_path = str(Path(img_path).resolve())
            if not Path(abs_path).exists():
                return False, f"壁纸文件不存在: {abs_path}"
            if target_id is None:
                dw.SetWallpaper(None, abs_path)
            elif target_id in active_com_ids:
                dw.SetWallpaper(target_id, abs_path)
            elif len(active_com_ids) == 1:
                dw.SetWallpaper(active_com_ids[0], abs_path)
            else:
                # 若传入的是 \\.\DISPLAY1 而非 COM 路径，返回 False 交由虚拟桌面合成回退
                return False, f"显示器标识 {target_id} 不在活动 IDesktopWallpaper 设备列表中"

        return True, ""
    except Exception as exc:
        return False, f"IDesktopWallpaper COM 调用失败: {exc}"


def set_wallpaper_via_spi(
    image_path: str | Path,
    display_mode: str = "fill",
) -> tuple[bool, str]:
    """通过注册表样式设置 + `SystemParametersInfoW(SPI_SETDESKWALLPAPER)` 应用桌面壁纸."""
    if sys.platform != "win32":
        return False, "当前非 Windows 平台"

    abs_path = str(Path(image_path).resolve())
    if not Path(abs_path).exists():
        return False, f"壁纸图片不存在: {abs_path}"

    style_val, tile_val = DISPLAY_MODE_TO_REGISTRY.get(display_mode.lower(), ("10", "0"))
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Desktop",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, style_val)
            winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, tile_val)
    except OSError as exc:
        logger.warning("写入 Control Panel\\Desktop 注册表样式失败: %s", exc)

    try:
        flags = SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_SETDESKWALLPAPER,
            0,
            abs_path,
            flags,
        )
        if not ok:
            err_code = ctypes.GetLastError()
            return False, f"SystemParametersInfoW 返回失败 (GetLastError={err_code})"
        return True, ""
    except Exception as exc:
        return False, f"SystemParametersInfoW 调用异常: {exc}"


def get_current_system_wallpaper() -> str:
    """通过 `SystemParametersInfoW(SPI_GETDESKWALLPAPER)` 查询当前系统壁纸路径."""
    if sys.platform != "win32":
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 512, buf, 0)
        return buf.value if ok else ""
    except Exception:
        return ""


# ============================================================================
# Windows 屏幕保护程序系统集成与预览窗口挂载
# ============================================================================

def is_valid_window_handle(hwnd: int) -> bool:
    """检查给定的窗口句柄 `hwnd` 是否是当前有效的 Win32 窗口."""
    if sys.platform != "win32" or not hwnd or hwnd <= 0:
        return False
    try:
        return bool(ctypes.windll.user32.IsWindow(wintypes.HWND(int(hwnd))))
    except Exception:
        return False


def get_window_client_rect(hwnd: int) -> tuple[int, int]:
    """获取父窗口客户区宽高 `(width, height)`."""
    if sys.platform != "win32" or not is_valid_window_handle(hwnd):
        return (240, 180)
    rc = wintypes.RECT()
    if ctypes.windll.user32.GetClientRect(wintypes.HWND(int(hwnd)), ctypes.byref(rc)):
        w = max(1, int(rc.right - rc.left))
        h = max(1, int(rc.bottom - rc.top))
        return (w, h)
    return (240, 180)


def embed_window_into_preview_hwnd(child_win_id: int, parent_hwnd: int) -> tuple[int, int]:
    """将 PySide6 窗口挂载为 Windows 屏保设置预览小窗 (`parent_hwnd`) 的子窗口."""
    if sys.platform != "win32":
        return (240, 180)
    w, h = get_window_client_rect(parent_hwnd)
    try:
        user32 = ctypes.windll.user32
        c_hwnd = wintypes.HWND(int(child_win_id))
        p_hwnd = wintypes.HWND(int(parent_hwnd))

        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        set_long(c_hwnd, GWL_STYLE, WS_CHILD | WS_VISIBLE | WS_CLIPCHILDREN)
        user32.SetParent(c_hwnd, p_hwnd)
        user32.SetWindowPos(
            c_hwnd,
            wintypes.HWND(0),
            0,
            0,
            w,
            h,
            SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
    except Exception as exc:
        logger.error("挂载屏保预览窗口句柄 (child=%s, parent=%s) 失败: %s", child_win_id, parent_hwnd, exc)
    return (w, h)


def open_windows_screensaver_settings() -> bool:
    """打开 Windows 原生“屏幕保护程序设置”对话框 (`desk.cpl`)."""
    if sys.platform != "win32":
        return False
    commands = [
        ["control.exe", "desk.cpl,screensaver,@screensaver"],
        ["rundll32.exe", "shell32.dll,Control_RunDLL", "desk.cpl,,1"],
    ]
    sys32 = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    for cmd in commands:
        try:
            subprocess.Popen(cmd, close_fds=True, cwd=sys32)
            logger.info("已启动 Windows 屏幕保护程序设置: %s", " ".join(cmd))
            return True
        except Exception as exc:
            logger.warning("尝试启动系统屏保设置失败 (%s): %s", cmd, exc)
    return False


def heal_screensaver_registry_path(expected_scr_path: str | Path | None = None) -> bool:
    """自动修复 HKCU\\Control Panel\\Desktop 中可能被 desk.cpl 截断为 WALLFL~1.SCR 的短路径."""
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Desktop",
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as key:
            try:
                curr_val, _ = winreg.QueryValueEx(key, "SCRNSAVE.EXE")
            except FileNotFoundError:
                curr_val = ""

            # 若当前项包含 wallfl 或 screensaver 但不是存在的绝对路径
            if curr_val and ("wallfl" in curr_val.lower() or "screensaver" in curr_val.lower()):
                p = Path(curr_val)
                if not p.is_absolute() or not p.exists():
                    target: Path | None = None
                    if expected_scr_path and Path(expected_scr_path).exists():
                        target = Path(expected_scr_path).resolve()
                    else:
                        candidates = [
                            Path(sys.executable).parent / "GouWallpaperSaver.scr",
                            Path(__file__).resolve().parent.parent / "dist" / "GouWallpaperSaver.scr",
                            Path(__file__).resolve().parent.parent / "GouWallpaperSaver.scr",
                            Path(sys.executable).parent / "WallFlowSaver.scr",
                            Path(__file__).resolve().parent.parent / "dist" / "WallFlowSaver.scr",
                            Path(__file__).resolve().parent.parent / "WallFlowSaver.scr",
                        ]
                        for c in candidates:
                            if c.exists():
                                target = c.resolve()
                                break
                    if target:
                        winreg.SetValueEx(key, "SCRNSAVE.EXE", 0, winreg.REG_SZ, str(target))
                        logger.info("已自动修复系统屏保注册表路径: %s -> %s", curr_val, target)
                        return True
    except Exception as exc:
        logger.debug("自动修复系统屏保注册表路径异常: %s", exc)
    return False


def get_system_screensaver_status() -> dict[str, str]:
    """读取 `HKCU\\Control Panel\\Desktop` 中当前 Windows 屏保设置状态."""
    result = {
        "scrnsave_exe": "(未设置)",
        "active": "否",
        "timeout_seconds": "0",
    }
    if sys.platform != "win32":
        return result
    heal_screensaver_registry_path()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Desktop",
            0,
            winreg.KEY_READ,
        ) as key:
            try:
                val, _ = winreg.QueryValueEx(key, "SCRNSAVE.EXE")
                if val:
                    result["scrnsave_exe"] = str(val)
            except FileNotFoundError:
                pass
            try:
                act, _ = winreg.QueryValueEx(key, "ScreenSaveActive")
                result["active"] = "是" if str(act) == "1" else "否"
            except FileNotFoundError:
                pass
            try:
                timeout, _ = winreg.QueryValueEx(key, "ScreenSaveTimeOut")
                result["timeout_seconds"] = str(timeout)
            except FileNotFoundError:
                pass
    except OSError as exc:
        logger.debug("读取系统屏保注册表信息失败: %s", exc)
    return result


def install_screensaver_to_user_desktop_config(scr_path: str | Path, timeout_seconds: int = 300) -> tuple[bool, str]:
    """将指定 `.scr` 文件注册为当前用户的 Windows 屏幕保护程序（无需管理员权限）."""
    if sys.platform != "win32":
        return False, "当前非 Windows 平台"
    resolved = Path(scr_path).resolve()
    if not resolved.exists():
        return False, f"屏保文件不存在: {resolved}"
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Control Panel\Desktop",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "SCRNSAVE.EXE", 0, winreg.REG_SZ, str(resolved))
            winreg.SetValueEx(key, "ScreenSaveActive", 0, winreg.REG_SZ, "1")
            winreg.SetValueEx(key, "ScreenSaveTimeOut", 0, winreg.REG_SZ, str(max(60, int(timeout_seconds))))
        logger.info("已将 %s 设置为当前用户 Windows 屏幕保护程序", resolved)
        return True, ""
    except OSError as exc:
        return False, f"写入当前用户屏保注册表项失败: {exc}"


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


def get_system_idle_seconds() -> float:
    """获取 Windows 系统全局无键盘鼠标输入的空闲秒数 (基于 Win32 GetLastInputInfo)."""
    if sys.platform != "win32":
        return 0.0
    try:
        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            curr_tick = ctypes.windll.kernel32.GetTickCount()
            # 兼容 32 位 DWORD 环绕
            diff = (curr_tick - lii.dwTime) & 0xFFFFFFFF
            return max(0.0, float(diff) / 1000.0)
    except Exception as exc:
        logger.debug("获取系统空闲时间失败: %s", exc)
    return 0.0


@dataclass
class SystemActivityState:
    """系统级智能活跃状态与多媒体检测结果."""

    is_busy: bool = False
    reason: str = ""
    audio_playing: bool = False
    audio_peak: float = 0.0
    fullscreen_active: bool = False
    fullscreen_title: str = ""
    user_notification_state: int = 0


class AudioActivityDetector:
    """基于 Windows Core Audio (WASAPI) 实时检测系统音频输出与活动会话."""

    def __init__(self) -> None:
        self._last_audio_active_mono: float = 0.0
        self._meter = None
        self._session_mgr = None
        self._lock = threading.Lock()

    def _ensure_interfaces(self):
        if sys.platform != "win32":
            return None, None
        if self._meter is not None:
            return self._meter, self._session_mgr
        try:
            import comtypes
            from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
            from ctypes import POINTER, c_float, c_int, windll

            class _IAudioMeterInformation(IUnknown):
                _iid_ = GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
                _methods_ = [
                    COMMETHOD([], HRESULT, "GetPeakValue", (["out"], POINTER(c_float), "pfPeak")),
                ]

            class _IAudioSessionControl(IUnknown):
                _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")
                _methods_ = [
                    COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_int), "pRetVal")),
                ]

            class _IAudioSessionEnumerator(IUnknown):
                _iid_ = GUID("{E2F5EE11-070C-401E-A580-9260B860FAF5}")
                _methods_ = [
                    COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int), "SessionCount")),
                    COMMETHOD(
                        [],
                        HRESULT,
                        "GetSession",
                        (["in"], c_int, "SessionIndex"),
                        (["out"], POINTER(POINTER(_IAudioSessionControl)), "Session"),
                    ),
                ]

            class _IAudioSessionManager2(IUnknown):
                _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
                _methods_ = [
                    COMMETHOD([], HRESULT, "GetAudioSessionControl"),
                    COMMETHOD([], HRESULT, "GetSimpleAudioVolume"),
                    COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], POINTER(POINTER(_IAudioSessionEnumerator)), "SessionEnum")),
                ]

            class _IMMDevice(IUnknown):
                _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
                _methods_ = [
                    COMMETHOD(
                        [],
                        HRESULT,
                        "Activate",
                        (["in"], POINTER(GUID), "iid"),
                        (["in"], c_int, "dwClsCtx"),
                        (["in"], POINTER(c_int), "pActivationParams"),
                        (["out"], POINTER(POINTER(IUnknown)), "ppInterface"),
                    ),
                ]

            class _IMMDeviceEnumerator(IUnknown):
                _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
                _methods_ = [
                    COMMETHOD([], HRESULT, "EnumAudioEndpoints"),
                    COMMETHOD(
                        [],
                        HRESULT,
                        "GetDefaultAudioEndpoint",
                        (["in"], c_int, "dataFlow"),
                        (["in"], c_int, "role"),
                        (["out"], POINTER(POINTER(_IMMDevice)), "ppDevice"),
                    ),
                ]

            try:
                windll.ole32.CoInitialize(None)
            except Exception:
                pass

            enumerator = comtypes.CoCreateInstance(
                GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}"),
                _IMMDeviceEnumerator,
                comtypes.CLSCTX_INPROC_SERVER,
            )
            device = enumerator.GetDefaultAudioEndpoint(0, 1)
            meter_obj = device.Activate(GUID("{C02216F6-8C67-4B5B-9D00-D008E73E0064}"), comtypes.CLSCTX_ALL, None)
            self._meter = meter_obj.QueryInterface(_IAudioMeterInformation)
            try:
                mgr_obj = device.Activate(GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}"), comtypes.CLSCTX_ALL, None)
                self._session_mgr = mgr_obj.QueryInterface(_IAudioSessionManager2)
            except Exception:
                self._session_mgr = None

            return self._meter, self._session_mgr
        except Exception as exc:
            logger.debug("初始化 Windows Core Audio 接口异常: %s", exc)
            self._meter = None
            self._session_mgr = None
            return None, None

    def check_audio_activity(self, grace_period_seconds: float = 25.0) -> tuple[bool, float]:
        """检测当前系统是否有音频正在输出或处于短暂停顿的宽限期内.

        返回 (is_active: bool, peak_value: float).
        """
        now = time.monotonic()
        peak = 0.0
        has_active_session = False
        with self._lock:
            meter, mgr = self._ensure_interfaces()
            if meter is not None:
                try:
                    peak = float(meter.GetPeakValue())
                except Exception:
                    self._meter = None
                    self._session_mgr = None

            if mgr is not None:
                try:
                    s_enum = mgr.GetSessionEnumerator()
                    cnt = s_enum.GetCount()
                    for i in range(cnt):
                        ctl = s_enum.GetSession(i)
                        # AudioSessionStateActive == 1
                        if ctl.GetState() == 1:
                            has_active_session = True
                            break
                except Exception:
                    self._session_mgr = None

            if peak > 0.001 or has_active_session:
                self._last_audio_active_mono = now
                return True, peak

            if (now - self._last_audio_active_mono) < grace_period_seconds and self._last_audio_active_mono > 0:
                return True, peak

            return False, peak


_GLOBAL_AUDIO_DETECTOR = AudioActivityDetector()

QUNS_NOT_PRESENT = 1
QUNS_BUSY = 2
QUNS_RUNNING_D_3_D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7

MEDIA_WINDOW_KEYWORDS = (
    "抖音",
    "tiktok",
    "哔哩哔哩",
    "bilibili",
    "youtube",
    "potplayer",
    "vlc",
    "爱奇艺",
    "腾讯视频",
    "优酷",
    "netflix",
    "西瓜视频",
)


def is_foreground_fullscreen() -> tuple[bool, str]:
    """检测当前前台焦点窗口是否处于全屏显示状态（覆盖整个显示器边界）."""
    if sys.platform != "win32":
        return False, ""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd or not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
            return False, ""

        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        cls_name = cls_buf.value
        if cls_name in (
            "Progman",
            "WorkerW",
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
            "Windows.UI.Core.CoreWindow",
        ):
            return False, ""

        title_buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title_buf, 512)
        title = title_buf.value

        if title.startswith(("WallFlow", "Gou Wallpaper", "GouWallpaper")) or "Screensaver" in title:
            return False, ""

        h_mon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not h_mon:
            return False, ""

        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(h_mon, ctypes.byref(mi)):
            return False, ""

        w_rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(w_rect)):
            return False, ""

        is_fs = (
            w_rect.left <= mi.rcMonitor.left
            and w_rect.top <= mi.rcMonitor.top
            and w_rect.right >= mi.rcMonitor.right
            and w_rect.bottom >= mi.rcMonitor.bottom
        )
        return bool(is_fs), title or cls_name
    except Exception as exc:
        logger.debug("检测全屏窗口失败: %s", exc)
        return False, ""


def check_system_media_and_busy_state(
    suppress_media: bool = True,
    suppress_fullscreen: bool = True,
) -> SystemActivityState:
    """系统级综合判断：检测当前 Windows 是否正处于多媒体播放、全屏专注或演示繁忙状态."""
    state = SystemActivityState()
    if sys.platform != "win32":
        return state

    # 1. 检测系统音频活跃度（视频、音乐、短视频、语音通话）
    if suppress_media:
        try:
            audio_active, peak = _GLOBAL_AUDIO_DETECTOR.check_audio_activity()
            state.audio_playing = audio_active
            state.audio_peak = peak
            if audio_active:
                state.is_busy = True
                if peak > 0.001:
                    state.reason = f"系统正在播放音频/视频 (音量: {int(peak * 100)}%)"
                else:
                    state.reason = "检测到近期活跃音频流 (视频播放/影视短暂停歇)"
        except Exception as exc:
            logger.debug("检测音频播放状态异常: %s", exc)

    # 2. 检测 Windows 原生用户专注状态 (SHQueryUserNotificationState)
    # 仅检测 DirectX 独占全屏 (游戏) 与 PPT 演示模式，避免普通的专注助手/通知静音 (QUNS_BUSY=2) 产生误报
    try:
        qstate = wintypes.DWORD()
        res = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(qstate))
        if res == 0:
            val = qstate.value
            state.user_notification_state = val
            if suppress_fullscreen and not state.is_busy and val in (
                QUNS_RUNNING_D_3_D_FULL_SCREEN,
                QUNS_PRESENTATION_MODE,
            ):
                state.is_busy = True
                desc = {
                    QUNS_RUNNING_D_3_D_FULL_SCREEN: "DirectX 3D全屏独占中 (全屏游戏/播放器)",
                    QUNS_PRESENTATION_MODE: "Windows演示/投影模式",
                }.get(val, "系统级专注演示模式")
                state.reason = desc
    except Exception as exc:
        logger.debug("SHQueryUserNotificationState 查询失败: %s", exc)

    # 3. 检测前台窗口全屏状态 (双重保证，覆盖第三方非标准无边框全屏播放器)
    if suppress_fullscreen and not state.is_busy:
        is_fs, fs_title = is_foreground_fullscreen()
        state.fullscreen_active = is_fs
        state.fullscreen_title = fs_title
        if is_fs:
            state.is_busy = True
            state.reason = f"前台全屏窗口运行中 ({fs_title[:30]})"

    # 4. 检测常见多媒体前台窗口（即使未处于全屏且音频处于微声）
    if suppress_media and not state.is_busy:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if hwnd and user32.IsWindow(hwnd):
                title_buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, title_buf, 512)
                cur_title = title_buf.value.lower()
                for kw in MEDIA_WINDOW_KEYWORDS:
                    if kw in cur_title:
                        audio_active, _ = _GLOBAL_AUDIO_DETECTOR.check_audio_activity(grace_period_seconds=60.0)
                        if audio_active:
                            state.is_busy = True
                            state.reason = f"正在浏览多媒体视听内容 ({title_buf.value[:25]})"
                            break
        except Exception:
            pass

    return state


ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_CONTINUOUS = 0x80000000


def set_thread_display_required(enable: bool = True, include_system: bool = True) -> bool:
    """通知 Windows 屏幕正在处于主动全屏屏保/展示或多媒体视听状态，阻止 Windows 系统空闲超时后重复拉起 .scr 屏保或熄屏/休眠.

    当屏保退出或多媒体播放结束时传入 enable=False 恢复默认电源管理状态.
    """
    if sys.platform != "win32":
        return False
    try:
        active_flags = ES_DISPLAY_REQUIRED
        if include_system:
            active_flags |= ES_SYSTEM_REQUIRED
        flags = ES_CONTINUOUS | (active_flags if enable else 0)
        res = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        return res != 0
    except Exception as exc:
        logger.debug("SetThreadExecutionState 调用异常: %s", exc)
        return False


def suspend_system(hibernate: bool = False, force: bool = False) -> bool:
    """令 Windows 系统进入睡眠或休眠状态 (通过 powrprof.dll SetSuspendState).

    :param hibernate: True 为休眠 (S4)，False 为待机睡眠 (S3 / 现代待机)
    :param force: 是否强制挂起，忽略应用程序的阻拦广播
    """
    if sys.platform != "win32":
        return False
    try:
        powrprof = ctypes.windll.powrprof
        powrprof.SetSuspendState.argtypes = [wintypes.BOOL, wintypes.BOOL, wintypes.BOOL]
        powrprof.SetSuspendState.restype = wintypes.BOOL
        # SetSuspendState(bHibernate, bForce, bWakeupEventsDisabled)
        return bool(powrprof.SetSuspendState(1 if hibernate else 0, 1 if force else 0, 0))
    except Exception as exc:
        logger.warning("SetSuspendState 调用失败: %s", exc)
        return False


def turn_off_display() -> bool:
    """通过 Win32 SC_MONITORPOWER 系统指令关闭显示器 (进入低功耗息屏)."""
    if sys.platform != "win32":
        return False
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SYSCOMMAND = 0x0112
        SC_MONITORPOWER = 0xF170
        # 2 = POWER_OFF (关闭显示器)
        ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
        return True
    except Exception as exc:
        logger.warning("turn_off_display 调用失败: %s", exc)
        return False


def trim_process_memory() -> bool:
    """执行垃圾回收并释放进程的物理内存工作集 (Working Set Trimming).

    在主界面关闭/隐藏到后台时调用，通知 Windows 内存管理器回收未使用的物理页，
    使常驻托盘内存从数百兆瞬降至 20~35 MB 极轻量水平。
    """
    import gc
    gc.collect()
    try:
        from PySide6.QtGui import QPixmapCache
        QPixmapCache.clear()
    except Exception:
        pass

    if sys.platform != "win32":
        return True

    try:
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
        psapi.EmptyWorkingSet.restype = wintypes.BOOL

        h_proc = k32.GetCurrentProcess()
        res = psapi.EmptyWorkingSet(h_proc)
        return bool(res)
    except Exception as exc:
        logger.debug("EmptyWorkingSet 执行异常: %s", exc)
        return False


def toggle_desktop_icons_visibility() -> bool:
    """切换 Windows 桌面图标的显示与隐藏状态。返回切换后的可见性 (True 为显示, False 为隐藏)."""
    if sys.platform != "win32":
        return True

    try:
        user32 = ctypes.windll.user32
        # 确保当前线程附加到 Default 交互式桌面
        h_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_default:
            user32.SetThreadDesktop(h_default)

        def_view = 0
        # 1. 首先尝试从 Progman 查找
        progman = user32.FindWindowW("Progman", None)
        if progman:
            def_view = user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None)

        # 2. 若 Progman 下未找到 (Win10/Win11 常见移动到 WorkerW)
        if not def_view:
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.LPARAM)
            workers = []

            def enum_cb(h, lp):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(h, buf, 256)
                if buf.value == "WorkerW":
                    dv = user32.FindWindowExW(h, 0, "SHELLDLL_DefView", None)
                    if dv:
                        workers.append(dv)
                return 1

            user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
            if workers:
                def_view = workers[0]

        if not def_view:
            logger.warning("未找到桌面视图句柄 SHELLDLL_DefView")
            return True

        list_view = user32.FindWindowExW(def_view, 0, "SysListView32", None)
        target_hwnd = list_view or def_view

        # 判断当前是否可见
        is_visible = bool(user32.IsWindowVisible(target_hwnd))
        SW_HIDE = 0
        SW_SHOW = 5
        if is_visible:
            user32.ShowWindow(target_hwnd, SW_HIDE)
            logger.info("已通过全局快捷键隐藏 Windows 桌面图标")
            return False
        else:
            user32.ShowWindow(target_hwnd, SW_SHOW)
            logger.info("已通过全局快捷键恢复显示 Windows 桌面图标")
            return True
    except Exception as exc:
        logger.warning("切换桌面图标显示状态异常: %s", exc)
        return True


# ============================================================================
# Windows 系统消息处理与分发 (Qt QAbstractNativeEventFilter)
# ============================================================================

class Win32MessageDispatcher:
    """解析并分发关键 Windows 系统消息（可独立于 Qt 单独进行单元测试）."""

    def __init__(
        self,
        on_time_change: Callable[[], None] | None = None,
        on_display_change: Callable[[], None] | None = None,
        on_power_resume: Callable[[int], None] | None = None,
        on_session_unlock: Callable[[], None] | None = None,
        on_setting_change: Callable[[], None] | None = None,
        on_hotkey: Callable[[int], None] | None = None,
    ) -> None:
        self.on_time_change = on_time_change
        self.on_display_change = on_display_change
        self.on_power_resume = on_power_resume
        self.on_session_unlock = on_session_unlock
        self.on_setting_change = on_setting_change
        self.on_hotkey = on_hotkey

    def handle_win_message(self, msg_id: int, wparam: int = 0, lparam: int = 0) -> bool:
        """处理单个 Win32 消息 ID，若触发了业务回调则返回 True."""
        if msg_id == WM_TIMECHANGE:
            logger.info("收到系统消息 WM_TIMECHANGE (系统时间或时区发生变化)")
            if self.on_time_change:
                self.on_time_change()
            return True

        if msg_id == WM_DISPLAYCHANGE:
            logger.info("收到系统消息 WM_DISPLAYCHANGE (显示器连接、旋转或分辨率变化)")
            if self.on_display_change:
                self.on_display_change()
            return True

        if msg_id == WM_POWERBROADCAST:
            if wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND, PBT_APMRESUMECRITICAL):
                logger.info("收到系统电源恢复消息 WM_POWERBROADCAST (wParam=0x%04X)", wparam)
                if self.on_power_resume:
                    self.on_power_resume(wparam)
                return True

        if msg_id == WM_WTSSESSION_CHANGE:
            if wparam in (WTS_SESSION_UNLOCK, WTS_SESSION_LOGON):
                logger.info("收到会话恢复消息 WM_WTSSESSION_CHANGE (wParam=0x%04X)", wparam)
                if self.on_session_unlock:
                    self.on_session_unlock()
                return True

        if msg_id == WM_SETTINGCHANGE:
            if self.on_setting_change:
                self.on_setting_change()
            return True

        if msg_id == WM_HOTKEY:
            if self.on_hotkey:
                self.on_hotkey(int(wparam))
            return True

        return False


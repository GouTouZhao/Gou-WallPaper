"""配置文件读写、版本迁移与损坏恢复管理模块 (`app/settings_manager.py`).

满足需求：
- 保存在 `%LOCALAPPDATA%\\WallFlow\\config.json`
- 临时文件写入 + JSON 校验 + `os.fsync` + `os.replace` 原子替换
- 维护 `.bak` 备份；若配置文件损坏，保留损坏副本 `config.corrupted.<ts>.json` 并安全恢复默认/备份配置
- 支持版本迁移 (`config_version`)
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.logger import get_default_appdata_dir, get_logger

CURRENT_CONFIG_VERSION = 2


@dataclass
class FolderSourceConfig:
    """单个图片文件夹来源配置."""

    path: str
    enabled: bool = True
    recursive: bool = True
    valid_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "enabled": bool(self.enabled),
            "recursive": bool(self.recursive),
            "valid_count": int(self.valid_count),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FolderSourceConfig:
        return cls(
            path=str(data.get("path", "")),
            enabled=bool(data.get("enabled", True)),
            recursive=bool(data.get("recursive", True)),
            valid_count=int(data.get("valid_count", 0)),
        )


@dataclass
class ImageAdjustmentConfig:
    """非破坏性图片色彩调整参数 (-100 ~ +100)."""

    brightness: int = 0
    contrast: int = 0
    saturation: int = 0
    warmth: int = 0

    def is_identity(self) -> bool:
        return (
            self.brightness == 0
            and self.contrast == 0
            and self.saturation == 0
            and self.warmth == 0
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "brightness": max(-100, min(100, int(self.brightness))),
            "contrast": max(-100, min(100, int(self.contrast))),
            "saturation": max(-100, min(100, int(self.saturation))),
            "warmth": max(-100, min(100, int(self.warmth))),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ImageAdjustmentConfig:
        if not isinstance(data, dict):
            return cls()
        return cls(
            brightness=max(-100, min(100, int(data.get("brightness", 0)))),
            contrast=max(-100, min(100, int(data.get("contrast", 0)))),
            saturation=max(-100, min(100, int(data.get("saturation", 0)))),
            warmth=max(-100, min(100, int(data.get("warmth", 0)))),
        )


@dataclass
class WallpaperSettings:
    """桌面壁纸相关设置."""

    folders: list[FolderSourceConfig] = field(default_factory=list)
    recursive_default: bool = True
    playback_mode: str = "random"  # "random" | "sequential"
    display_mode: str = "fill"  # "fill" | "fit" | "stretch" | "center" | "tile" | "span"
    multi_monitor_mode: str = "different"  # "same" | "different" | "span"
    background_color: str = "#000000"
    collage_enabled: bool = False
    collage_count: int = 2  # 2 ~ 8
    adjustments: ImageAdjustmentConfig = field(default_factory=ImageAdjustmentConfig)
    current_wallpaper_paths: list[str] = field(default_factory=list)
    selector_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "folders": [f.to_dict() for f in self.folders],
            "recursive_default": bool(self.recursive_default),
            "playback_mode": self.playback_mode if self.playback_mode in ("random", "sequential") else "random",
            "display_mode": self.display_mode if self.display_mode in ("fill", "fit", "stretch", "center", "tile", "span") else "fill",
            "multi_monitor_mode": self.multi_monitor_mode if self.multi_monitor_mode in ("same", "different", "span") else "different",
            "background_color": str(self.background_color or "#000000"),
            "collage_enabled": bool(self.collage_enabled),
            "collage_count": max(2, min(8, int(self.collage_count))),
            "adjustments": self.adjustments.to_dict(),
            "current_wallpaper_paths": [str(p) for p in self.current_wallpaper_paths],
            "selector_state": copy.deepcopy(self.selector_state) if isinstance(self.selector_state, dict) else {},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> WallpaperSettings:
        if not isinstance(data, dict):
            return cls()
        raw_folders = data.get("folders", [])
        folders = [
            FolderSourceConfig.from_dict(item)
            for item in raw_folders
            if isinstance(item, dict) and item.get("path")
        ]
        playback_mode = str(data.get("playback_mode", "random"))
        if playback_mode not in ("random", "sequential"):
            playback_mode = "random"
        display_mode = str(data.get("display_mode", "fill"))
        if display_mode not in ("fill", "fit", "stretch", "center", "tile", "span"):
            display_mode = "fill"
        multi_monitor_mode = str(data.get("multi_monitor_mode", "different"))
        if multi_monitor_mode not in ("same", "different", "span"):
            multi_monitor_mode = "different"
        return cls(
            folders=folders,
            recursive_default=bool(data.get("recursive_default", True)),
            playback_mode=playback_mode,
            display_mode=display_mode,
            multi_monitor_mode=multi_monitor_mode,
            background_color=str(data.get("background_color", "#000000")),
            collage_enabled=bool(data.get("collage_enabled", False)),
            collage_count=max(2, min(8, int(data.get("collage_count", 2)))),
            adjustments=ImageAdjustmentConfig.from_dict(data.get("adjustments")),
            current_wallpaper_paths=[
                str(p) for p in data.get("current_wallpaper_paths", []) if isinstance(p, str)
            ],
            selector_state=data.get("selector_state", {}) if isinstance(data.get("selector_state"), dict) else {},
        )


@dataclass
class ScreensaverSettings:
    """屏幕保护程序相关设置."""

    use_wallpaper_library: bool = True
    folders: list[FolderSourceConfig] = field(default_factory=list)
    recursive_default: bool = True
    playback_mode: str = "random"  # "random" | "sequential"
    interval_seconds: int = 10  # 屏保内部图片切换间隔
    display_mode: str = "fit"  # "fill" | "fit" | "stretch" | "center"
    independent_per_monitor: bool = True
    match_orientation: bool = False  # 是否启用横竖屏方向智能匹配
    background_color: str = "#000000"
    transition_effect: str = "fade"  # "none" | "fade" | "slide" | "zoom"
    transition_duration_ms: int = 800
    exit_on_mouse_move: bool = True
    show_clock_overlay: bool = False
    clock_size: str = "medium"  # "small" | "medium" | "large" | "xlarge"
    clock_position: str = "bottom_left"  # "bottom_left" | "center_top" | "center_bottom"
    idle_enabled: bool = True
    timeout_seconds: int = 60  # 空闲自动启动屏保等待时间 (秒)
    suppress_when_media_playing: bool = True  # 检测到系统正在播放音频/视频 (如抖音、音乐、电影) 时阻止屏保
    suppress_when_fullscreen: bool = True  # 检测到全屏应用 (游戏、全屏播放器、PPT演示) 时阻止屏保
    prevent_system_sleep: bool = True  # 屏保运行时是否阻止系统休眠/息屏
    timed_sleep_minutes: int = 30  # 屏保运行指定分钟后进入睡眠 (0 为从不/一直保持屏保)
    timed_sleep_action: str = "sleep"  # "sleep" (系统睡眠) | "turn_off_screen" (关闭屏幕) | "allow_sleep" (解除阻止由系统接管)
    selector_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "use_wallpaper_library": bool(self.use_wallpaper_library),
            "folders": [f.to_dict() for f in self.folders],
            "recursive_default": bool(self.recursive_default),
            "playback_mode": self.playback_mode if self.playback_mode in ("random", "sequential") else "random",
            "interval_seconds": max(3, int(self.interval_seconds)),
            "display_mode": self.display_mode if self.display_mode in ("fill", "fit", "stretch", "center") else "fit",
            "independent_per_monitor": bool(self.independent_per_monitor),
            "match_orientation": bool(self.match_orientation),
            "background_color": str(self.background_color or "#000000"),
            "transition_effect": self.transition_effect if self.transition_effect in ("none", "fade", "slide", "zoom") else "fade",
            "transition_duration_ms": max(0, min(5000, int(self.transition_duration_ms))),
            "exit_on_mouse_move": bool(self.exit_on_mouse_move),
            "show_clock_overlay": bool(self.show_clock_overlay),
            "clock_size": self.clock_size if self.clock_size in ("small", "medium", "large", "xlarge") else "medium",
            "clock_position": self.clock_position if self.clock_position in ("bottom_left", "center_top", "center_bottom") else "bottom_left",
            "idle_enabled": bool(self.idle_enabled),
            "timeout_seconds": max(10, int(self.timeout_seconds)),
            "suppress_when_media_playing": bool(self.suppress_when_media_playing),
            "suppress_when_fullscreen": bool(self.suppress_when_fullscreen),
            "prevent_system_sleep": bool(self.prevent_system_sleep),
            "timed_sleep_minutes": max(0, int(self.timed_sleep_minutes)),
            "timed_sleep_action": self.timed_sleep_action if self.timed_sleep_action in ("sleep", "turn_off_screen", "allow_sleep") else "sleep",
            "selector_state": copy.deepcopy(self.selector_state) if isinstance(self.selector_state, dict) else {},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ScreensaverSettings:
        if not isinstance(data, dict):
            return cls()
        raw_folders = data.get("folders", [])
        folders = [
            FolderSourceConfig.from_dict(item)
            for item in raw_folders
            if isinstance(item, dict) and item.get("path")
        ]
        playback_mode = str(data.get("playback_mode", "random"))
        if playback_mode not in ("random", "sequential"):
            playback_mode = "random"
        display_mode = str(data.get("display_mode", "fit"))
        if display_mode not in ("fill", "fit", "stretch", "center"):
            display_mode = "fit"
        transition_effect = str(data.get("transition_effect", "fade"))
        if transition_effect not in ("none", "fade", "slide", "zoom"):
            transition_effect = "fade"
        clock_size = str(data.get("clock_size", "medium"))
        if clock_size not in ("small", "medium", "large", "xlarge"):
            clock_size = "medium"
        clock_pos = str(data.get("clock_position", "bottom_left"))
        if clock_pos not in ("bottom_left", "center_top", "center_bottom"):
            clock_pos = "bottom_left"
        return cls(
            use_wallpaper_library=bool(data.get("use_wallpaper_library", True)),
            folders=folders,
            recursive_default=bool(data.get("recursive_default", True)),
            playback_mode=playback_mode,
            interval_seconds=max(3, int(data.get("interval_seconds", 10))),
            display_mode=display_mode,
            independent_per_monitor=bool(data.get("independent_per_monitor", True)),
            match_orientation=bool(data.get("match_orientation", False)),
            background_color=str(data.get("background_color", "#000000")),
            transition_effect=transition_effect,
            transition_duration_ms=max(0, min(5000, int(data.get("transition_duration_ms", 800)))),
            exit_on_mouse_move=bool(data.get("exit_on_mouse_move", True)),
            show_clock_overlay=bool(data.get("show_clock_overlay", False)),
            clock_size=clock_size,
            clock_position=clock_pos,
            idle_enabled=bool(data.get("idle_enabled", True)),
            timeout_seconds=max(10, int(data.get("timeout_seconds", 60))),
            suppress_when_media_playing=bool(data.get("suppress_when_media_playing", True)),
            suppress_when_fullscreen=bool(data.get("suppress_when_fullscreen", True)),
            prevent_system_sleep=bool(data.get("prevent_system_sleep", True)),
            timed_sleep_minutes=max(0, int(data.get("timed_sleep_minutes", 30))),
            timed_sleep_action=str(data.get("timed_sleep_action", "sleep")),
            selector_state=data.get("selector_state", {}) if isinstance(data.get("selector_state"), dict) else {},
        )


@dataclass
class SchedulerSettings:
    """壁纸轮播调度持久化状态."""

    enabled: bool = True
    paused: bool = False
    interval_seconds: int = 1800  # 默认 30 分钟；>= 21600 自动切换为系统时间戳调度
    scheduler_mode: str = "monotonic"  # "monotonic" | "timestamp_utc"
    last_success_at_utc: str = ""
    next_due_at_utc: str = ""
    last_check_at_utc: str = ""
    consecutive_failures: int = 0
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        mode = "timestamp_utc" if int(self.interval_seconds) >= 21600 else "monotonic"
        return {
            "enabled": bool(self.enabled),
            "paused": bool(self.paused),
            "interval_seconds": max(5, int(self.interval_seconds)),
            "scheduler_mode": mode,
            "last_success_at_utc": str(self.last_success_at_utc or ""),
            "next_due_at_utc": str(self.next_due_at_utc or ""),
            "last_check_at_utc": str(self.last_check_at_utc or ""),
            "consecutive_failures": max(0, int(self.consecutive_failures)),
            "last_error": str(self.last_error or ""),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SchedulerSettings:
        if not isinstance(data, dict):
            return cls()
        interval = max(5, int(data.get("interval_seconds", 1800)))
        mode = "timestamp_utc" if interval >= 21600 else "monotonic"
        return cls(
            enabled=bool(data.get("enabled", True)),
            paused=bool(data.get("paused", False)),
            interval_seconds=interval,
            scheduler_mode=mode,
            last_success_at_utc=str(data.get("last_success_at_utc", "")),
            next_due_at_utc=str(data.get("next_due_at_utc", "")),
            last_check_at_utc=str(data.get("last_check_at_utc", "")),
            consecutive_failures=max(0, int(data.get("consecutive_failures", 0))),
            last_error=str(data.get("last_error", "")),
        )


@dataclass
class SystemSettings:
    """系统集成、缓存、快捷键与日志设置."""

    autostart_enabled: bool = False
    start_minimized: bool = True
    close_to_tray: bool = True
    thumbnail_cache_max_mb: int = 128
    render_cache_max_items: int = 12
    hotkeys_enabled: bool = False
    hotkey_next: str = "Ctrl+Alt+Right"
    hotkey_prev: str = "Ctrl+Alt+Left"
    hotkey_random: str = "Ctrl+Alt+R"
    hotkey_toggle_pause: str = "Ctrl+Alt+P"
    hotkey_toggle_desktop_icons: str = "Ctrl+Alt+H"
    log_level: str = "INFO"
    log_max_mb: int = 5
    log_backup_count: int = 5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SystemSettings:
        if not isinstance(data, dict):
            return cls()
        return cls(
            autostart_enabled=bool(data.get("autostart_enabled", False)),
            start_minimized=bool(data.get("start_minimized", True)),
            close_to_tray=bool(data.get("close_to_tray", True)),
            thumbnail_cache_max_mb=max(16, min(1024, int(data.get("thumbnail_cache_max_mb", 128)))),
            render_cache_max_items=max(2, min(64, int(data.get("render_cache_max_items", 12)))),
            hotkeys_enabled=bool(data.get("hotkeys_enabled", False)),
            hotkey_next=str(data.get("hotkey_next", "Ctrl+Alt+Right")),
            hotkey_prev=str(data.get("hotkey_prev", "Ctrl+Alt+Left")),
            hotkey_random=str(data.get("hotkey_random", "Ctrl+Alt+R")),
            hotkey_toggle_pause=str(data.get("hotkey_toggle_pause", "Ctrl+Alt+P")),
            hotkey_toggle_desktop_icons=str(data.get("hotkey_toggle_desktop_icons", "Ctrl+Alt+H")),
            log_level=str(data.get("log_level", "INFO")).upper(),
            log_max_mb=max(1, min(50, int(data.get("log_max_mb", 5)))),
            log_backup_count=max(1, min(20, int(data.get("log_backup_count", 5)))),
        )


@dataclass
class AppConfig:
    """顶层应用配置对象."""

    config_version: int = CURRENT_CONFIG_VERSION
    wallpaper: WallpaperSettings = field(default_factory=WallpaperSettings)
    screensaver: ScreensaverSettings = field(default_factory=ScreensaverSettings)
    scheduler: SchedulerSettings = field(default_factory=SchedulerSettings)
    system: SystemSettings = field(default_factory=SystemSettings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": int(self.config_version),
            "wallpaper": self.wallpaper.to_dict(),
            "screensaver": self.screensaver.to_dict(),
            "scheduler": self.scheduler.to_dict(),
            "system": self.system.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AppConfig:
        if not isinstance(data, dict):
            return cls()
        migrated = migrate_config_dict(data)
        return cls(
            config_version=CURRENT_CONFIG_VERSION,
            wallpaper=WallpaperSettings.from_dict(migrated.get("wallpaper")),
            screensaver=ScreensaverSettings.from_dict(migrated.get("screensaver")),
            scheduler=SchedulerSettings.from_dict(migrated.get("scheduler")),
            system=SystemSettings.from_dict(migrated.get("system")),
        )


def migrate_config_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """将旧版本（如 v1 扁平字段）配置平滑迁移到最新结构 (`CURRENT_CONFIG_VERSION`)."""
    data = copy.deepcopy(raw)
    version = int(data.get("config_version", 1))

    if version < 2:
        # 兼容 v1 顶层扁平字段迁移
        wp = data.setdefault("wallpaper", {})
        if not isinstance(wp, dict):
            wp = {}
            data["wallpaper"] = wp

        if "wallpaper_folders" in data and "folders" not in wp:
            folders = []
            for item in data["wallpaper_folders"]:
                if isinstance(item, str):
                    folders.append({"path": item, "enabled": True, "recursive": True})
                elif isinstance(item, dict):
                    folders.append(item)
            wp["folders"] = folders

        sched = data.setdefault("scheduler", {})
        if not isinstance(sched, dict):
            sched = {}
            data["scheduler"] = sched

        if "interval_seconds" in data and "interval_seconds" not in sched:
            sched["interval_seconds"] = data["interval_seconds"]
        if "last_success_at_utc" in data and "last_success_at_utc" not in sched:
            sched["last_success_at_utc"] = data["last_success_at_utc"]
        if "next_due_at_utc" in data and "next_due_at_utc" not in sched:
            sched["next_due_at_utc"] = data["next_due_at_utc"]

        data["config_version"] = CURRENT_CONFIG_VERSION

    return data


class SettingsManager:
    """线程安全、原子持久化的配置管理器."""

    def __init__(self, config_dir: Path | str | None = None, filename: str = "config.json") -> None:
        self._logger = get_logger("settings")
        if config_dir is None:
            self._config_dir = get_default_appdata_dir()
        else:
            self._config_dir = Path(config_dir)
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = self._config_dir / filename
        self._backup_path = self._config_dir / f"{filename}.bak"
        self._lock = threading.RLock()
        self.last_corrupted_backup: Path | None = None
        self.recovered_from_backup: bool = False
        self.config: AppConfig = self.load()

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def backup_path(self) -> Path:
        return self._backup_path

    @property
    def cache_dir(self) -> Path:
        path = self._config_dir / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def load(self) -> AppConfig:
        """从磁盘加载配置；若主文件损坏，保留损坏副本并尝试从备份或默认值恢复."""
        with self._lock:
            self.recovered_from_backup = False
            if not self._config_path.exists():
                if self._backup_path.exists():
                    try:
                        data = self._read_and_validate_json(self._backup_path)
                        self.config = AppConfig.from_dict(data)
                        self.recovered_from_backup = True
                        self.save()
                        self._logger.warning("主配置文件不存在，已从备份 %s 恢复配置", self._backup_path)
                        return self.config
                    except Exception as exc:
                        self._logger.warning("尝试从备份恢复失败: %s", exc)
                self.config = AppConfig()
                self.save()
                self._logger.info("首次创建默认配置文件: %s", self._config_path)
                return self.config

            try:
                data = self._read_and_validate_json(self._config_path)
                self.config = AppConfig.from_dict(data)
                self._logger.info("成功加载配置文件: %s", self._config_path)
                return self.config
            except Exception as exc:
                self._logger.error("配置文件损坏 (%s): %s，开始执行异常恢复流程", self._config_path, exc)
                self._preserve_corrupted_file(self._config_path)

                # 尝试从 .bak 备份恢复
                if self._backup_path.exists():
                    try:
                        bak_data = self._read_and_validate_json(self._backup_path)
                        self.config = AppConfig.from_dict(bak_data)
                        self.recovered_from_backup = True
                        self.save()
                        self._logger.warning("已从备份文件 %s 成功恢复配置", self._backup_path)
                        return self.config
                    except Exception as bak_exc:
                        self._logger.error("备份配置文件同样损坏 (%s): %s", self._backup_path, bak_exc)

                # 回退安全默认值
                self.config = AppConfig()
                self.save()
                self._logger.warning("已重置为安全默认配置并写入 %s", self._config_path)
                return self.config

    def save(self, config: AppConfig | None = None) -> bool:
        """原子保存配置到磁盘（写临时文件 -> 校验 JSON -> fsync -> 备份旧文件 -> os.replace）."""
        with self._lock:
            if config is not None:
                self.config = config

            payload_dict = self.config.to_dict()
            json_text = json.dumps(payload_dict, ensure_ascii=False, indent=2)

            # 写入前再次校验 JSON 可反序列化
            parsed_check = json.loads(json_text)
            if not isinstance(parsed_check, dict):
                raise ValueError("序列化后的配置不是合法 JSON 对象")

            self._config_dir.mkdir(parents=True, exist_ok=True)
            pid = os.getpid()
            tid = threading.get_ident()
            tmp_path = self._config_dir / f".{self._config_path.name}.{pid}.{tid}.tmp"

            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(json_text)
                    f.flush()
                    os.fsync(f.fileno())

                # 若当前主文件有效，先更新 .bak 备份
                if self._config_path.exists():
                    try:
                        self._read_and_validate_json(self._config_path)
                        shutil.copy2(self._config_path, self._backup_path)
                    except Exception:
                        pass

                os.replace(tmp_path, self._config_path)
                try:
                    shutil.copy2(self._config_path, self._backup_path)
                except OSError:
                    pass
                return True
            except Exception as exc:
                self._logger.error("保存配置文件失败: %s", exc)
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                return False

    def _read_and_validate_json(self, path: Path) -> dict[str, Any]:
        raw_text = path.read_text(encoding="utf-8")
        if not raw_text.strip():
            raise ValueError("配置文件为空")
        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            raise ValueError("配置文件根节点必须是 JSON Object (dict)")
        return parsed

    def _preserve_corrupted_file(self, path: Path) -> None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        corrupted_copy = self._config_dir / f"{path.stem}.corrupted.{timestamp}{path.suffix}"
        counter = 1
        while corrupted_copy.exists():
            corrupted_copy = self._config_dir / f"{path.stem}.corrupted.{timestamp}_{counter}{path.suffix}"
            counter += 1
        try:
            shutil.copy2(path, corrupted_copy)
            self.last_corrupted_backup = corrupted_copy
            self._logger.warning("已保留损坏的配置文件副本供排查: %s", corrupted_copy)
        except OSError as exc:
            self._logger.error("保留损坏配置文件副本失败: %s", exc)


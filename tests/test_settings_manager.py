"""配置管理器与损坏自愈测试 (`tests/test_settings_manager.py`)."""

from __future__ import annotations

import json
from pathlib import Path

from app.settings_manager import AppConfig, FolderSourceConfig, SettingsManager


def test_settings_initial_creation(temp_dir: Path):
    mgr = SettingsManager(config_dir=temp_dir)
    assert mgr.config_path.exists()
    assert mgr.config.config_version == 2
    assert mgr.config.scheduler.interval_seconds == 1800


def test_settings_atomic_save(temp_dir: Path):
    mgr = SettingsManager(config_dir=temp_dir)
    mgr.config.wallpaper.display_mode = "fit"
    mgr.config.scheduler.interval_seconds = 21600
    mgr.config.wallpaper.folders = [FolderSourceConfig(path=r"C:\Wallpapers", enabled=True, recursive=True)]
    ok = mgr.save()
    assert ok is True

    # 重新加载验证
    mgr2 = SettingsManager(config_dir=temp_dir)
    assert mgr2.config.wallpaper.display_mode == "fit"
    assert mgr2.config.scheduler.interval_seconds == 21600
    assert mgr2.config.scheduler.scheduler_mode == "timestamp_utc"
    assert len(mgr2.config.wallpaper.folders) == 1
    assert mgr2.config.wallpaper.folders[0].path == r"C:\Wallpapers"


def test_corrupted_config_recovery_from_backup(temp_dir: Path):
    mgr = SettingsManager(config_dir=temp_dir)
    mgr.config.wallpaper.display_mode = "stretch"
    mgr.save()
    assert mgr.backup_path.exists()

    # 人为损坏主配置文件 (写入非法 JSON)
    mgr.config_path.write_text("CORRUPTED_JSON_CONTENT{{{[[", encoding="utf-8")

    # 再次加载，应保留损坏文件并从 .bak 恢复
    mgr2 = SettingsManager(config_dir=temp_dir)
    assert mgr2.recovered_from_backup is True
    assert mgr2.config.wallpaper.display_mode == "stretch"
    assert mgr2.last_corrupted_backup is not None
    assert mgr2.last_corrupted_backup.exists()
    assert "corrupted" in mgr2.last_corrupted_backup.name


def test_corrupted_config_recovery_to_safe_defaults(temp_dir: Path):
    mgr = SettingsManager(config_dir=temp_dir)
    mgr.save()

    # 人为同时损坏主配置与备份文件
    mgr.config_path.write_text("NOT_JSON", encoding="utf-8")
    if mgr.backup_path.exists():
        mgr.backup_path.write_text("NOT_JSON_BAK", encoding="utf-8")

    mgr2 = SettingsManager(config_dir=temp_dir)
    assert mgr2.config is not None
    assert mgr2.config.config_version == 2
    assert mgr2.config_path.exists()
    # 重新解析磁盘文件确认已安全覆写为有效 JSON
    with open(mgr2.config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)


def test_config_version_migration(temp_dir: Path):
    cfg_file = temp_dir / "config.json"
    v1_data = {
        "config_version": 1,
        "wallpaper_folders": ["D:\\Images1", {"path": "D:\\Images2", "enabled": True}],
        "interval_seconds": 7200,
        "last_success_at_utc": "2026-10-29T10:00:00+00:00",
    }
    cfg_file.write_text(json.dumps(v1_data), encoding="utf-8")

    mgr = SettingsManager(config_dir=temp_dir)
    assert mgr.config.config_version == 2
    assert mgr.config.scheduler.interval_seconds == 7200
    assert len(mgr.config.wallpaper.folders) == 2
    assert mgr.config.wallpaper.folders[0].path == "D:\\Images1"
    assert mgr.config.scheduler.last_success_at_utc == "2026-10-29T10:00:00+00:00"


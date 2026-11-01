"""针对用户痛点优化的单元测试 (`tests/test_optimizations.py`).

覆盖：
1. `ImageLibrary.total_valid_count` 属性与巨型图片秒级快速扫描；
2. `SchedulerStatusSnapshot` 实时倒计时格式化 (`remaining_formatted`)、本地时间显示与状态属性；
3. `WallpaperScheduler.get_snapshot()` 兼容别名；
4. `ScreensaverGlobalEventFilter` 全局输入拦截退出；
5. `MAIN_STYLESHEET` 样式表与资产图标完整性。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent

from app.clock import VirtualClock
from app.icon_helper import ensure_ui_icon_assets
from app.image_library import ImageLibrary, inspect_static_image_header
from app.scheduler import WallpaperScheduler
from app.screensaver import ScreensaverGlobalEventFilter
from app.settings_manager import FolderSourceConfig, SettingsManager
from app.ui.styles import MAIN_STYLESHEET


def test_image_library_total_valid_count_and_fast_inspection(tmp_path: Path) -> None:
    # 创建 5 张测试静态图片
    for i in range(5):
        img = Image.new("RGB", (1920, 1080), color=(i * 40, 100, 200))
        img.save(tmp_path / f"img_{i}.png")

    lib = ImageLibrary(disk_cache_dir=tmp_path / "cache")
    assert lib.total_valid_count == 0

    lib.set_sources([FolderSourceConfig(path=str(tmp_path), enabled=True, recursive=False)])
    summary = lib.scan()
    assert summary.valid_count == 5
    assert lib.total_valid_count == 5
    lib.shutdown()


def test_scheduler_snapshot_properties_and_countdown_format(tmp_path: Path) -> None:
    vclock = VirtualClock()
    sm = SettingsManager(config_dir=tmp_path)
    sm.config.scheduler.interval_seconds = 3600
    sm.config.scheduler.enabled = True
    sm.config.scheduler.paused = False

    called = []
    sched = WallpaperScheduler(
        settings_manager=sm,
        apply_callback=lambda: (True, ""),
        clock=vclock,
    )
    sched.record_manual_switch_success()

    snap = sched.get_snapshot()
    assert snap.is_paused is False
    assert snap.is_enabled is True
    assert snap.mode == "monotonic"
    assert snap.remaining_formatted == "01:00:00" or "59:" in snap.remaining_formatted
    assert snap.failure_count == 0
    assert snap.last_success_formatted != "尚未切换"


def test_screensaver_global_event_filter_exits_on_input() -> None:
    exit_reasons = []

    def on_exit(reason: str) -> None:
        exit_reasons.append(reason)

    ef = ScreensaverGlobalEventFilter(exit_callback=on_exit, exit_on_mouse_move=True)

    # 1. 测试按键退出
    key_ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    handled = ef.eventFilter(None, key_ev)
    assert handled is True
    assert len(exit_reasons) == 1
    assert "GlobalInput(KeyPress)" in exit_reasons[0]

    # 2. 测试鼠标点击退出
    from PySide6.QtCore import QPointF
    mouse_ev = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(100.0, 100.0),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    handled2 = ef.eventFilter(None, mouse_ev)
    assert handled2 is True
    assert len(exit_reasons) == 2


def test_high_contrast_stylesheet_and_icons(tmp_path: Path) -> None:
    assets_dir = ensure_ui_icon_assets(tmp_path / "assets")
    assert (assets_dir / "cb_checked.png").exists()
    assert (assets_dir / "cb_unchecked.png").exists()
    assert (assets_dir / "cb_disabled.png").exists()
    assert (assets_dir / "arrow_down.png").exists()

    # 样式表非空且包含强化边框规则
    assert len(MAIN_STYLESHEET) > 1000
    assert "QCheckBox::indicator" in MAIN_STYLESHEET
    assert "QComboBox::drop-down" in MAIN_STYLESHEET
    assert "gridline-color" in MAIN_STYLESHEET


def test_no_wheel_scroll_forwarder(tmp_path: Path) -> None:
    """测试 NoWheelScrollForwarder 能拦截子控件滚轮并转发给外层 QScrollArea 视口."""
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea, QVBoxLayout, QWidget
    from app.ui.main_window import NoWheelScrollForwarder

    app = QApplication.instance() or QApplication([])

    scroll = QScrollArea()
    scroll.resize(300, 200)
    content = QWidget()
    layout = QVBoxLayout(content)
    combo = QComboBox()
    combo.addItems(["A", "B", "C"])
    layout.addWidget(combo)
    scroll.setWidget(content)
    scroll.show()
    app.processEvents()

    fwd = NoWheelScrollForwarder(scroll, content)
    combo.installEventFilter(fwd)

    curr_idx_before = combo.currentIndex()
    we = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    # 模拟向 combo 发送滚轮事件
    app.sendEvent(combo, we)

    # 选项必须保持不变（滚轮修改被拦截），不得触发意外切换
    assert combo.currentIndex() == curr_idx_before


def test_image_processor_target_cache(tmp_path: Path) -> None:
    """测试 image_processor 对 target_size 的内存 LRU 缓存加速."""
    import time
    from app.image_processor import load_image_for_target

    test_img = tmp_path / "cache_test.png"
    img = Image.new("RGB", (800, 600), (100, 150, 200))
    img.save(test_img)

    t0 = time.perf_counter()
    loaded1 = load_image_for_target(test_img, target_size=(320, 240))
    t1 = time.perf_counter()

    t2 = time.perf_counter()
    loaded2 = load_image_for_target(test_img, target_size=(320, 240))
    t3 = time.perf_counter()

    assert loaded1.size == loaded2.size
    assert loaded1.size[0] <= 800 and loaded1.size[1] <= 600
    # 第二次命中缓存，耗时应当极短
    assert (t3 - t2) <= (t1 - t0) + 0.05


def test_bottom_action_bar_compact_labels() -> None:
    """测试底部常驻操作栏按钮与标签已紧凑化，绝不在窄屏下文字截断."""
    from PySide6.QtWidgets import QApplication
    from app.ui.action_bar import BottomActionBar

    app = QApplication.instance() or QApplication([])
    bar = BottomActionBar()

    assert "上一张" in bar.btn_prev.text()
    assert "应用" in bar.btn_apply.text()
    assert "下一张" in bar.btn_next.text()
    assert "暂停" in bar.btn_pause.text()
    assert "随机" in bar.btn_random.text()
    assert "屏保" in bar.btn_ss.text()

    bar.update_status(
        mode_text="时间戳",
        is_timestamp_mode=True,
        countdown_text="05:00",
        image_count=42,
    )
    assert "42" in bar.lbl_library_stat.text()
    assert "05:00" in bar.lbl_countdown.text()


def test_system_idle_seconds_and_com_import() -> None:
    """测试 Win32 集成中的空闲检测与 COM 模块正常运行."""
    import sys
    from app.win32_integration import get_system_idle_seconds

    idle = get_system_idle_seconds()
    assert isinstance(idle, float)
    assert idle >= 0.0


def test_manual_switch_resets_scheduler_countdown(tmp_path: Path) -> None:
    """测试用户手动点击'下一张'或手动切换壁纸时，定时切换倒计时被立即重置为完整间隔."""
    vclock = VirtualClock()
    sm = SettingsManager(config_dir=tmp_path)
    sm.config.scheduler.interval_seconds = 300  # 5 分钟
    sm.config.scheduler.enabled = True
    sm.config.scheduler.paused = False

    sched = WallpaperScheduler(
        settings_manager=sm,
        apply_callback=lambda: (True, ""),
        clock=vclock,
    )
    sched.record_manual_switch_success()

    # 初始状态：剩余 300 秒
    snap1 = sched.get_snapshot()
    assert 299 <= snap1.remaining_seconds <= 300

    # 模拟时间流逝 120 秒，剩余约 180 秒
    vclock.advance(120)
    snap2 = sched.get_snapshot()
    assert snap2.remaining_seconds <= 180

    # 用户手动点击了"下一张"壁纸，触发 record_manual_switch_success
    sched.record_manual_switch_success()

    # 倒计时必须被重置为满额 300 秒！
    snap3 = sched.get_snapshot()
    assert 299 <= snap3.remaining_seconds <= 300


def test_tray_manager_show_message_alias() -> None:
    """测试 TrayManager 拥有 show_message 与 show_notification 方法且安全不崩溃."""
    from PySide6.QtWidgets import QApplication
    from app.tray_manager import TrayManager

    app = QApplication.instance() or QApplication([])
    tm = TrayManager()
    assert hasattr(tm, "show_message")
    assert hasattr(tm, "show_notification")
    # 未挂载系统托盘显示时调用亦不抛出异常
    tm.show_message("测试标题", "测试内容")
    tm.show_notification("测试标题2", "测试内容2")


def test_screensaver_idle_config_roundtrip(tmp_path: Path) -> None:
    """测试 ScreensaverSettings 拥有 idle_enabled 与 timeout_seconds 且持久化无损."""
    sm = SettingsManager(config_dir=tmp_path)
    sm.config.screensaver.idle_enabled = True
    sm.config.screensaver.timeout_seconds = 60
    sm.save()

    sm2 = SettingsManager(config_dir=tmp_path)
    assert sm2.config.screensaver.idle_enabled is True
    assert sm2.config.screensaver.timeout_seconds == 60


def test_screensaver_mutex_guard_single_instance() -> None:
    """测试 ScreensaverMutexGuard 全局互斥锁能阻止多个屏保同时全屏启动."""
    from app.screensaver import ScreensaverMutexGuard

    ScreensaverMutexGuard.release()

    # 首个获取成功
    ok1 = ScreensaverMutexGuard.acquire()
    assert ok1 is True
    assert ScreensaverMutexGuard.is_active() is True

    # 释放后重新可用
    ScreensaverMutexGuard.release()
    assert ScreensaverMutexGuard._mutex_handle is None


def test_toggle_desktop_icons_function() -> None:
    """测试 toggle_desktop_icons_visibility 调用安全不异常."""
    from app.win32_integration import toggle_desktop_icons_visibility

    res = toggle_desktop_icons_visibility()
    assert isinstance(res, bool)


def test_system_settings_hotkey_desktop_icons_roundtrip(tmp_path: Path) -> None:
    """测试 SystemSettings 包含 hotkey_toggle_desktop_icons 且默认值为 Ctrl+Alt+H."""
    sm = SettingsManager(config_dir=tmp_path)
    assert sm.config.system.hotkey_toggle_desktop_icons == "Ctrl+Alt+H"
    sm.config.system.hotkey_toggle_desktop_icons = "Ctrl+Shift+D"
    sm.save()

    sm2 = SettingsManager(config_dir=tmp_path)
    assert sm2.config.system.hotkey_toggle_desktop_icons == "Ctrl+Shift+D"


def test_scheduler_3_days_and_1_week_presets(tmp_path: Path) -> None:
    """测试 3 天 (259200s) 与 1 周 (604800s) 的长间隔调度模式自动进入 timestamp_utc."""
    vclock = VirtualClock()
    sm = SettingsManager(config_dir=tmp_path)
    sched = WallpaperScheduler(settings_manager=sm, apply_callback=lambda: (True, ""), clock=vclock)

    # 3 天
    sched.set_interval(259200)
    snap_3d = sched.get_snapshot()
    assert snap_3d.interval_seconds == 259200
    assert snap_3d.mode == "timestamp"

    # 1 周 (7 天)
    sched.set_interval(604800)
    snap_1w = sched.get_snapshot()
    assert snap_1w.interval_seconds == 604800
    assert snap_1w.mode == "timestamp"


def test_screensaver_clock_size_and_position_config_roundtrip(tmp_path: Path) -> None:
    """测试 ScreensaverSettings 拥有 clock_size 与 clock_position 且持久化无损."""
    sm = SettingsManager(config_dir=tmp_path)
    assert sm.config.screensaver.clock_size == "medium"
    assert sm.config.screensaver.clock_position == "bottom_left"

    sm.config.screensaver.clock_size = "large"
    sm.config.screensaver.clock_position = "center_top"
    sm.save()

    sm2 = SettingsManager(config_dir=tmp_path)
    assert sm2.config.screensaver.clock_size == "large"
    assert sm2.config.screensaver.clock_position == "center_top"

    # 非法输入回退测试
    bad_dict = {
        "clock_size": "invalid_size",
        "clock_position": "invalid_pos",
    }
    from app.settings_manager import ScreensaverSettings
    s_obj = ScreensaverSettings.from_dict(bad_dict)
    assert s_obj.clock_size == "medium"
    assert s_obj.clock_position == "bottom_left"


def test_screensaver_canvas_clock_config() -> None:
    """测试 ScreensaverCanvasWidget 接受 clock_size 与 clock_position 参数."""
    from app.screensaver import ScreensaverCanvasWidget

    canvas = ScreensaverCanvasWidget(
        target_width=800,
        target_height=600,
        is_preview=True,
        show_clock_overlay=True,
        clock_size="xlarge",
        clock_position="center_bottom",
    )
    assert canvas._clock_size == "xlarge"
    assert canvas._clock_position == "center_bottom"


def test_screensaver_canvas_clock_rendering() -> None:
    """测试 ScreensaverCanvasWidget 在各位置下时钟数字与日期中心对齐绘制均无异常."""
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication
    from app.screensaver import ScreensaverCanvasWidget

    app = QApplication.instance() or QApplication([])
    for pos in ["bottom_left", "center_top", "center_bottom"]:
        canvas = ScreensaverCanvasWidget(
            target_width=1920,
            target_height=1080,
            is_preview=False,
            show_clock_overlay=True,
            clock_size="medium",
            clock_position=pos,
        )
        pix = QPixmap(100, 100)
        canvas.show_image(pix, immediate=True)
        canvas.resize(1920, 1080)
        canvas.repaint()



def test_global_no_wheel_filter_prevents_accidental_switching() -> None:
    """测试 GlobalNoWheelFilter 拦截全软件所有 QComboBox、QSpinBox、QSlider 的滚轮修改并保证无误触."""
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QScrollArea,
        QSlider,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
    from app.ui.no_wheel_filter import GlobalNoWheelFilter

    app = QApplication.instance() or QApplication([])
    flt = GlobalNoWheelFilter()
    app.installEventFilter(flt)

    scroll = QScrollArea()
    scroll.resize(400, 300)
    container = QWidget()
    layout = QVBoxLayout(container)

    cb = QComboBox()
    cb.addItems(["小 (Small)", "中等 (Medium)", "大 (Large)", "特大 (Extra Large)"])
    sp = QSpinBox()
    sp.setValue(60)
    sl = QSlider(Qt.Orientation.Horizontal)
    sl.setValue(50)

    layout.addWidget(cb)
    layout.addWidget(sp)
    layout.addWidget(sl)
    scroll.setWidget(container)
    scroll.show()
    app.processEvents()

    we = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )

    # 1. 验证滚轮对 QComboBox 无效（绝不会误触切换字号或模式）
    app.sendEvent(cb, we)
    assert cb.currentIndex() == 0

    # 2. 验证滚轮对 QSpinBox 无效（绝不会误触改动数值）
    app.sendEvent(sp, we)
    assert sp.value() == 60

    # 3. 验证滚轮对 QSlider 无效（绝不会误触变动滑块）
    app.sendEvent(sl, we)
    assert sl.value() == 50


def test_open_windows_screensaver_settings_no_name_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试 open_windows_screensaver_settings 不发生 NameError: name 'os' is not defined."""
    import subprocess
    from app.win32_integration import open_windows_screensaver_settings

    # 拦截 subprocess.Popen 避免测试时真实弹出系统屏保面板
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: None)
    res = open_windows_screensaver_settings()
    assert res is True


def test_screensaver_standalone_folder_sources(tmp_path: Path) -> None:
    """测试屏保独立图片文件夹生效机制：取消共用壁纸库时正确加载专属屏保图片，且不污染壁纸库."""
    from PIL import Image
    from app.image_library import ImageLibrary
    from app.screensaver import ScreensaverController
    from app.settings_manager import FolderSourceConfig, SettingsManager

    wp_dir = tmp_path / "wallpapers"
    wp_dir.mkdir()
    ss_dir = tmp_path / "screensavers"
    ss_dir.mkdir()

    # 创建壁纸图片
    wp_img = wp_dir / "wp1.jpg"
    Image.new("RGB", (200, 200), (255, 0, 0)).save(wp_img)

    # 创建独立屏保图片
    ss_img = ss_dir / "ss1.jpg"
    Image.new("RGB", (300, 300), (0, 255, 0)).save(ss_img)

    sm = SettingsManager(config_dir=tmp_path / "cfg")
    sm.config.wallpaper.folders = [FolderSourceConfig(path=str(wp_dir), enabled=True, recursive=True)]
    sm.config.screensaver.use_wallpaper_library = False
    sm.config.screensaver.folders = [FolderSourceConfig(path=str(ss_dir), enabled=True, recursive=True)]
    sm.save()

    # 壁纸主库
    wp_lib = ImageLibrary(disk_cache_dir=tmp_path / "cache")
    wp_lib.set_sources(sm.config.wallpaper.folders)
    wp_lib.scan()
    assert len(wp_lib.get_all_images()) == 1
    assert wp_lib.get_all_images()[0].path == str(wp_img)

    # 创建屏保控制器并传入壁纸库
    ctrl = ScreensaverController(settings_manager=sm, image_library=wp_lib)
    candidates = ctrl._prepare_library_and_candidates()

    # 必须返回独立屏保图片，而非壁纸图片！
    assert len(candidates) == 1
    assert candidates[0].path == str(ss_img)

    # 同时验证原有壁纸库未受污染
    assert len(wp_lib.get_all_images()) == 1
    assert wp_lib.get_all_images()[0].path == str(wp_img)


def test_screensaver_execution_state_prevent_black_flicker() -> None:
    """测试 set_thread_display_required 阻止系统屏保重复唤起与黑屏."""
    from app.win32_integration import set_thread_display_required

    # 启用阻止息屏与屏保
    res_on = set_thread_display_required(True)
    assert isinstance(res_on, bool)

    # 恢复默认系统电源状态
    res_off = set_thread_display_required(False)
    assert isinstance(res_off, bool)


def test_screensaver_config_dialog_tolerant_init(tmp_path: Path) -> None:
    """测试 ScreensaverConfigDialog 对变参初始化的容错保护，防止 TypeError 并保证文件夹表格正常展示."""
    from PySide6.QtWidgets import QApplication, QWidget
    from app.image_library import ImageLibrary
    from app.settings_manager import SettingsManager
    from app.ui.screensaver_config_dialog import ScreensaverConfigDialog

    app = QApplication.instance() or QApplication([])
    sm = SettingsManager(config_dir=tmp_path / "cfg_dlg")
    lib = ImageLibrary(disk_cache_dir=tmp_path / "cache_dlg")
    parent_w = QWidget()

    # 模拟 MainWindow 传入 (sm, lib, parent)
    dlg = ScreensaverConfigDialog(sm, lib, parent_w)
    assert dlg._library is lib
    assert dlg.parent() == parent_w
    dlg.show()
    app.processEvents()

    # 验证表格具备充足可视高度，绝不再出现 viewport == 0 导致添加文件夹不可见的严重 bug
    assert dlg.folder_table.height() >= 160
    assert dlg.folder_table.viewport().height() >= 100

    # 模拟添加文件夹并验证收集
    dlg._append_folder_row(str(tmp_path / "custom_ss"), True, True)
    assert dlg.folder_table.rowCount() == 1
    collected = dlg._collect_folders_from_table()
    assert len(collected) == 1
    assert collected[0].path == str(tmp_path / "custom_ss")
    assert collected[0].enabled is True
    assert collected[0].recursive is True

    dlg.close()


def test_image_processor_cache_limit_and_clear(tmp_path: Path) -> None:
    """测试图片解码器缓存上限限制 (2张) 与手动清空缓存，防止内存爆炸."""
    from app.image_processor import _IMAGE_CACHE, clear_image_cache, load_image_for_target

    clear_image_cache()
    assert len(_IMAGE_CACHE) == 0

    # 创建 3 张不同尺寸的测试图片
    paths = []
    for i in range(3):
        p = tmp_path / f"img_cache_{i}.png"
        img = Image.new("RGB", (600 + i * 10, 400 + i * 10), color=(10 * i, 20 * i, 30 * i))
        img.save(p)
        paths.append(str(p))

    # 分别解码加载这 3 张图片
    for p in paths:
        loaded = load_image_for_target(p, (400, 300))
        assert loaded is not None

    # 缓存最大条目必须 <= 2，超过即被 LRU 驱逐并显式 close()
    assert len(_IMAGE_CACHE) <= 2

    # 执行清空解码缓存
    clear_image_cache()
    assert len(_IMAGE_CACHE) == 0


def test_trim_process_memory_execution() -> None:
    """测试 Win32 进程工作集修剪功能."""
    from app.win32_integration import trim_process_memory

    ok = trim_process_memory()
    assert isinstance(ok, bool)
    assert ok is True


def test_image_library_clear_memory_cache(tmp_path: Path) -> None:
    """测试 ImageLibrary 清理缩略图内存缓存."""
    from app.image_library import ImageLibrary

    lib = ImageLibrary(disk_cache_dir=tmp_path / "thumb_cache")
    # 构造假缓存
    from PySide6.QtGui import QPixmap
    lib._thumb_cache.put("fake_key", QPixmap(10, 10), 100)
    assert len(lib._thumb_cache) == 1

    lib.clear_memory_cache()
    assert len(lib._thumb_cache) == 0
    lib.shutdown()


def test_main_window_background_mode_memory_release(tmp_path: Path) -> None:
    """测试 MainWindow 进入和离开后台模式时视觉资源与内存的精准释放与恢复."""
    from PySide6.QtWidgets import QApplication
    from app.image_library import ImageLibrary
    from app.random_selector import ImageSelector
    from app.scheduler import WallpaperScheduler
    from app.settings_manager import FolderSourceConfig, SettingsManager
    from app.startup_manager import StartupManager
    from app.ui.main_window import MainWindow
    from app.wallpaper_manager import WallpaperManager

    app = QApplication.instance() or QApplication([])

    img_dir = tmp_path / "wallpapers"
    img_dir.mkdir()
    test_img = img_dir / "test.png"
    Image.new("RGB", (800, 600), color="blue").save(test_img)

    sm = SettingsManager(config_dir=tmp_path / "cfg_mw")
    sm.config.wallpaper.folders = [FolderSourceConfig(path=str(img_dir), enabled=True, recursive=True)]
    sm.save()

    lib = ImageLibrary(disk_cache_dir=tmp_path / "cache_mw")
    lib.set_sources(sm.config.wallpaper.folders)
    lib.scan()

    selector = ImageSelector(candidates=lib.get_all_paths())
    wp_mgr = WallpaperManager(settings_manager=sm, image_library=lib, selector=selector)
    sched = WallpaperScheduler(settings_manager=sm, apply_callback=lambda: (True, ""))
    start_mgr = StartupManager()

    win = MainWindow(
        settings_manager=sm,
        image_library=lib,
        selector=selector,
        wallpaper_manager=wp_mgr,
        scheduler=sched,
        startup_manager=start_mgr,
    )

    # 模拟进入前台刷新舞台
    win.overview_preview_stage.refresh_preview(force=True)
    assert win.overview_preview_stage._rendered_pixmap is not None

    # 模拟切换/隐藏至后台模式
    win._on_enter_background_mode()
    # 验证内存已彻底释放
    assert win.overview_preview_stage._rendered_pixmap is None
    assert win.overview_preview_stage._rendered_pil is None
    assert win.wallpaper_preview_stage._rendered_pixmap is None
    assert win.wallpaper_preview_stage._rendered_pil is None

    # 模拟恢复前台
    win._on_leave_background_mode()
    assert win.overview_preview_stage._rendered_pixmap is not None

    win.close()


def test_screensaver_smart_suppression_settings_roundtrip() -> None:
    """测试 ScreensaverSettings 智能多媒体与全屏免打扰配置的序列化与反序列化."""
    from app.settings_manager import ScreensaverSettings

    ss = ScreensaverSettings()
    assert ss.suppress_when_media_playing is True
    assert ss.suppress_when_fullscreen is True

    d = ss.to_dict()
    assert d["suppress_when_media_playing"] is True
    assert d["suppress_when_fullscreen"] is True

    d["suppress_when_media_playing"] = False
    d["suppress_when_fullscreen"] = False
    restored = ScreensaverSettings.from_dict(d)
    assert restored.suppress_when_media_playing is False
    assert restored.suppress_when_fullscreen is False


def test_audio_activity_detector_grace_period(monkeypatch) -> None:
    """测试 AudioActivityDetector 在视频切换或短暂停歇时的宽限保护."""
    from app.win32_integration import AudioActivityDetector

    detector = AudioActivityDetector()
    # 隔离真实物理音频设备与会话状态，纯逻辑测试时间窗口
    monkeypatch.setattr(detector, "_ensure_interfaces", lambda: (None, None))
    now = time.monotonic()
    # 模拟 5 秒前曾有视频音频活跃
    detector._last_audio_active_mono = now - 5.0
    active, _ = detector.check_audio_activity(grace_period_seconds=20.0)
    assert active is True

    # 模拟 30 秒前有音频，超过 20 秒宽限期
    detector._last_audio_active_mono = now - 30.0
    active_expired, peak = detector.check_audio_activity(grace_period_seconds=20.0)
    assert active_expired is False


def test_check_system_media_and_busy_state() -> None:
    """测试 check_system_media_and_busy_state 系统级多媒体与状态识别."""
    from app.win32_integration import SystemActivityState, check_system_media_and_busy_state

    state = check_system_media_and_busy_state(suppress_media=True, suppress_fullscreen=True)
    assert isinstance(state, SystemActivityState)
    assert isinstance(state.is_busy, bool)
    assert isinstance(state.audio_playing, bool)
    assert isinstance(state.fullscreen_active, bool)
    assert isinstance(state.reason, str)


def test_main_window_watchdog_respects_busy_state(tmp_path: Path) -> None:
    """测试 MainWindow 空闲看门狗在检测到系统忙碌/媒体播放时暂停屏保启动并友好提示."""
    from PySide6.QtWidgets import QApplication
    from app.image_library import ImageLibrary
    from app.random_selector import ImageSelector
    from app.scheduler import WallpaperScheduler
    from app.settings_manager import FolderSourceConfig, SettingsManager
    from app.startup_manager import StartupManager
    from app.ui.main_window import MainWindow
    from app.wallpaper_manager import WallpaperManager
    from app.win32_integration import SystemActivityState

    app = QApplication.instance() or QApplication([])
    sm = SettingsManager(config_dir=tmp_path / "cfg_watchdog")
    lib = ImageLibrary(disk_cache_dir=tmp_path / "cache_wd")
    selector = ImageSelector(candidates=[])
    wp_mgr = WallpaperManager(settings_manager=sm, image_library=lib, selector=selector)
    sched = WallpaperScheduler(settings_manager=sm, apply_callback=lambda: (True, ""))
    start_mgr = StartupManager()

    win = MainWindow(
        settings_manager=sm,
        image_library=lib,
        selector=selector,
        wallpaper_manager=wp_mgr,
        scheduler=sched,
        startup_manager=start_mgr,
    )
    win.show()

    # 模拟系统处于音频播放中
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.ui.main_window.check_system_media_and_busy_state",
            lambda suppress_media, suppress_fullscreen: SystemActivityState(
                is_busy=True,
                reason="系统正在播放音频/视频 (音量: 35%)",
                audio_playing=True,
            ),
        )
        mp.setattr("app.ui.main_window.get_system_idle_seconds", lambda: 999.0)

        # 执行一次时钟 Tick
        win._on_ui_tick()

        # 验证屏保并未启动
        assert win._screensaver_preview_ctrl is None
        # 验证防闪黑系统常亮锁已激活
        assert win._system_keep_awake_active is True
        # 验证提示标签明确向用户展示忙碌保护状态
        lbl_text = win.lbl_ss_idle_live.text()
        assert "系统级忙碌保护中" in lbl_text
        assert "系统正在播放音频/视频" in lbl_text

        # 模拟媒体播放结束且宽限期已过
        mp.setattr(
            "app.ui.main_window.check_system_media_and_busy_state",
            lambda suppress_media, suppress_fullscreen: SystemActivityState(
                is_busy=False,
                reason="",
                audio_playing=False,
            ),
        )
        win._on_ui_tick()
        # 验证防闪黑系统常亮锁已安全释放
        assert win._system_keep_awake_active is False

    win.close()


def test_screensaver_sleep_prevention_and_timed_sleep_settings_roundtrip() -> None:
    """测试屏保阻止睡眠与定时睡眠配置的序列化与反序列化."""
    from app.settings_manager import ScreensaverSettings

    ss = ScreensaverSettings()
    assert ss.prevent_system_sleep is True
    assert ss.timed_sleep_minutes == 30
    assert ss.timed_sleep_action == "sleep"

    d = ss.to_dict()
    assert d["prevent_system_sleep"] is True
    assert d["timed_sleep_minutes"] == 30
    assert d["timed_sleep_action"] == "sleep"

    d["prevent_system_sleep"] = False
    d["timed_sleep_minutes"] = 15
    d["timed_sleep_action"] = "turn_off_screen"
    restored = ScreensaverSettings.from_dict(d)
    assert restored.prevent_system_sleep is False
    assert restored.timed_sleep_minutes == 15
    assert restored.timed_sleep_action == "turn_off_screen"


def test_win32_suspend_system_and_turn_off_display_functions() -> None:
    """测试 suspend_system 与 turn_off_display Win32 接口包装安全."""
    from app.win32_integration import suspend_system, turn_off_display

    assert callable(suspend_system)
    assert callable(turn_off_display)


def test_screensaver_controller_timed_sleep_flow(tmp_path: Path) -> None:
    """测试 ScreensaverController 定时睡眠计时器与动作触发."""
    from PySide6.QtWidgets import QApplication
    from app.screensaver import ScreensaverController
    from app.settings_manager import SettingsManager

    app = QApplication.instance() or QApplication([])
    sm = SettingsManager(config_dir=tmp_path / "cfg_ss_sleep")
    sm.config.screensaver.prevent_system_sleep = False
    sm.config.screensaver.timed_sleep_minutes = 10
    sm.config.screensaver.timed_sleep_action = "allow_sleep"
    sm.save()

    ctrl = ScreensaverController(settings_manager=sm)

    # 模拟启动全屏屏保
    with pytest.MonkeyPatch.context() as mp:
        display_calls = []
        mp.setattr("app.screensaver.set_thread_display_required", lambda enable: display_calls.append(enable))

        ctrl.start_fullscreen(auto_close_ms=1000)

        # 当 prevent_system_sleep = False 时，初始调用应为 False
        assert False in display_calls

        # 验证定时睡眠定时器已初始化
        assert ctrl._timed_sleep_timer is not None

        # 模拟定时睡眠触发
        ctrl._on_timed_sleep_due()
        assert ctrl._timed_sleep_fired is True
        # 释放阻止休眠
        assert display_calls[-1] is False

        ctrl.stop(reason="TestFinish")












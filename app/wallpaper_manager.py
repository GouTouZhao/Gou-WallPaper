"""桌面壁纸管理与多显示器合成引擎 (`app/wallpaper_manager.py`).

核心职责：
- 支持 6 种显示布局：Fill、Fit、Stretch、Center、Tile、Span
- 支持多显示器模式：
  1. 所有显示器使用同一张壁纸 (`same`)
  2. 不同显示器分别使用随机/独立图片 (`different`)
  3. 跨显示器显示 (`span`)
- 支持 2~8 张图片拼贴模式与亮度/对比度/饱和度/色温非破坏性调整
- 优先使用 `IDesktopWallpaper` COM 接口实现原生多显示器控制；当需要跨屏合成、拼贴、滤镜调整或 COM 接口不可用时，使用虚拟桌面合成 + `SystemParametersInfoW` 回退
- 保留原始图片，所有渲染产物存储在 `%LOCALAPPDATA%\\WallFlow\\cache\\rendered\\` 并限制最大保留文件数
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PIL import Image

from app.image_library import ImageLibrary
from app.image_processor import (
    apply_adjustments,
    load_image_for_target,
    parse_rgb_color,
    render_collage,
    render_image_layout,
)
from app.logger import get_logger
from app.random_selector import ImageSelector
from app.settings_manager import SettingsManager
from app.win32_integration import (
    MonitorInfo,
    compute_virtual_desktop_bounds,
    enumerate_display_monitors,
    set_wallpaper_via_com,
    set_wallpaper_via_spi,
)


@dataclass
class ApplyResult:
    """单次壁纸应用结果."""

    success: bool
    applied_paths: list[str] = field(default_factory=list)
    rendered_output_paths: list[str] = field(default_factory=list)
    method_used: str = ""  # "IDesktopWallpaper" | "SystemParametersInfoW"
    error_message: str = ""
    elapsed_ms: float = 0.0

    @property
    def image_paths(self) -> list[str]:
        return self.applied_paths


class WallpaperManager:
    """桌面壁纸核心管理器."""

    def __init__(
        self,
        settings_manager: SettingsManager,
        image_library: ImageLibrary,
        selector: ImageSelector,
        com_setter: Callable[..., tuple[bool, str]] = set_wallpaper_via_com,
        spi_setter: Callable[..., tuple[bool, str]] = set_wallpaper_via_spi,
        monitor_provider: Callable[[], list[MonitorInfo]] = enumerate_display_monitors,
    ) -> None:
        self._logger = get_logger("wallpaper_manager")
        self._settings = settings_manager
        self._library = image_library
        self._selector = selector
        self._com_setter = com_setter
        self._spi_setter = spi_setter
        self._monitor_provider = monitor_provider

        self._lock = threading.RLock()
        self._monitors: list[MonitorInfo] = self._monitor_provider()
        self._rendered_dir = self._settings.cache_dir / "rendered"
        self._rendered_dir.mkdir(parents=True, exist_ok=True)
        self._render_seq = 0
        self._listeners: list[Callable[[ApplyResult], None]] = []

    @property
    def monitors(self) -> list[MonitorInfo]:
        with self._lock:
            return list(self._monitors)

    def refresh_monitors(self) -> list[MonitorInfo]:
        """重新枚举显示器拓扑（在显示器连接/断开/旋转/分辨率变化后调用）."""
        with self._lock:
            self._monitors = self._monitor_provider()
            self._logger.info(
                "刷新显示器列表完成，检测到 %d 台显示器: %s",
                len(self._monitors),
                [m.display_label for m in self._monitors],
            )
            return list(self._monitors)

    def add_listener(self, callback: Callable[[ApplyResult], None]) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def apply_next(self) -> ApplyResult:
        """切换到下一张（或下一组）壁纸."""
        return self._select_and_apply(direction="next")

    def apply_previous(self) -> ApplyResult:
        """切换到上一张壁纸."""
        return self._select_and_apply(direction="previous")

    def apply_random(self) -> ApplyResult:
        """立即随机切换壁纸."""
        return self._select_and_apply(direction="random")

    def apply_current_or_next(self) -> ApplyResult:
        """立即应用当前选中的壁纸（若尚未选中则抽取下一张）."""
        with self._lock:
            wp_cfg = self._settings.config.wallpaper
            if wp_cfg.collage_enabled or (wp_cfg.multi_monitor_mode == "different" and len(self._monitors) > 1):
                if wp_cfg.current_wallpaper_paths:
                    valid_existing = [p for p in wp_cfg.current_wallpaper_paths if self._library.is_path_valid(p)]
                    if valid_existing:
                        return self.apply_specific_images(valid_existing)
                return self.apply_next()

            cur = self._selector.current()
            if cur and self._library.is_path_valid(cur):
                return self.apply_specific_images([cur])
            return self.apply_next()

    def _select_and_apply(self, direction: str = "next", max_retries: int = 3) -> ApplyResult:
        """从选择器抽取有效图片并应用，遇到损坏文件自动剔除并重试."""
        with self._lock:
            self._monitors = self._monitor_provider()
            wp_cfg = self._settings.config.wallpaper
            monitor_count = max(1, len(self._monitors))

            if wp_cfg.collage_enabled:
                per_mon = max(2, min(8, wp_cfg.collage_count))
                needed = per_mon * monitor_count if wp_cfg.multi_monitor_mode == "different" else per_mon
            elif wp_cfg.multi_monitor_mode == "different" and wp_cfg.display_mode != "span":
                needed = monitor_count
            else:
                needed = 1

            last_error = "图片库中没有可用图片"
            for attempt in range(1, max_retries + 1):
                if self._selector.candidate_count == 0:
                    return ApplyResult(success=False, error_message="图片库为空，请先添加包含有效静态图片的文件夹")

                chosen_paths: list[str] = []
                if needed == 1:
                    if direction == "previous":
                        p = self._selector.previous()
                    elif direction == "random":
                        p = self._selector.random_switch()
                    else:
                        p = self._selector.next()
                    if p:
                        chosen_paths = [p]
                else:
                    chosen_paths = self._selector.select_multiple(needed)

                # 过滤并剔除中途被删除或损坏的图片
                valid_paths: list[str] = []
                for p in chosen_paths:
                    if self._library.is_path_valid(p):
                        valid_paths.append(p)
                    else:
                        self._logger.warning("检测到图片不可用或已被删除，从候选集移除: %s", p)
                        self._selector.remove_candidate(p)

                if not valid_paths:
                    last_error = f"抽取的图片无效或已被删除 (尝试 {attempt}/{max_retries})"
                    continue

                result = self.apply_specific_images(valid_paths)
                if result.success:
                    return result

                last_error = result.error_message
                self._logger.warning("壁纸应用尝试 %d/%d 失败: %s", attempt, max_retries, last_error)

            return ApplyResult(success=False, error_message=last_error)

    def apply_specific_images(self, image_paths: list[str]) -> ApplyResult:
        """将指定的 1 张或多张图片按当前布局、多显示器与调整配置应用为桌面壁纸."""
        start_t = time.perf_counter()
        with self._lock:
            if not image_paths:
                return ApplyResult(success=False, error_message="未提供壁纸图片路径")

            wp_cfg = self._settings.config.wallpaper
            monitors = self._monitor_provider()
            self._monitors = monitors
            display_mode = wp_cfg.display_mode.lower()
            multi_mode = wp_cfg.multi_monitor_mode.lower()
            bg_color = wp_cfg.background_color
            bg_rgb = parse_rgb_color(bg_color)
            adjustments = wp_cfg.adjustments

            try:
                # 情况 1：跨显示器布局 (Span) 或 Tile 平铺模式 -> 合成整张虚拟桌面画布并调用 SPI / COM
                if display_mode in ("span", "tile") or multi_mode == "span":
                    effective_mode = "span" if (display_mode == "span" or multi_mode == "span") else "tile"
                    canvas = self._compose_span_or_tile_canvas(
                        image_paths[0],
                        monitors,
                        effective_mode,
                        bg_color,
                        adjustments,
                    )
                    out_path = self._save_rendered_image(canvas, prefix="wallflow_span")
                    ok, err = self._spi_setter(out_path, display_mode=effective_mode)
                    method = "SystemParametersInfoW"
                    if not ok:
                        ok, err = self._com_setter({None: str(out_path)}, display_mode=effective_mode, background_rgb=bg_rgb)
                        method = "IDesktopWallpaper"
                    return self._finalize_result(ok, image_paths[:1], [str(out_path)], method, err, start_t)

                # 情况 2：开启了拼贴模式 (2~8 张图) 或多显示器各自独立壁纸
                if wp_cfg.collage_enabled or (multi_mode == "different" and len(monitors) > 1):
                    per_monitor_outputs: dict[str | None, str] = {}
                    per_monitor_images: list[tuple[MonitorInfo, Image.Image]] = []
                    rendered_files: list[str] = []

                    per_mon_count = max(2, min(8, wp_cfg.collage_count)) if wp_cfg.collage_enabled else 1
                    for m_idx, mon in enumerate(monitors):
                        if multi_mode == "same":
                            sub_paths = image_paths[:per_mon_count]
                        else:
                            start_i = (m_idx * per_mon_count) % len(image_paths)
                            sub_paths = [
                                image_paths[(start_i + k) % len(image_paths)]
                                for k in range(per_mon_count)
                            ]

                        if wp_cfg.collage_enabled:
                            mon_img = render_collage(
                                sub_paths,
                                mon.width,
                                mon.height,
                                display_mode=display_mode,
                                background_color=bg_color,
                            )
                        else:
                            src = load_image_for_target(sub_paths[0], target_size=(mon.width, mon.height))
                            mon_img = render_image_layout(
                                src,
                                mon.width,
                                mon.height,
                                display_mode=display_mode,
                                background_color=bg_color,
                            )
                        mon_img = apply_adjustments(mon_img, adjustments)
                        per_monitor_images.append((mon, mon_img))
                        out_file = self._save_rendered_image(mon_img, prefix=f"wallflow_mon{m_idx}")
                        rendered_files.append(str(out_file))
                        per_monitor_outputs[mon.device_id] = str(out_file)

                    # 优先尝试 IDesktopWallpaper 按显示器独立设置
                    if len(monitors) > 1:
                        com_ok, com_err = self._com_setter(
                            per_monitor_outputs,
                            display_mode="fill",
                            background_rgb=bg_rgb,
                        )
                        if com_ok:
                            return self._finalize_result(
                                True,
                                image_paths,
                                rendered_files,
                                "IDesktopWallpaper",
                                "",
                                start_t,
                            )
                        self._logger.info("IDesktopWallpaper 独立设置不可用 (%s)，回退虚拟桌面合成模式", com_err)

                    # 回退或单显示器：合成为虚拟桌面画布并通过 SystemParametersInfoW 应用
                    if len(per_monitor_images) == 1:
                        single_out = rendered_files[0]
                        ok, err = self._spi_setter(single_out, display_mode="fill")
                        return self._finalize_result(ok, image_paths, rendered_files, "SystemParametersInfoW", err, start_t)

                    v_canvas = self._stitch_monitors_to_virtual_desktop(per_monitor_images, bg_rgb)
                    v_out = self._save_rendered_image(v_canvas, prefix="wallflow_virtual")
                    rendered_files.append(str(v_out))
                    ok, err = self._spi_setter(v_out, display_mode="span")
                    return self._finalize_result(ok, image_paths, rendered_files, "SystemParametersInfoW(VirtualDesktop)", err, start_t)

                # 情况 3：所有显示器使用同一张单图 (`same` 或单显示器)
                primary_mon = monitors[0]
                src = load_image_for_target(image_paths[0], target_size=(primary_mon.width, primary_mon.height))
                rendered = render_image_layout(
                    src,
                    primary_mon.width,
                    primary_mon.height,
                    display_mode=display_mode,
                    background_color=bg_color,
                )
                rendered = apply_adjustments(rendered, adjustments)
                out_path = self._save_rendered_image(rendered, prefix="wallflow_single")

                com_ok, com_err = self._com_setter(
                    {None: str(out_path)},
                    display_mode="fill",
                    background_rgb=bg_rgb,
                )
                if com_ok:
                    return self._finalize_result(True, image_paths[:1], [str(out_path)], "IDesktopWallpaper", "", start_t)

                spi_ok, spi_err = self._spi_setter(out_path, display_mode="fill")
                return self._finalize_result(
                    spi_ok,
                    image_paths[:1],
                    [str(out_path)],
                    "SystemParametersInfoW",
                    spi_err or com_err,
                    start_t,
                )

            except Exception as exc:
                self._logger.error("渲染并应用壁纸异常: %s", exc)
                return ApplyResult(
                    success=False,
                    applied_paths=list(image_paths),
                    error_message=f"壁纸处理异常: {exc}",
                    elapsed_ms=(time.perf_counter() - start_t) * 1000.0,
                )

    def generate_preview_image(
        self,
        preview_width: int = 640,
        preview_height: int = 360,
        image_paths: list[str] | None = None,
    ) -> Image.Image:
        """生成当前配置与多显示器布局的可视化预览图，供 GUI 界面实时展示."""
        wp_cfg = self._settings.config.wallpaper
        bg_rgb = parse_rgb_color(wp_cfg.background_color)
        paths = image_paths or wp_cfg.current_wallpaper_paths
        if not paths:
            cur = self._selector.current()
            if cur:
                paths = [cur]
        if not paths:
            all_paths = self._library.get_all_paths()
            if all_paths:
                paths = all_paths[: max(2, wp_cfg.collage_count) if wp_cfg.collage_enabled else 1]

        if not paths:
            return Image.new("RGB", (preview_width, preview_height), bg_rgb)

        try:
            if wp_cfg.collage_enabled:
                count = max(2, min(8, wp_cfg.collage_count))
                selected = [paths[i % len(paths)] for i in range(count)]
                canvas = render_collage(
                    selected,
                    preview_width,
                    preview_height,
                    display_mode=wp_cfg.display_mode,
                    background_color=wp_cfg.background_color,
                )
            else:
                src = load_image_for_target(paths[0], target_size=(preview_width, preview_height))
                canvas = render_image_layout(
                    src,
                    preview_width,
                    preview_height,
                    display_mode=wp_cfg.display_mode,
                    background_color=wp_cfg.background_color,
                )
            return apply_adjustments(canvas, wp_cfg.adjustments)
        except Exception as exc:
            self._logger.warning("生成预览图失败: %s", exc)
            return Image.new("RGB", (preview_width, preview_height), bg_rgb)

    def _compose_span_or_tile_canvas(
        self,
        image_path: str,
        monitors: list[MonitorInfo],
        mode: str,
        bg_color: str,
        adjustments,
    ) -> Image.Image:
        _, _, vw, vh = compute_virtual_desktop_bounds(monitors)
        src = load_image_for_target(image_path, target_size=(vw, vh))
        canvas = render_image_layout(src, vw, vh, display_mode=mode, background_color=bg_color)
        return apply_adjustments(canvas, adjustments)

    def _stitch_monitors_to_virtual_desktop(
        self,
        per_monitor_images: list[tuple[MonitorInfo, Image.Image]],
        bg_rgb: tuple[int, int, int],
    ) -> Image.Image:
        monitors = [m for m, _ in per_monitor_images]
        min_x, min_y, vw, vh = compute_virtual_desktop_bounds(monitors)
        canvas = Image.new("RGB", (vw, vh), bg_rgb)
        for mon, img in per_monitor_images:
            offset_x = mon.x - min_x
            offset_y = mon.y - min_y
            canvas.paste(img, (offset_x, offset_y))
        return canvas

    def _save_rendered_image(self, image: Image.Image, prefix: str = "wallflow") -> Path:
        self._rendered_dir.mkdir(parents=True, exist_ok=True)
        self._render_seq = (self._render_seq + 1) % 1000
        out_path = self._rendered_dir / f"{prefix}_{self._render_seq:03d}.bmp"
        image.save(out_path, format="BMP")
        self._cleanup_old_rendered_files()
        return out_path

    def _cleanup_old_rendered_files(self) -> None:
        max_keep = max(4, int(self._settings.config.system.render_cache_max_items))
        try:
            files = sorted(
                self._rendered_dir.glob("wallflow_*.bmp"),
                key=lambda p: p.stat().st_mtime_ns,
                reverse=True,
            )
            for old_file in files[max_keep:]:
                try:
                    old_file.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    def _finalize_result(
        self,
        success: bool,
        applied_paths: list[str],
        rendered_paths: list[str],
        method_used: str,
        error_msg: str,
        start_t: float,
    ) -> ApplyResult:
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        if success:
            self._settings.config.wallpaper.current_wallpaper_paths = list(applied_paths)
            self._settings.config.wallpaper.selector_state = self._selector.to_dict()
            self._settings.save()
            self._logger.info(
                "成功设置桌面壁纸 (方式=%s, 耗时=%.1fms): %s",
                method_used,
                elapsed_ms,
                applied_paths,
            )
        else:
            self._logger.error("设置桌面壁纸失败 (方式=%s): %s", method_used, error_msg)

        result = ApplyResult(
            success=success,
            applied_paths=list(applied_paths),
            rendered_output_paths=list(rendered_paths),
            method_used=method_used,
            error_message=error_msg,
            elapsed_ms=elapsed_ms,
        )
        for cb in list(self._listeners):
            try:
                cb(result)
            except Exception:
                pass

        # 壁纸应用完成后立即清空解码缓存并修剪工作集，防止常驻占用数百兆内存
        try:
            from app.image_processor import clear_image_cache
            from app.win32_integration import trim_process_memory
            clear_image_cache()
            trim_process_memory()
        except Exception:
            pass

        return result


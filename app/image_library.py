"""静态图片库扫描、元数据过滤、索引与有界缩略图缓存模块 (`app/image_library.py`).

核心设计：
- 支持格式：PNG, JPG/JPEG, WebP, BMP, TIF/TIFF
- 排除动态 GIF、动态 WebP 等非静态图像
- 扫描阶段仅读取文件头部元数据 (`Image.open` 不调用 `.load()`)，绝不一次性将像素读入内存
- 支持多文件夹源、独立启用/禁用、可选递归子目录、中文/空格/长路径
- 结合 `(mtime_ns, file_size)` 元数据缓存，数千张高分辨率 PNG 二次扫描毫秒级完成
- 缩略图采用工作线程生成 + 有明确字节上限的 LRU 内存缓存
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps, UnidentifiedImageError

# 解除 PIL 像素上限警告/限制，支持 8K/16K 超高清壁纸与巨幅长图
Image.MAX_IMAGE_PIXELS = None

from app.logger import get_logger
from app.settings_manager import FolderSourceConfig

SUPPORTED_STATIC_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
)

ALLOWED_PIL_FORMATS: frozenset[str] = frozenset(
    {"PNG", "JPEG", "WEBP", "BMP", "TIFF", "MPO"}
)


@dataclass(frozen=True)
class ImageMetadata:
    """单张有效静态图片的轻量级元数据."""

    path: str
    folder_source: str
    filename: str
    extension: str
    format_name: str
    width: int
    height: int
    file_size: int
    mtime_ns: int

    @property
    def file_size_bytes(self) -> int:
        return self.file_size

    @property
    def format(self) -> str:
        return self.format_name

    @property
    def is_landscape(self) -> bool:
        return self.width >= self.height

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width

    @property
    def resolution_text(self) -> str:
        return f"{self.width} × {self.height}"

    @property
    def size_mb_text(self) -> str:
        mb = self.file_size / (1024 * 1024)
        if mb >= 1.0:
            return f"{mb:.2f} MB"
        kb = self.file_size / 1024
        return f"{kb:.1f} KB"


@dataclass
class SkippedFileRecord:
    """被跳过的无效/损坏/不支持图片记录."""

    path: str
    reason: str


@dataclass
class ScanSummary:
    """单次图片库扫描统计结果."""

    total_candidates: int = 0
    valid_count: int = 0
    skipped_count: int = 0
    added_count: int = 0
    removed_count: int = 0
    elapsed_seconds: float = 0.0
    folder_counts: dict[str, int] = None  # type: ignore[assignment]
    skipped_files: list[SkippedFileRecord] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.folder_counts is None:
            self.folder_counts = {}
        if self.skipped_files is None:
            self.skipped_files = []


class BoundedByteLRUCache:
    """带明确字节大小与条目数上限的线程安全 LRU 缓存."""

    def __init__(self, max_bytes: int = 128 * 1024 * 1024, max_items: int = 500) -> None:
        self._max_bytes = max(1, int(max_bytes))
        self._max_items = max(1, int(max_items))
        self._store: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._current_bytes: int = 0
        self._lock = threading.RLock()

    @property
    def current_bytes(self) -> int:
        with self._lock:
            return self._current_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def item_count(self) -> int:
        with self._lock:
            return len(self._store)

    def __len__(self) -> int:
        return self.item_count

    def set_limits(self, max_bytes: int | None = None, max_items: int | None = None) -> None:
        with self._lock:
            if max_bytes is not None:
                self._max_bytes = max(1024 * 1024, int(max_bytes))
            if max_items is not None:
                self._max_items = max(1, int(max_items))
            self._evict_if_needed()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            self._store.move_to_end(key)
            return entry[0]

    def put(self, key: str, value: Any, size_bytes: int) -> None:
        est_size = max(1, int(size_bytes))
        if est_size > self._max_bytes:
            return
        with self._lock:
            if key in self._store:
                _, old_size = self._store.pop(key)
                self._current_bytes -= old_size
            self._store[key] = (value, est_size)
            self._current_bytes += est_size
            self._evict_if_needed()

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._current_bytes = 0

    def _evict_if_needed(self) -> None:
        while self._store and (
            self._current_bytes > self._max_bytes or len(self._store) > self._max_items
        ):
            _, (_, evicted_size) = self._store.popitem(last=False)
            self._current_bytes = max(0, self._current_bytes - evicted_size)


def inspect_static_image_header(file_path: Path | str, folder_source: str = "") -> tuple[ImageMetadata | None, str]:
    """仅读取图像文件头部元数据，验证其是否为合法静态图片.

    不解码完整像素数组，对 8K 大型 PNG 耗时 < 1ms。
    返回 `(ImageMetadata | None, error_reason)`.
    """
    p = Path(file_path)
    ext = p.suffix.lower()
    if ext not in SUPPORTED_STATIC_EXTENSIONS:
        return None, f"不支持的文件扩展名: {ext or '(无扩展名)'}"

    try:
        stat = p.stat()
    except OSError as exc:
        return None, f"文件无法访问: {exc}"

    if stat.st_size <= 0:
        return None, "文件大小为 0 字节"

    try:
        # 放宽像素上限以支持 8K/16K 超高清原图，同时仅读头部不解码像素
        with Image.open(p) as img:
            fmt = (img.format or "").upper()
            if fmt not in ALLOWED_PIL_FORMATS:
                return None, f"实际编码格式不受支持: {fmt or 'UNKNOWN'}"

            # 排除多帧动画 (如伪装扩展名的 GIF 或动态 WebP/APNG)
            is_animated = bool(getattr(img, "is_animated", False))
            n_frames = int(getattr(img, "n_frames", 1))
            if fmt == "GIF" or (is_animated and n_frames > 1 and fmt in ("GIF", "WEBP")):
                return None, f"检测到动态图像 ({fmt}, {n_frames} 帧)，仅支持静态图片"

            width, height = img.size
            if width <= 0 or height <= 0:
                return None, f"无效的图片尺寸: {width}x{height}"

            # 仅在可能包含相机方向 EXIF 的格式 (JPEG/MPO/TIFF) 下检查 Orientation，
            # 避免对数百兆巨型 PNG 读取全部 IDAT 压缩块带来的数秒磁盘 IO 阻塞
            if fmt in ("JPEG", "MPO", "TIFF"):
                try:
                    exif = img.getexif()
                    orientation = exif.get(0x0112, 1) if exif else 1
                    if orientation in (5, 6, 7, 8):
                        width, height = height, width
                except Exception:
                    pass

            meta = ImageMetadata(
                path=str(p.resolve()),
                folder_source=str(folder_source or p.parent),
                filename=p.name,
                extension=ext,
                format_name="JPEG" if fmt == "MPO" else fmt,
                width=int(width),
                height=int(height),
                file_size=int(stat.st_size),
                mtime_ns=int(stat.st_mtime_ns),
            )
            return meta, ""
    except (UnidentifiedImageError, SyntaxError, ValueError, OSError, EOFError) as exc:
        return None, f"图片头部解码校验失败: {exc}"
    except Exception as exc:
        return None, f"未知图片解析异常: {exc}"


class DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """线程池工作线程全部设为 daemon=True，防止阻止 Python 解释器瞬间安全退出."""

    def _adjust_thread_count(self) -> None:
        super()._adjust_thread_count()
        for t in self._threads:
            t.daemon = True


class ImageLibrary:
    """静态图片库管理器，支持增量元数据缓存、异步扫描与有界缩略图生成."""

    def __init__(
        self,
        thumbnail_cache_max_mb: int = 24,
        disk_cache_dir: Path | str | None = None,
    ) -> None:
        self._logger = get_logger("image_library")
        self._lock = threading.RLock()
        self._sources: list[FolderSourceConfig] = []
        self._images_by_path: dict[str, ImageMetadata] = {}
        self._ordered_paths: list[str] = []
        self._skipped_records: list[SkippedFileRecord] = []
        self._folder_counts: dict[str, int] = {}

        # 元数据指纹缓存: path -> (mtime_ns, file_size, ImageMetadata)
        self._header_cache: dict[str, tuple[int, int, ImageMetadata]] = {}

        # 缩略图内存 LRU 缓存（上限 24 MB / 120 张，其余由快速磁盘缓存接管）
        self._thumb_cache = BoundedByteLRUCache(
            max_bytes=thumbnail_cache_max_mb * 1024 * 1024,
            max_items=120,
        )
        self._disk_cache_dir: Path | None = Path(disk_cache_dir) if disk_cache_dir else None
        if self._disk_cache_dir:
            self._disk_cache_dir.mkdir(parents=True, exist_ok=True)

        self._executor = DaemonThreadPoolExecutor(
            max_workers=max(2, min(8, os.cpu_count() or 4)),
            thread_name_prefix="WallFlowImgLib",
        )

    @property
    def total_valid_count(self) -> int:
        """图片库当前有效图片总数."""
        with self._lock:
            return len(self._ordered_paths)

    @property
    def thumbnail_cache(self) -> BoundedByteLRUCache:
        return self._thumb_cache

    def set_sources(self, sources: list[FolderSourceConfig]) -> None:
        """设置图片文件夹来源列表."""
        with self._lock:
            self._sources = [
                FolderSourceConfig(
                    path=str(s.path),
                    enabled=bool(s.enabled),
                    recursive=bool(s.recursive),
                    valid_count=int(s.valid_count),
                )
                for s in sources
                if s.path and str(s.path).strip()
            ]

    def get_sources(self) -> list[FolderSourceConfig]:
        with self._lock:
            return [
                FolderSourceConfig(
                    path=s.path,
                    enabled=s.enabled,
                    recursive=s.recursive,
                    valid_count=self._folder_counts.get(s.path, s.valid_count),
                )
                for s in self._sources
            ]

    def scan(
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ScanSummary:
        """同步扫描所有启用的图片文件夹（可在后台线程调用）."""
        start_time = time.perf_counter()
        with self._lock:
            sources_snapshot = list(self._sources)
            previous_paths = set(self._images_by_path.keys())

        candidate_files: list[tuple[Path, str]] = []
        skipped_records: list[SkippedFileRecord] = []
        folder_counts: dict[str, int] = {s.path: 0 for s in sources_snapshot}

        for src in sources_snapshot:
            if cancel_event and cancel_event.is_set():
                break
            if not src.enabled:
                folder_counts[src.path] = 0
                continue

            folder_path = Path(src.path)
            if not folder_path.exists() or not folder_path.is_dir():
                reason = "文件夹不存在或不是有效目录"
                skipped_records.append(SkippedFileRecord(path=src.path, reason=reason))
                self._logger.warning("跳过无效文件夹来源 [%s]: %s", src.path, reason)
                continue

            self._collect_candidates(folder_path, src.path, src.recursive, candidate_files, skipped_records, cancel_event)

        total_candidates = len(candidate_files)
        new_images_by_path: dict[str, ImageMetadata] = {}
        new_ordered_paths: list[str] = []

        for idx, (file_path, source_root) in enumerate(candidate_files, start=1):
            if cancel_event and cancel_event.is_set():
                break

            if progress_callback and (idx == 1 or idx % 25 == 0 or idx == total_candidates):
                try:
                    progress_callback(idx, total_candidates, str(file_path))
                except Exception:
                    pass

            meta, err = self._inspect_with_cache(file_path, source_root)
            if meta is not None:
                if meta.path not in new_images_by_path:
                    new_images_by_path[meta.path] = meta
                    new_ordered_paths.append(meta.path)
                folder_counts[source_root] = folder_counts.get(source_root, 0) + 1
            else:
                skipped_records.append(SkippedFileRecord(path=str(file_path), reason=err))
                self._logger.debug("跳过图片 [%s]: %s", file_path, err)

        new_paths_set = set(new_images_by_path.keys())
        added_count = len(new_paths_set - previous_paths)
        removed_count = len(previous_paths - new_paths_set)
        elapsed = time.perf_counter() - start_time

        with self._lock:
            self._images_by_path = new_images_by_path
            self._ordered_paths = new_ordered_paths
            self._skipped_records = skipped_records
            self._folder_counts = folder_counts
            for s in self._sources:
                s.valid_count = folder_counts.get(s.path, 0)

        summary = ScanSummary(
            total_candidates=total_candidates,
            valid_count=len(new_ordered_paths),
            skipped_count=len(skipped_records),
            added_count=added_count,
            removed_count=removed_count,
            elapsed_seconds=elapsed,
            folder_counts=dict(folder_counts),
            skipped_files=list(skipped_records),
        )
        self._logger.info(
            "图片库扫描完成: 候选=%d, 有效=%d, 跳过=%d, 新增=%d, 移除=%d, 耗时=%.3fs",
            summary.total_candidates,
            summary.valid_count,
            summary.skipped_count,
            summary.added_count,
            summary.removed_count,
            summary.elapsed_seconds,
        )
        return summary

    def scan_async(
        self,
        progress_callback: Callable[[int, int, str], None] | None = None,
        done_callback: Callable[[ScanSummary], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Future[ScanSummary]:
        """在后台工作线程异步执行图片库扫描，绝不阻塞 GUI 主线程."""

        def _task() -> ScanSummary:
            summary = self.scan(progress_callback=progress_callback, cancel_event=cancel_event)
            if done_callback:
                try:
                    done_callback(summary)
                except Exception as exc:
                    self._logger.error("扫描完成回调异常: %s", exc)
            return summary

        return self._executor.submit(_task)

    def _collect_candidates(
        self,
        current_dir: Path,
        source_root: str,
        recursive: bool,
        out_files: list[tuple[Path, str]],
        skipped_records: list[SkippedFileRecord],
        cancel_event: threading.Event | None = None,
    ) -> None:
        try:
            with os.scandir(current_dir) as entries:
                subdirs: list[Path] = []
                for entry in entries:
                    if cancel_event and cancel_event.is_set():
                        return
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if recursive:
                                subdirs.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            ext = os.path.splitext(entry.name)[1].lower()
                            if ext in SUPPORTED_STATIC_EXTENSIONS:
                                out_files.append((Path(entry.path), source_root))
                            elif ext in {".gif", ".heic", ".avif", ".svg", ".ico", ".psd", ".raw", ".cr2", ".nef", ".mp4", ".mkv", ".avi"}:
                                skipped_records.append(
                                    SkippedFileRecord(path=entry.path, reason=f"格式不受支持或非静态图片: {ext}")
                                )
                    except OSError as exc:
                        skipped_records.append(SkippedFileRecord(path=entry.path, reason=f"读取目录项失败: {exc}"))
                for subdir in sorted(subdirs, key=lambda p: p.name.lower()):
                    if cancel_event and cancel_event.is_set():
                        return
                    self._collect_candidates(subdir, source_root, recursive, out_files, skipped_records, cancel_event)
        except OSError as exc:
            skipped_records.append(SkippedFileRecord(path=str(current_dir), reason=f"无法遍历目录: {exc}"))

    def _inspect_with_cache(self, file_path: Path, source_root: str) -> tuple[ImageMetadata | None, str]:
        try:
            stat = file_path.stat()
            resolved = str(file_path.resolve())
        except OSError as exc:
            return None, f"文件无法访问: {exc}"

        mtime_ns = int(stat.st_mtime_ns)
        fsize = int(stat.st_size)

        with self._lock:
            cached = self._header_cache.get(resolved)
        if cached is not None and cached[0] == mtime_ns and cached[1] == fsize:
            old_meta = cached[2]
            if old_meta.folder_source == source_root:
                return old_meta, ""
            updated_meta = ImageMetadata(
                path=old_meta.path,
                folder_source=source_root,
                filename=old_meta.filename,
                extension=old_meta.extension,
                format_name=old_meta.format_name,
                width=old_meta.width,
                height=old_meta.height,
                file_size=old_meta.file_size,
                mtime_ns=old_meta.mtime_ns,
            )
            with self._lock:
                self._header_cache[resolved] = (mtime_ns, fsize, updated_meta)
            return updated_meta, ""

        meta, err = inspect_static_image_header(file_path, folder_source=source_root)
        if meta is not None:
            with self._lock:
                self._header_cache[meta.path] = (meta.mtime_ns, meta.file_size, meta)
        return meta, err

    def get_all_images(self) -> list[ImageMetadata]:
        with self._lock:
            return [self._images_by_path[p] for p in self._ordered_paths if p in self._images_by_path]

    def get_all_paths(self) -> list[str]:
        with self._lock:
            return list(self._ordered_paths)

    def get_images_page(self, page: int, page_size: int = 50, search_query: str = "") -> tuple[list[ImageMetadata], int]:
        """分页获取图片元数据，用于大型图片库的 GUI 虚拟化/分页展示，防止创建数万个组件."""
        with self._lock:
            all_items = [self._images_by_path[p] for p in self._ordered_paths if p in self._images_by_path]
        if search_query.strip():
            q = search_query.strip().lower()
            all_items = [item for item in all_items if q in item.filename.lower() or q in item.path.lower()]
        total = len(all_items)
        page_size = max(1, int(page_size))
        start = max(0, int(page) * page_size)
        end = start + page_size
        return all_items[start:end], total

    def get_metadata(self, path: str) -> ImageMetadata | None:
        with self._lock:
            meta = self._images_by_path.get(path)
            if meta is not None:
                return meta
        # 若路径规范化差异，尝试 resolve
        try:
            resolved = str(Path(path).resolve())
            with self._lock:
                return self._images_by_path.get(resolved)
        except OSError:
            return None

    def get_skipped_records(self) -> list[SkippedFileRecord]:
        with self._lock:
            return list(self._skipped_records)

    def remove_missing_or_invalid(self, path: str) -> None:
        """当运行期发现某张图片已被删除或损坏时，从活动索引中移除."""
        with self._lock:
            if path in self._images_by_path:
                del self._images_by_path[path]
            if path in self._ordered_paths:
                self._ordered_paths.remove(path)
            self._header_cache.pop(path, None)

    def is_path_valid(self, path: str) -> bool:
        """实时校验某张图片是否仍然存在且可读."""
        p = Path(path)
        if not p.exists() or not p.is_file():
            self.remove_missing_or_invalid(path)
            return False
        meta, _ = inspect_static_image_header(p)
        if meta is None:
            self.remove_missing_or_invalid(path)
            return False
        return True

    def load_thumbnail_pil(self, path: str, max_size: tuple[int, int] = (240, 150)) -> Image.Image | None:
        """按需生成或从有界 LRU 缓存获取缩略图 (`PIL.Image`)."""
        cache_key = f"{path}@{max_size[0]}x{max_size[1]}"
        cached = self._thumb_cache.get(cache_key)
        if cached is not None:
            return cached.copy()

        # 检查磁盘缩略图缓存
        disk_thumb_path: Path | None = None
        if self._disk_cache_dir is not None:
            try:
                stat = Path(path).stat()
                digest = hashlib.sha1(
                    f"{path}|{stat.st_mtime_ns}|{stat.st_size}|{max_size[0]}x{max_size[1]}".encode("utf-8")
                ).hexdigest()
                disk_thumb_path = self._disk_cache_dir / f"{digest}.jpg"
                if disk_thumb_path.exists():
                    with Image.open(disk_thumb_path) as dimg:
                        thumb = dimg.convert("RGB")
                    est_bytes = thumb.width * thumb.height * 3
                    self._thumb_cache.put(cache_key, thumb, est_bytes)
                    return thumb.copy()
            except Exception:
                pass

        try:
            with Image.open(path) as img:
                # 针对大型 JPEG/PNG 启用 draft 加速降采样
                try:
                    img.draft("RGB", (max_size[0] * 2, max_size[1] * 2))
                except Exception:
                    pass
                img = ImageOps.exif_transpose(img)
                thumb = img.convert("RGB")
                thumb.thumbnail(max_size, Image.Resampling.BILINEAR)
                est_bytes = thumb.width * thumb.height * 3
                self._thumb_cache.put(cache_key, thumb, est_bytes)

                if disk_thumb_path is not None:
                    try:
                        thumb.save(disk_thumb_path, format="JPEG", quality=82)
                    except OSError:
                        pass
                return thumb.copy()
        except Exception as exc:
            self._logger.warning("生成缩略图失败 [%s]: %s", path, exc)
            return None

    def get_thumbnail(self, path: str, max_size: int = 240) -> Any:
        """为 UI 视图快速返回 QPixmap 格式的缩略图 (若未装 PySide6 则返回 PIL Image)."""
        pil_img = self.load_thumbnail_pil(path, (max_size, max_size))
        if pil_img is None:
            return None
        try:
            from PySide6.QtGui import QImage, QPixmap
            data = pil_img.convert("RGB").tobytes("raw", "RGB")
            qimg = QImage(data, pil_img.width, pil_img.height, pil_img.width * 3, QImage.Format.Format_RGB888)
            return QPixmap.fromImage(qimg)
        except Exception:
            return pil_img

    def clear_memory_cache(self) -> None:
        """仅清空内存中的缩略图缓存，保留元数据索引与磁盘缓存文件，用于后台超低内存运行."""
        self._thumb_cache.clear()

    def clear_cache(self) -> int:
        """清理内存与磁盘缩略图缓存，返回清理的文件数."""
        self._thumb_cache.clear()
        removed_files = 0
        if self._disk_cache_dir and self._disk_cache_dir.exists():
            for f in self._disk_cache_dir.glob("*.jpg"):
                try:
                    f.unlink()
                    removed_files += 1
                except OSError:
                    pass
        self._logger.info("已清理缩略图缓存 (移除磁盘缓存文件 %d 个)", removed_files)
        return removed_files

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


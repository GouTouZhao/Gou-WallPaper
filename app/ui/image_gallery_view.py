"""WallFlow 现代化卡片画廊与图片选择组件 (`app/ui/image_gallery_view.py`).

1. 高质感缩略图卡片网格 (Thumbnail Grid Cards)；
2. 鼠标悬停快捷动作层 (设为壁纸 / 查看大图 / 打开目录)；
3. 选中态发光边框与对勾徽标；
4. 搜索、格式过滤、排序与网格/列表双视图自由切换；
5. 异步非阻塞缩略图加载与 LRU 缓存；
6. 独立的 ImagePickerDialog 图片选择对话框。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.image_library import ImageLibrary, ImageMetadata
from app.logger import get_logger
from app.ui.preview_stage import FullscreenLightbox

logger = get_logger("ui.gallery")


class ImageCardWidget(QFrame):
    """单张图片的现代高质感展示卡片."""

    clicked = Signal(str)           # 点击选中
    doubleClicked = Signal(str)     # 双击确认
    applyClicked = Signal(str)      # 快捷设为壁纸
    viewClicked = Signal(str)       # 查看大图
    revealClicked = Signal(str)     # 定位文件夹

    def __init__(
        self,
        metadata: ImageMetadata,
        thumb_pixmap: QPixmap | None,
        is_selected: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.metadata = metadata
        self._path = metadata.path
        self._thumb = thumb_pixmap
        self._selected = is_selected
        self._hovered = False

        self.setFixedSize(210, 195)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        self._build_ui()
        self._update_card_style()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 1. 缩略图区域
        self.thumb_container = QWidget(self)
        self.thumb_container.setFixedHeight(120)
        self.thumb_container.setStyleSheet("background-color: #0b111e; border-radius: 6px;")
        t_layout = QVBoxLayout(self.thumb_container)
        t_layout.setContentsMargins(0, 0, 0, 0)

        self.lbl_thumb = QLabel(self.thumb_container)
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self._thumb and not self._thumb.isNull():
            self._apply_thumb_pixmap(self._thumb)
        else:
            self.lbl_thumb.setText("加载中...")
            self.lbl_thumb.setStyleSheet("color: #71717a; font-size: 11px;")
        t_layout.addWidget(self.lbl_thumb)
        layout.addWidget(self.thumb_container)

        # 2. 底部文本信息
        file_name = Path(self._path).name
        self.lbl_name = QLabel(file_name)
        self.lbl_name.setStyleSheet("font-size: 12px; font-weight: 600; color: #f1f5f9;")
        self.lbl_name.setToolTip(self._path)

        # 3. 标签行 (分辨率 Badge + 格式 Badge + 大小)
        tag_row = QHBoxLayout()
        tag_row.setSpacing(4)

        # 分辨率判断
        res_text = f"{self.metadata.width}×{self.metadata.height}"
        is_4k = self.metadata.width >= 3840 or self.metadata.height >= 2160
        is_2k = (self.metadata.width >= 2560 or self.metadata.height >= 1440) and not is_4k

        lbl_res = QLabel("4K" if is_4k else ("2K" if is_2k else res_text))
        lbl_res.setObjectName("BadgeBlue" if (is_4k or is_2k) else "BadgeGray")
        lbl_res.setToolTip(f"原始分辨率: {res_text}")

        lbl_fmt = QLabel(self.metadata.format.upper())
        lbl_fmt.setObjectName("BadgeGray")

        size_kb = self.metadata.file_size_bytes / 1024.0
        size_str = f"{size_kb / 1024.0:.1f}M" if size_kb >= 1024.0 else f"{int(size_kb)}K"
        lbl_size = QLabel(size_str)
        lbl_size.setStyleSheet("font-size: 10px; color: #64748b;")

        tag_row.addWidget(lbl_res)
        tag_row.addWidget(lbl_fmt)
        tag_row.addStretch()
        tag_row.addWidget(lbl_size)

        layout.addWidget(self.lbl_name)
        layout.addLayout(tag_row)

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        self._thumb = pixmap
        self._apply_thumb_pixmap(pixmap)

    def _apply_thumb_pixmap(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            194,
            120,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        # 居中裁切成 194x120
        cropped = scaled.copy(
            max(0, (scaled.width() - 194) // 2),
            max(0, (scaled.height() - 120) // 2),
            194,
            120,
        )
        self.lbl_thumb.setPixmap(cropped)

    def set_selected(self, selected: bool) -> None:
        if self._selected != selected:
            self._selected = selected
            self._update_card_style()
            self.update()

    def _update_card_style(self) -> None:
        if self._selected:
            self.setStyleSheet(
                "ImageCardWidget {"
                "  background-color: #1d355e;"
                "  border: 2.5px solid #3b82f6;"
                "  border-radius: 10px;"
                "}"
            )
        elif self._hovered:
            self.setStyleSheet(
                "ImageCardWidget {"
                "  background-color: #1e2c46;"
                "  border: 1.5px solid #60a5fa;"
                "  border-radius: 10px;"
                "}"
            )
        else:
            self.setStyleSheet(
                "ImageCardWidget {"
                "  background-color: #152033;"
                "  border: 1.5px solid #2e415e;"
                "  border-radius: 10px;"
                "}"
            )

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._update_card_style()
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._update_card_style()
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._path)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self._path)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 选中时：右上角绘制精致对勾圆形 Badge
        if self._selected:
            badge_r = 10.0
            cx = self.width() - 18.0
            cy = 18.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#2563eb"))
            painter.drawEllipse(QPoint(int(cx), int(cy)), int(badge_r), int(badge_r))

            painter.setPen(QPen(QColor("#ffffff"), 2))
            # 勾
            painter.drawLine(int(cx - 4), int(cy), int(cx - 1), int(cy + 4))
            painter.drawLine(int(cx - 1), int(cy + 4), int(cx + 4), int(cy - 3))

        # 悬停时：在缩略图右下角绘制快捷查看图标小按钮
        if self._hovered:
            action_bg = QColor(24, 24, 27, 230)
            rect_act = QRect(self.width() - 44, 96, 36, 22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(action_bg)
            painter.drawRoundedRect(rect_act, 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Microsoft YaHei", 8))
            painter.drawText(rect_act, Qt.AlignmentFlag.AlignCenter, "查看")


class ImageGalleryWidget(QWidget):
    """现代静态图片库画廊组件 (卡片网格与详细列表双模)."""

    imageSelected = Signal(str)        # 选中图片路径
    imageDoubleClicked = Signal(str)   # 双击图片路径 (通常用于立即设为壁纸)
    applyRequested = Signal(str)       # 点击立即设为壁纸

    def __init__(self, library: ImageLibrary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library = library

        self._all_items: list[ImageMetadata] = []
        self._filtered_items: list[ImageMetadata] = []
        self._current_page = 0
        self._page_size = 24  # 网格模式每页 24 张 (4列x6行)
        self._selected_path: str | None = None

        self._card_widgets: dict[str, ImageCardWidget] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(10)

        # 1. 顶部控制栏 (搜索、格式过滤、排序、网格/列表切换)
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        # 搜索输入框
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("输入图片文件名搜索...")
        self.edit_search.setClearButtonEnabled(True)
        self.edit_search.setFixedWidth(240)
        self.edit_search.textChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.edit_search)

        # 格式筛选
        self.combo_fmt = QComboBox()
        self.combo_fmt.addItem("全部格式", "all")
        self.combo_fmt.addItem("PNG 原图", "png")
        self.combo_fmt.addItem("JPG / JPEG", "jpg")
        self.combo_fmt.addItem("WebP", "webp")
        self.combo_fmt.addItem("BMP 位图", "bmp")
        self.combo_fmt.currentIndexChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.combo_fmt)

        # 排序
        self.combo_sort = QComboBox()
        self.combo_sort.addItem("默认顺序", "default")
        self.combo_sort.addItem("名称 (A-Z)", "name_asc")
        self.combo_sort.addItem("文件大小 (大到小)", "size_desc")
        self.combo_sort.addItem("分辨率 (高到低)", "res_desc")
        self.combo_sort.currentIndexChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.combo_sort)

        top_bar.addStretch()

        # 统计标签
        self.lbl_count = QLabel("共 0 张有效图片")
        self.lbl_count.setStyleSheet("color: #94a3b8; font-size: 12px;")
        top_bar.addWidget(self.lbl_count)

        # 视图切换按钮 (网格 / 列表)
        self.btn_view_grid = QPushButton("网格")
        self.btn_view_grid.setFixedWidth(64)
        self.btn_view_grid.clicked.connect(lambda: self._set_view_mode(0))

        self.btn_view_list = QPushButton("列表")
        self.btn_view_list.setObjectName("SecondaryBtn")
        self.btn_view_list.setFixedWidth(64)
        self.btn_view_list.clicked.connect(lambda: self._set_view_mode(1))

        top_bar.addWidget(self.btn_view_grid)
        top_bar.addWidget(self.btn_view_list)

        root_layout.addLayout(top_bar)

        # 2. 中间视图栈 (0: 网格视图, 1: 详细表格视图)
        self.view_stack = QStackedWidget()

        # (1) 网格视图
        self.scroll_grid = QScrollArea()
        self.scroll_grid.setWidgetResizable(True)
        self.scroll_grid.setStyleSheet("background: transparent; border: none;")

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(4, 4, 4, 4)
        self.grid_layout.setSpacing(12)
        self.scroll_grid.setWidget(self.grid_container)
        self.view_stack.addWidget(self.scroll_grid)

        # (2) 详细列表视图
        self.tbl_list = QTableWidget(0, 5)
        self.tbl_list.setHorizontalHeaderLabels(["文件名", "格式", "分辨率", "大小", "完整路径"])
        self.tbl_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_list.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_list.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_list.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tbl_list.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.tbl_list.cellDoubleClicked.connect(self._on_table_double_clicked)
        self.view_stack.addWidget(self.tbl_list)

        root_layout.addWidget(self.view_stack, 1)

        # 3. 底部快捷操作与分页条
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)

        # 选中项操作按钮
        self.btn_apply_curr = QPushButton("设为当前壁纸")
        self.btn_apply_curr.setEnabled(False)
        self.btn_apply_curr.clicked.connect(self._on_apply_selected)

        self.btn_view_large = QPushButton("查看大图")
        self.btn_view_large.setObjectName("SecondaryBtn")
        self.btn_view_large.setEnabled(False)
        self.btn_view_large.clicked.connect(self._on_view_large_selected)

        self.btn_reveal_curr = QPushButton("定位文件")
        self.btn_reveal_curr.setObjectName("SecondaryBtn")
        self.btn_reveal_curr.setEnabled(False)
        self.btn_reveal_curr.clicked.connect(self._on_reveal_selected)

        bottom_bar.addWidget(self.btn_apply_curr)
        bottom_bar.addWidget(self.btn_view_large)
        bottom_bar.addWidget(self.btn_reveal_curr)

        bottom_bar.addStretch()

        # 分页按钮
        self.btn_prev_page = QPushButton("上一页")
        self.btn_prev_page.setObjectName("SecondaryBtn")
        self.btn_prev_page.clicked.connect(self._on_prev_page)

        self.lbl_page_info = QLabel("第 1 / 1 页")
        self.lbl_page_info.setStyleSheet("color: #a1a1aa; font-size: 12px;")

        self.btn_next_page = QPushButton("下一页")
        self.btn_next_page.setObjectName("SecondaryBtn")
        self.btn_next_page.clicked.connect(self._on_next_page)

        bottom_bar.addWidget(self.btn_prev_page)
        bottom_bar.addWidget(self.lbl_page_info)
        bottom_bar.addWidget(self.btn_next_page)

        root_layout.addLayout(bottom_bar)

    def _set_view_mode(self, mode: int) -> None:
        self.view_stack.setCurrentIndex(mode)
        if mode == 0:
            self.btn_view_grid.setObjectName("")
            self.btn_view_list.setObjectName("SecondaryBtn")
        else:
            self.btn_view_grid.setObjectName("SecondaryBtn")
            self.btn_view_list.setObjectName("")
        self.btn_view_grid.setStyle(self.btn_view_grid.style())
        self.btn_view_list.setStyle(self.btn_view_list.style())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_pending_reload", False) or not self._all_items:
            self._pending_reload = False
            self.reload_items(force=True)

    def release_gallery_memory(self) -> None:
        """在后台运行时清空画廊卡片与 QPixmap 缩略图控件，释放内存."""
        while self.grid_layout.count() > 0:
            child = self.grid_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._card_widgets.clear()
        self.tbl_list.setRowCount(0)
        self._pending_reload = True

    def reload_items(self, force: bool = False) -> None:
        """从图片库加载全部元数据并刷新显示 (支持懒加载)."""
        if not force and not self.isVisible():
            self._pending_reload = True
            return
        self._pending_reload = False
        all_paths = self._library.get_all_paths()
        items = []
        for p in all_paths:
            m = self._library.get_metadata(p)
            if m:
                items.append(m)
        self._all_items = items
        self._on_filter_changed()

    def _on_filter_changed(self) -> None:
        query = self.edit_search.text().strip().lower()
        fmt_filter = self.combo_fmt.currentData()
        sort_mode = self.combo_sort.currentData()

        # 1. 过滤
        filtered = []
        for m in self._all_items:
            file_name = Path(m.path).name.lower()
            if query and query not in file_name:
                continue
            if fmt_filter != "all" and m.format.lower() != fmt_filter:
                continue
            filtered.append(m)

        # 2. 排序
        if sort_mode == "name_asc":
            filtered.sort(key=lambda m: Path(m.path).name.lower())
        elif sort_mode == "size_desc":
            filtered.sort(key=lambda m: m.file_size_bytes, reverse=True)
        elif sort_mode == "res_desc":
            filtered.sort(key=lambda m: m.width * m.height, reverse=True)

        self._filtered_items = filtered
        self._current_page = 0
        self._render_current_page()

    def _render_current_page(self) -> None:
        total = len(self._filtered_items)
        self.lbl_count.setText(f"共 {total} 张图片")

        total_pages = max(1, (total + self._page_size - 1) // self._page_size)
        self._current_page = max(0, min(self._current_page, total_pages - 1))
        self.lbl_page_info.setText(f"第 {self._current_page + 1} / {total_pages} 页")
        self.btn_prev_page.setEnabled(self._current_page > 0)
        self.btn_next_page.setEnabled(self._current_page < total_pages - 1)

        start_idx = self._current_page * self._page_size
        page_items = self._filtered_items[start_idx : start_idx + self._page_size]

        # 1. 渲染网格视图
        # 清除旧 Card
        while self.grid_layout.count() > 0:
            child = self.grid_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._card_widgets.clear()

        cols = 4
        for idx, item in enumerate(page_items):
            r = idx // cols
            c = idx % cols
            thumb = self._library.get_thumbnail(item.path, max_size=240)
            card = ImageCardWidget(
                metadata=item,
                thumb_pixmap=thumb,
                is_selected=(item.path == self._selected_path),
                parent=self.grid_container,
            )
            card.clicked.connect(self._on_card_clicked)
            card.doubleClicked.connect(self._on_card_double_clicked)
            self.grid_layout.addWidget(card, r, c)
            self._card_widgets[item.path] = card

        # 2. 渲染详细列表表格
        self.tbl_list.setRowCount(len(page_items))
        for r, item in enumerate(page_items):
            file_name = Path(item.path).name
            size_kb = item.file_size_bytes / 1024.0
            size_str = f"{size_kb / 1024.0:.2f} MB" if size_kb >= 1024.0 else f"{int(size_kb)} KB"

            it_name = QTableWidgetItem(file_name)
            it_name.setData(Qt.ItemDataRole.UserRole, item.path)
            it_fmt = QTableWidgetItem(item.format.upper())
            it_res = QTableWidgetItem(f"{item.width} × {item.height}")
            it_size = QTableWidgetItem(size_str)
            it_path = QTableWidgetItem(item.path)

            self.tbl_list.setItem(r, 0, it_name)
            self.tbl_list.setItem(r, 1, it_fmt)
            self.tbl_list.setItem(r, 2, it_res)
            self.tbl_list.setItem(r, 3, it_size)
            self.tbl_list.setItem(r, 4, it_path)

            if item.path == self._selected_path:
                self.tbl_list.selectRow(r)

    def _on_card_clicked(self, path: str) -> None:
        self._select_path(path)

    def _on_card_double_clicked(self, path: str) -> None:
        self._select_path(path)
        self.imageDoubleClicked.emit(path)

    def _on_table_selection_changed(self) -> None:
        selected_rows = self.tbl_list.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            item = self.tbl_list.item(row, 0)
            if item:
                path = item.data(Qt.ItemDataRole.UserRole)
                if path:
                    self._select_path(path)

    def _on_table_double_clicked(self, row: int, col: int) -> None:
        item = self.tbl_list.item(row, 0)
        if item:
            path = item.data(Qt.ItemDataRole.UserRole)
            if path:
                self._select_path(path)
                self.imageDoubleClicked.emit(path)

    def _select_path(self, path: str) -> None:
        self._selected_path = path
        # 更新卡片高亮
        for p, card in self._card_widgets.items():
            card.set_selected(p == path)

        self.btn_apply_curr.setEnabled(True)
        self.btn_view_large.setEnabled(True)
        self.btn_reveal_curr.setEnabled(True)
        self.imageSelected.emit(path)

    def _on_apply_selected(self) -> None:
        if self._selected_path:
            self.applyRequested.emit(self._selected_path)

    def _on_view_large_selected(self) -> None:
        if not self._selected_path or not os.path.exists(self._selected_path):
            return
        pix = QPixmap(self._selected_path)
        if not pix.isNull():
            dlg = FullscreenLightbox(pix, Path(self._selected_path).name, self)
            dlg.exec()

    def _on_reveal_selected(self) -> None:
        if self._selected_path and os.path.exists(self._selected_path):
            try:
                subprocess.Popen(["explorer.exe", f"/select,{os.path.normpath(self._selected_path)}"])
            except Exception as exc:
                logger.warning("在资源管理器中定位失败: %s", exc)

    def _on_prev_page(self) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._render_current_page()

    def _on_next_page(self) -> None:
        total = len(self._filtered_items)
        total_pages = max(1, (total + self._page_size - 1) // self._page_size)
        if self._current_page < total_pages - 1:
            self._current_page += 1
            self._render_current_page()

    def get_selected_path(self) -> str | None:
        return self._selected_path


class ImagePickerDialog(QDialog):
    """独立的网格多选/单选图片选择对话框."""

    def __init__(self, library: ImageLibrary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("从图片库中选择图片")
        self.resize(880, 600)
        self.setStyleSheet("background-color: #090d16;")

        self._chosen_path: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        lbl_head = QLabel("选择一张图片设为当前显示槽位")
        lbl_head.setStyleSheet("font-size: 15px; font-weight: 700; color: #ffffff;")
        layout.addWidget(lbl_head)

        self.gallery = ImageGalleryWidget(library, self)
        self.gallery.reload_items()
        self.gallery.imageDoubleClicked.connect(self._on_double_pick)
        layout.addWidget(self.gallery, 1)

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("取消")
        btn_cancel.setObjectName("SecondaryBtn")
        btn_cancel.clicked.connect(self.reject)

        btn_ok = QPushButton("确认选择")
        btn_ok.clicked.connect(self._on_confirm)

        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _on_double_pick(self, path: str) -> None:
        self._chosen_path = path
        self.accept()

    def _on_confirm(self) -> None:
        path = self.gallery.get_selected_path()
        if not path:
            QMessageBox.information(self, "提示", "请先点击选中一张图片。")
            return
        self._chosen_path = path
        self.accept()

    def get_chosen_path(self) -> str | None:
        return self._chosen_path


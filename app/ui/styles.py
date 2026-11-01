"""WallFlow 现代化深色高对比度科技感设计系统样式表 (`app/ui/styles.py`).

深度优化：
- 解决选项格子/边框与背景融为一体的问题：强化边框线条对比度、添加醒目的悬停与选中光晕。
- 明确区分可选 (enabled) 与不可选 (disabled) 选项状态。
- 为 QCheckBox / QRadioButton 提供清晰的 20px 独立指示框与高对比度勾选标记。
- 为 QComboBox 定制右侧下拉按钮与高对比度菜单项。
- 为 QTableWidget 提供醒目的表格网格线、交替行底色和清晰的选中态。
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.icon_helper import ensure_ui_icon_assets


def get_asset_file_path(filename: str) -> str:
    """获取图标资源的规范正斜杠绝对路径，支持源码环境与 PyInstaller 打包环境."""
    if getattr(sys, "frozen", False):
        base_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base_dir = Path(__file__).resolve().parent.parent.parent
    p = base_dir / "assets" / filename
    if not p.exists():
        ensure_ui_icon_assets(base_dir / "assets")
    return p.resolve().as_posix()


def build_stylesheet() -> str:
    cb_u = get_asset_file_path("cb_unchecked.png")
    cb_c = get_asset_file_path("cb_checked.png")
    cb_d = get_asset_file_path("cb_disabled.png")
    r_u = get_asset_file_path("radio_unchecked.png")
    r_c = get_asset_file_path("radio_checked.png")
    r_d = get_asset_file_path("radio_disabled.png")
    arr = get_asset_file_path("arrow_down.png")
    arr_d = get_asset_file_path("arrow_down_disabled.png")

    return f"""
/* 全局基础设置 - 黑白灰简约风 */
QWidget {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: #e4e4e7;
}}

QMainWindow, QDialog {{
    background-color: #000000;
}}

/* 顶部与通用标题 */
QLabel#AppHeaderTitle {{
    font-size: 16px;
    font-weight: 700;
    color: #ffffff;
    letter-spacing: -0.3px;
}}

QLabel#AppHeaderSubtitle {{
    font-size: 11px;
    color: #888888;
}}

QLabel#PageTitle {{
    font-size: 20px;
    font-weight: 700;
    color: #ffffff;
    padding-bottom: 2px;
    letter-spacing: -0.5px;
}}

QLabel#PageDescription {{
    font-size: 12px;
    color: #888888;
    margin-bottom: 6px;
}}

/* 侧边导航栏 - 仿 front 极简黑白灰卡片风格 */
QFrame#SidebarFrame {{
    background-color: #000000;
    border-right: 1px solid #262626;
}}

QListWidget#NavList {{
    background-color: transparent;
    border: none;
    outline: none;
    padding: 8px 4px;
}}

QListWidget#NavList::item {{
    height: 38px;
    padding-left: 14px;
    margin: 2px 6px;
    border-radius: 6px;
    color: #a1a1aa;
    font-size: 13px;
    font-weight: 500;
    border: 1px solid transparent;
}}

QListWidget#NavList::item:hover {{
    background-color: #18181b;
    border-color: #27272a;
    color: #ffffff;
}}

QListWidget#NavList::item:selected {{
    background-color: #ffffff;
    border-color: #ffffff;
    color: #000000;
    font-weight: 700;
}}

/* 现代化卡片容器 (Card) 与分组框 (GroupBox) */
QFrame#Card, QGroupBox {{
    background-color: #121212;
    border: 1px solid #262626;
    border-radius: 12px;
    margin-top: 12px;
    padding: 18px;
    font-weight: 600;
    color: #ffffff;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    padding: 0 6px;
    color: #ffffff;
    font-size: 13px;
    font-weight: 700;
    background-color: #121212;
}}

/* 显示器画布容器 */
QFrame#MonitorMockupFrame {{
    background-color: #000000;
    border: 1px solid #262626;
    border-radius: 12px;
}}

/* 底部常驻 Action Bar */
QFrame#ActionBarFrame {{
    background-color: #000000;
    border-top: 1px solid #262626;
    padding: 0;
}}

QFrame#ActionBarFrame QPushButton {{
    padding: 4px 12px;
    font-size: 12px;
    font-weight: 600;
    min-height: 28px;
    max-height: 28px;
    border-radius: 6px;
}}

/* 按钮规范 - 黑白灰极简高对比度 */
QPushButton {{
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #ffffff;
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 600;
    font-size: 13px;
}}

QPushButton:hover {{
    background-color: #e4e4e7;
    border-color: #e4e4e7;
}}

QPushButton:pressed {{
    background-color: #d4d4d8;
    border-color: #d4d4d8;
}}

QPushButton:disabled {{
    background-color: #27272a;
    border-color: #27272a;
    color: #71717a;
}}

QPushButton#SecondaryBtn, QPushButton[variant="secondary"] {{
    background-color: #18181b;
    color: #f4f4f5;
    border: 1px solid #3f3f46;
    border-radius: 6px;
    font-weight: 500;
}}

QPushButton#SecondaryBtn:hover, QPushButton[variant="secondary"]:hover {{
    background-color: #27272a;
    border-color: #71717a;
    color: #ffffff;
}}

QPushButton#SecondaryBtn:pressed, QPushButton[variant="secondary"]:pressed {{
    background-color: #09090b;
}}

QPushButton#SecondaryBtn:disabled {{
    background-color: #121212;
    border-color: #262626;
    color: #52525b;
}}

QPushButton#OutlineBtn {{
    background-color: transparent;
    color: #d4d4d8;
    border: 1px solid #3f3f46;
    border-radius: 6px;
}}

QPushButton#OutlineBtn:hover {{
    background-color: #18181b;
    color: #ffffff;
    border-color: #71717a;
}}

QPushButton#DangerBtn {{
    background-color: #18181b;
    color: #fca5a5;
    border: 1px solid #7f1d1d;
    border-radius: 6px;
}}

QPushButton#DangerBtn:hover {{
    background-color: #27272a;
    border-color: #ef4444;
}}

QPushButton#SuccessBtn {{
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #ffffff;
    border-radius: 6px;
}}

QPushButton#SuccessBtn:hover {{
    background-color: #e4e4e7;
}}

/* 单选/复选框 */
QCheckBox, QRadioButton {{
    spacing: 8px;
    color: #e4e4e7;
    font-size: 13px;
    font-weight: 500;
}}

QCheckBox:hover, QRadioButton:hover {{
    color: #ffffff;
}}

QCheckBox:disabled, QRadioButton:disabled {{
    color: #52525b;
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 18px;
    height: 18px;
}}

QCheckBox::indicator:unchecked {{
    image: url("{cb_u}");
}}

QCheckBox::indicator:checked {{
    image: url("{cb_c}");
}}

QCheckBox::indicator:disabled {{
    image: url("{cb_d}");
}}

QRadioButton::indicator:unchecked {{
    image: url("{r_u}");
}}

QRadioButton::indicator:checked {{
    image: url("{r_c}");
}}

QRadioButton::indicator:disabled {{
    image: url("{r_d}");
}}

/* 下拉框 (QComboBox) */
QComboBox {{
    background-color: #141414;
    border: 1px solid #333333;
    border-radius: 6px;
    padding: 6px 12px;
    padding-right: 32px;
    color: #ffffff;
    font-size: 13px;
    font-weight: 500;
    min-height: 22px;
}}

QComboBox:hover {{
    border-color: #71717a;
    background-color: #1c1c1c;
}}

QComboBox:focus {{
    border: 1px solid #ffffff;
    background-color: #1c1c1c;
}}

QComboBox:disabled {{
    background-color: #0d0d0d;
    border-color: #262626;
    color: #52525b;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border-left: 1px solid #333333;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
    background-color: #141414;
}}

QComboBox::drop-down:hover {{
    background-color: #262626;
}}

QComboBox::down-arrow {{
    image: url("{arr}");
    width: 12px;
    height: 12px;
}}

QComboBox::down-arrow:disabled {{
    image: url("{arr_d}");
}}

QComboBox QAbstractItemView {{
    background-color: #141414;
    border: 1px solid #333333;
    border-radius: 8px;
    padding: 6px;
    outline: none;
    selection-background-color: #262626;
    selection-color: #ffffff;
}}

QComboBox QAbstractItemView::item {{
    height: 32px;
    padding-left: 10px;
    border-radius: 4px;
    color: #e4e4e7;
}}

QComboBox QAbstractItemView::item:hover {{
    background-color: #262626;
    color: #ffffff;
}}

QComboBox QAbstractItemView::item:selected {{
    background-color: #262626;
    color: #ffffff;
    font-weight: 600;
}}

/* 输入框与数字微调器 */
QLineEdit, QSpinBox {{
    background-color: #141414;
    border: 1px solid #333333;
    border-radius: 6px;
    padding: 6px 12px;
    color: #ffffff;
    font-size: 13px;
    font-weight: 500;
    selection-background-color: #333333;
}}

QLineEdit:hover, QSpinBox:hover {{
    border-color: #71717a;
}}

QLineEdit:focus, QSpinBox:focus {{
    border: 1px solid #ffffff;
    background-color: #1c1c1c;
}}

QLineEdit:disabled, QSpinBox:disabled {{
    background-color: #0d0d0d;
    border-color: #262626;
    color: #52525b;
}}

/* 滑块控件 (QSlider) */
QSlider::groove:horizontal {{
    height: 4px;
    background: #27272a;
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: #ffffff;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: #ffffff;
    border: 1px solid #000000;
    width: 14px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 7px;
}}

QSlider::handle:horizontal:hover {{
    background: #d4d4d8;
}}

/* 表格控件 */
QTableWidget {{
    background-color: #0d0d0d;
    alternate-background-color: #141414;
    border: 1px solid #262626;
    border-radius: 8px;
    gridline-color: #262626;
    color: #e4e4e7;
    selection-background-color: #262626;
    selection-color: #ffffff;
    outline: none;
}}

QTableWidget::item {{
    padding: 8px 10px;
    border-bottom: 1px solid #1f1f1f;
}}

QTableWidget::item:hover {{
    background-color: #1c1c1c;
}}

QTableWidget::item:selected {{
    background-color: #262626;
    color: #ffffff;
    font-weight: 600;
}}

QHeaderView::section {{
    background-color: #18181b;
    color: #ffffff;
    font-weight: 600;
    padding: 8px 10px;
    border: none;
    border-right: 1px solid #262626;
    border-bottom: 1px solid #3f3f46;
}}

/* 文本日志查看器 */
QPlainTextEdit {{
    background-color: #0a0a0a;
    color: #d4d4d8;
    border: 1px solid #262626;
    border-radius: 8px;
    padding: 10px;
    font-family: "Cascadia Code", "Consolas", monospace;
    font-size: 12px;
}}

/* 进度条 */
QProgressBar {{
    background-color: #27272a;
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: #ffffff;
    border-radius: 3px;
}}

/* 徽章 Badge 样式 - 极简黑白灰高对比 */
QLabel#BadgeTimestamp, QLabel#BadgeMonotonic, QLabel#BadgeBlue, QLabel#BadgeGray {{
    background-color: #18181b;
    color: #f4f4f5;
    border: 1px solid #3f3f46;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 600;
}}

/* 滚动条 (QScrollBar) 精细定制 */
QScrollBar:vertical {{
    border: none;
    background: transparent;
    width: 6px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: #333333;
    min-height: 24px;
    border-radius: 3px;
}}

QScrollBar::handle:vertical:hover {{
    background: #555555;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    border: none;
    background: none;
    height: 0px;
}}

QScrollBar:horizontal {{
    border: none;
    background: transparent;
    height: 6px;
    margin: 0px;
}}

QScrollBar::handle:horizontal {{
    background: #333333;
    min-width: 24px;
    border-radius: 3px;
}}

QScrollBar::handle:horizontal:hover {{
    background: #555555;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    border: none;
    background: none;
    width: 0px;
}}

/* 底部状态栏样式 - 黑灰极简风 */
QStatusBar {{
    background-color: #09090b;
    color: #a1a1aa;
    border-top: 1px solid #27272a;
    font-size: 12px;
    padding: 2px 10px;
}}
QStatusBar QLabel {{
    color: #a1a1aa;
}}
"""


MAIN_STYLESHEET: str = build_stylesheet()

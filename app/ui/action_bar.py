"""WallFlow 底部常驻持久化操作栏 (`app/ui/action_bar.py`).

无论用户切到哪个设置页面，常用的核心操作（上一张、立即应用、下一张、暂停/启动轮播、随机、屏保测试）始终一键直达！
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)


class BottomActionBar(QFrame):
    """窗口底部常驻操作栏."""

    prevClicked = Signal()
    applyClicked = Signal()
    nextClicked = Signal()
    togglePauseClicked = Signal()
    randomClicked = Signal()
    screensaverClicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ActionBarFrame")
        self.setFixedHeight(50)

        self._is_paused = False

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        # 1. 壁纸切换三件套 (上一张, 立即应用, 下一张)
        self.btn_prev = QPushButton("上一张")
        self.btn_prev.setObjectName("SecondaryBtn")
        self.btn_prev.setToolTip("切换并应用上一张壁纸")
        self.btn_prev.clicked.connect(self.prevClicked)

        self.btn_apply = QPushButton("应用")
        self.btn_apply.setToolTip("重新应用或刷新当前桌面壁纸")
        self.btn_apply.clicked.connect(self.applyClicked)

        self.btn_next = QPushButton("下一张")
        self.btn_next.setObjectName("SecondaryBtn")
        self.btn_next.setToolTip("切换并应用下一张壁纸")
        self.btn_next.clicked.connect(self.nextClicked)

        layout.addWidget(self.btn_prev)
        layout.addWidget(self.btn_apply)
        layout.addWidget(self.btn_next)

        # 分割线
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("color: #27272a; max-height: 20px;")
        layout.addWidget(sep1)

        # 2. 轮播控制
        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setObjectName("SecondaryBtn")
        self.btn_pause.setToolTip("暂停或恢复自动定时壁纸轮播")
        self.btn_pause.clicked.connect(self.togglePauseClicked)
        layout.addWidget(self.btn_pause)

        # 3. 随机切换
        self.btn_random = QPushButton("随机")
        self.btn_random.setObjectName("SecondaryBtn")
        self.btn_random.setToolTip("在图片库中不放回随机选择一张新壁纸并立即应用")
        self.btn_random.clicked.connect(self.randomClicked)
        layout.addWidget(self.btn_random)

        # 4. 屏保测试
        self.btn_ss = QPushButton("屏保")
        self.btn_ss.setObjectName("SecondaryBtn")
        self.btn_ss.setToolTip("立即启动 Windows 原生全屏屏幕保护程序")
        self.btn_ss.clicked.connect(self.screensaverClicked)
        layout.addWidget(self.btn_ss)

        layout.addStretch()

        # 5. 右侧运行状态指示与倒计时胶囊
        self.lbl_mode_badge = QLabel("时间戳")
        self.lbl_mode_badge.setObjectName("BadgeTimestamp")

        self.lbl_countdown = QLabel("下次: 计算中...")
        self.lbl_countdown.setStyleSheet("font-size: 12px; font-weight: 600; color: #ffffff;")

        self.lbl_library_stat = QLabel("0 张")
        self.lbl_library_stat.setStyleSheet("font-size: 11px; color: #a1a1aa;")

        layout.addWidget(self.lbl_library_stat)
        layout.addWidget(self.lbl_mode_badge)
        layout.addWidget(self.lbl_countdown)

    def set_paused_state(self, is_paused: bool) -> None:
        self._is_paused = is_paused
        if is_paused:
            self.btn_pause.setText("恢复")
            self.btn_pause.setStyleSheet("background-color: #ffffff; color: #000000; font-weight: 700;")
        else:
            self.btn_pause.setText("暂停")
            self.btn_pause.setStyleSheet("")

    def update_status(
        self,
        mode_text: str,
        is_timestamp_mode: bool,
        countdown_text: str,
        image_count: int,
    ) -> None:
        short_mode = "时间戳" if is_timestamp_mode else "单调时钟"
        self.lbl_mode_badge.setText(short_mode)
        self.lbl_mode_badge.setObjectName("BadgeTimestamp" if is_timestamp_mode else "BadgeMonotonic")
        self.lbl_mode_badge.setStyle(self.lbl_mode_badge.style())
        self.lbl_countdown.setText(f"下次: {countdown_text}")
        self.lbl_library_stat.setText(f"图库: {image_count}张")


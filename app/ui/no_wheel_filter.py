"""全局拦截与禁用控件滚轮切换功能 (`app/ui/no_wheel_filter.py`).

彻底解决 QComboBox、QSpinBox、QSlider 等控件在页面滑动或光标悬停时因鼠标滚轮导致的误触切换与页面卡顿问题。
将捕获的滚轮事件无损转发给所在的 QScrollArea 视口，保证页面滚动极其流畅，且控件值绝对不发生任何意外变动。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QScrollArea,
    QSlider,
    QWidget,
)


class GlobalNoWheelFilter(QObject):
    """全局滚轮事件拦截器.

    拦截所有 QComboBox、QAbstractSpinBox (含 QSpinBox)、QSlider 的滚轮事件：
    - 阻止控件因滚轮改变选项或数值（彻底根除滚轮误触）；
    - 若控件处于 QScrollArea 内，自动将滚轮事件转发给其 viewport，实现平滑流畅的页面滚动。
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel:
            target = watched
            # 如果是 QAbstractSpinBox 内部的 QLineEdit 等子部件
            if isinstance(target, QWidget) and isinstance(target.parent(), (QComboBox, QAbstractSpinBox, QSlider)):
                target = target.parent()

            if isinstance(target, (QComboBox, QAbstractSpinBox, QSlider)):
                # 若下拉框已展开（处于弹出列表视图），允许在弹出列表内部滚动浏览选项
                if isinstance(target, QComboBox):
                    view = target.view()
                    if view is not None and view.isVisible():
                        return False

                # 寻找祖先中的 QScrollArea，将滚轮事件转发以保持页面顺畅滚动
                parent = target.parent()
                scroll_area = None
                while parent is not None:
                    if isinstance(parent, QScrollArea):
                        scroll_area = parent
                        break
                    parent = parent.parent()

                if scroll_area is not None:
                    QApplication.sendEvent(scroll_area.viewport(), event)

                # 消费该事件，坚决阻止控件修改自身索引或数值
                return True

        return super().eventFilter(watched, event)


_GLOBAL_FILTER_INSTANCE: GlobalNoWheelFilter | None = None


def install_global_no_wheel_filter(app: QApplication | None = None) -> GlobalNoWheelFilter:
    """在 QApplication 上安装全局防滚轮误触拦截器，确保全程序生效."""
    global _GLOBAL_FILTER_INSTANCE
    if app is None:
        app = QApplication.instance()
    if app is None:
        return GlobalNoWheelFilter()

    if _GLOBAL_FILTER_INSTANCE is None:
        _GLOBAL_FILTER_INSTANCE = GlobalNoWheelFilter(app)
        app.installEventFilter(_GLOBAL_FILTER_INSTANCE)
        # 挂载在 app 上防止被垃圾回收
        setattr(app, "_global_no_wheel_filter", _GLOBAL_FILTER_INSTANCE)
    return _GLOBAL_FILTER_INSTANCE


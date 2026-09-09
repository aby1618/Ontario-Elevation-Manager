from qgis.PyQt.QtCore import Qt, QEvent
from qgis.PyQt.QtGui import QCursor
from qgis.PyQt.QtWidgets import (
    QBoxLayout,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
)


class CollapsibleSection(QWidget):
    """Compact collapsible wrapper used for the numbered dock sections."""

    def __init__(
        self,
        title,
        content_widget,
        settings=None,
        settings_key=None,
        default_expanded=True,
        parent=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.settings_key = settings_key
        self.content_widget = content_widget

        expanded = bool(default_expanded)
        if self.settings is not None and self.settings_key:
            saved = self.settings.value(
                self.settings_key,
                expanded,
                type=bool,
            )
            expanded = bool(saved)

        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Fixed,
        )
        self.header.setStyleSheet(
            "QToolButton { font-weight: 600; border: none; "
            "padding: 5px 2px; text-align: left; }"
        )
        self.header.clicked.connect(self.set_expanded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.header)
        layout.addWidget(self.content_widget)

        self.set_expanded(expanded, persist=False)

    def is_expanded(self):
        return self.header.isChecked()

    def set_expanded(self, expanded, persist=True):
        expanded = bool(expanded)
        self.header.blockSignals(True)
        self.header.setChecked(expanded)
        self.header.blockSignals(False)
        self.header.setArrowType(
            Qt.DownArrow if expanded else Qt.RightArrow
        )
        self.content_widget.setVisible(expanded)

        if (
            persist
            and self.settings is not None
            and self.settings_key
        ):
            self.settings.setValue(
                self.settings_key,
                expanded,
            )


class HelpButton(QToolButton):
    """Tiny question-mark helper which exposes longer guidance on demand."""

    def __init__(self, tooltip="", parent=None):
        super().__init__(parent)
        self.setText("?")
        self.setAutoRaise(True)
        self.setCursor(Qt.WhatsThisCursor)
        self.setToolTip(str(tooltip or ""))
        self.setFixedSize(18, 18)
        self.setStyleSheet(
            "QToolButton {"
            " border: 1px solid palette(mid);"
            " border-radius: 8px;"
            " font-weight: 600;"
            " padding: 0px;"
            "}"
        )
        self.clicked.connect(self._show_tip)

    def set_help_text(self, text):
        self.setToolTip(str(text or ""))

    def _show_tip(self):
        if self.toolTip():
            QToolTip.showText(
                QCursor.pos(),
                self.toolTip(),
                self,
            )


class ResponsiveRow(QWidget):
    """Horizontal row which stacks vertically when the dock becomes narrow."""

    def __init__(
        self,
        widgets=None,
        breakpoint=390,
        parent=None,
    ):
        super().__init__(parent)
        self.breakpoint = int(breakpoint)
        self.box = QBoxLayout(QBoxLayout.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(5)

        for widget in widgets or []:
            self.addWidget(widget)

    def addWidget(self, widget, stretch=0):
        if hasattr(widget, "setMinimumWidth"):
            widget.setMinimumWidth(0)
        if hasattr(widget, "setSizePolicy"):
            policy = widget.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Expanding)
            widget.setSizePolicy(policy)
        self.box.addWidget(widget, stretch)

    def resizeEvent(self, event):
        direction = (
            QBoxLayout.TopToBottom
            if event.size().width() < self.breakpoint
            else QBoxLayout.LeftToRight
        )
        if self.box.direction() != direction:
            self.box.setDirection(direction)
        super().resizeEvent(event)

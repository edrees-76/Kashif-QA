"""Dialog utilities and responsive helpers for KASHIF-QA GUI."""

from typing import Optional
from datetime import datetime, timezone
from PyQt6.QtCore import Qt, QPoint, QObject, QEvent, QTimer, QRect, QSize
from PyQt6.QtWidgets import (
    QDialog,
    QComboBox,
    QDateEdit,
    QSpinBox,
    QDoubleSpinBox,
    QAbstractSpinBox,
    QAbstractScrollArea,
    QApplication,
    QScrollArea,
    QFrame,
    QPushButton,
    QWidget,
    QSizePolicy,
)

from kashif.gui.bidi import isolate_ltr


def enable_dialog_window_controls(
    dialog: QDialog,
    allow_maximize: bool = True,
    allow_minimize: bool = True,
) -> None:
    """Ensure standard window controls (Maximize, Minimize) are enabled on QDialog.
    
    By default, Qt on some platforms suppresses maximize and minimize buttons for QDialog.
    This helper explicitly enables them while preserving other window flags.
    """
    flags = dialog.windowFlags()
    if allow_maximize:
        flags |= Qt.WindowType.WindowMaximizeButtonHint
    else:
        flags &= ~Qt.WindowType.WindowMaximizeButtonHint

    if allow_minimize:
        flags |= Qt.WindowType.WindowMinimizeButtonHint
    else:
        flags &= ~Qt.WindowType.WindowMinimizeButtonHint

    dialog.setWindowFlags(flags)


class ResponsiveButtonContainer(QWidget):
    """Container widget that constrains button rows to max_content_width and centers them."""

    def __init__(self, target_width: int = 1000, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.target_width = target_width
        self.setMaximumWidth(target_width)

    def sizeHint(self) -> QSize:
        h = super().sizeHint().height()
        return QSize(self.target_width, h if h > 0 else 36)


class ResponsiveDialogFilter(QObject):
    """Event filter that dynamically adjusts dialog layout margins so that content
    expands up to max_content_width on wide displays while remaining centered horizontally,
    without shrinking below its natural size on smaller screens."""

    def __init__(self, dialog: QDialog, max_content_width: int = 1000):
        super().__init__(dialog)
        self.dialog = dialog
        self.max_content_width = max_content_width
        layout = dialog.layout()
        if layout:
            m = layout.contentsMargins()
            self.base_left = m.left()
            self.base_top = m.top()
            self.base_right = m.right()
            self.base_bottom = m.bottom()
        else:
            self.base_left = self.base_top = self.base_right = self.base_bottom = 20

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.update_margins()
        return super().eventFilter(obj, event)

    def update_margins(self) -> None:
        layout = self.dialog.layout()
        if not layout:
            return
        w = self.dialog.width()
        total_base = self.base_left + self.base_right
        if w > self.max_content_width + total_base:
            extra = w - self.max_content_width
            m_left = extra // 2
            m_right = extra - m_left
            new_margins = (m_left, self.base_top, m_right, self.base_bottom)
        else:
            new_margins = (self.base_left, self.base_top, self.base_right, self.base_bottom)
        cur = layout.contentsMargins()
        if (cur.left(), cur.top(), cur.right(), cur.bottom()) != new_margins:
            layout.setContentsMargins(*new_margins)
            layout.activate()


def constrain_dialog_content_width(dialog: QDialog, max_content_width: int = 1000) -> None:
    """Constrain dialog content/form width and center it so fields do not stretch excessively on large screens.
    
    Ensures that when a dialog is maximized on wide displays (e.g. 1920px), the form remains
    at a readable width (around 1000px) and neatly centered horizontally.
    """
    scroll_areas = dialog.findChildren(QScrollArea)
    if scroll_areas:
        for sa in scroll_areas:
            sa.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            widget = sa.widget()
            if widget:
                widget.setMaximumWidth(max_content_width)
                widget.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    widget.sizePolicy().verticalPolicy(),
                )
    else:
        existing_filters = [c for c in dialog.children() if isinstance(c, ResponsiveDialogFilter)]
        if existing_filters:
            rf = existing_filters[0]
            rf.max_content_width = max_content_width
            rf.update_margins()
        else:
            rf = ResponsiveDialogFilter(dialog, max_content_width=max_content_width)
            dialog.installEventFilter(rf)
            rf.update_margins()

        d_layout = dialog.layout()
        for frame in dialog.findChildren(QFrame):
            if frame.parent() == dialog:
                frame.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    frame.sizePolicy().verticalPolicy(),
                )
                if d_layout:
                    d_layout.setAlignment(frame, Qt.AlignmentFlag(0))

    d_layout = dialog.layout()
    if d_layout:
        for i in range(d_layout.count()):
            item = d_layout.itemAt(i)
            if not item:
                continue

            w = item.widget()
            if isinstance(w, ResponsiveButtonContainer):
                w.target_width = max_content_width
                w.setMaximumWidth(max_content_width)
                d_layout.setAlignment(w, Qt.AlignmentFlag.AlignHCenter)
                continue

            child_l = item.layout()
            if not child_l:
                continue

            # Check if this child layout is a button row (e.g. Save/Cancel/Action buttons)
            child_widgets = [
                child_l.itemAt(j).widget()
                for j in range(child_l.count())
                if child_l.itemAt(j) and child_l.itemAt(j).widget()
            ]
            if child_widgets and all(isinstance(bw, QPushButton) for bw in child_widgets):
                d_layout.removeItem(item)
                container = ResponsiveButtonContainer(max_content_width, parent=dialog)
                child_l.setContentsMargins(0, 0, 0, 0)
                container.setLayout(child_l)
                d_layout.insertWidget(i, container, 0, Qt.AlignmentFlag.AlignHCenter)

    for btn in dialog.findChildren(QPushButton):
        if btn.parent() == dialog and dialog.layout():
            btn.setMaximumWidth(max_content_width)
            dialog.layout().setAlignment(btn, Qt.AlignmentFlag.AlignHCenter)


def adjust_dialog_size_to_screen(
    dialog: QDialog,
    max_ratio: float = 0.90,
    preferred_width: Optional[int] = None,
    preferred_height: Optional[int] = None,
    max_content_width: int = 1000,
) -> None:
    """Adjust dialog size so it never exceeds max_ratio of the available screen geometry,
    and constrain inner content/form width so fields do not stretch across wide screens."""
    constrain_dialog_content_width(dialog, max_content_width=max_content_width)
    install_wheel_guard_on_inputs(dialog)

    screen = QApplication.primaryScreen()
    if not screen:
        if preferred_width and preferred_height:
            dialog.resize(preferred_width, preferred_height)
        return

    avail = screen.availableGeometry()
    max_w = int(avail.width() * max_ratio)
    max_h = int(avail.height() * max_ratio)

    hint = dialog.sizeHint()
    cur_w = preferred_width if preferred_width is not None else hint.width()
    cur_h = preferred_height if preferred_height is not None else hint.height()

    target_w = max(dialog.minimumWidth(), min(cur_w if cur_w > 0 else 600, max_w))
    target_h = max(dialog.minimumHeight(), min(cur_h if cur_h > 0 else 700, max_h))

    dialog.resize(target_w, target_h)
    for c in dialog.children():
        if isinstance(c, ResponsiveDialogFilter):
            c.update_margins()


class CalendarPopupEventFilter(QObject):
    """Event filter to ensure calendar popup opens below the input cell without obscuring upper rows."""

    def __init__(self, date_edit: QDateEdit):
        super().__init__(date_edit)
        self.date_edit = date_edit

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show:
            QTimer.singleShot(0, self._adjust_position)
        return super().eventFilter(obj, event)

    def _adjust_position(self) -> None:
        cal = self.date_edit.calendarWidget()
        if not cal:
            return
        popup = cal.window()
        if not popup or not popup.isVisible():
            return

        de_rect = self.date_edit.rect()
        de_bottom_left = self.date_edit.mapToGlobal(de_rect.bottomLeft())
        de_bottom_right = self.date_edit.mapToGlobal(de_rect.bottomRight())

        screen = QApplication.screenAt(de_bottom_left) or QApplication.primaryScreen()
        avail_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        popup_w = popup.width()
        popup_h = popup.height()

        # In RTL layout, align the right edge of the popup with the right edge of the date cell
        if self.date_edit.isRightToLeft():
            target_x = de_bottom_right.x() - popup_w
        else:
            target_x = de_bottom_left.x()

        target_y = de_bottom_left.y() + 1
        target_x = max(avail_geo.left(), min(target_x, avail_geo.right() - popup_w))

        # Position below the cell if space permits on screen
        if target_y + popup_h <= avail_geo.bottom() or (avail_geo.bottom() - target_y) >= (popup_h * 0.75):
            popup.move(target_x, target_y)


def attach_calendar_popup_filter(date_edit: QDateEdit) -> None:
    """Attach the repositioning event filter to the date edit's calendar popup window."""
    cal = date_edit.calendarWidget()
    if cal:
        popup = cal.window()
        filter_obj = CalendarPopupEventFilter(date_edit)
        popup.installEventFilter(filter_obj)
        # Keep a reference to prevent garbage collection
        setattr(date_edit, "_calendar_popup_filter", filter_obj)


class WheelScrollGuardFilter(QObject):
    """Event filter that prevents unfocused input widgets (QComboBox, QDateEdit,
    QSpinBox, QDoubleSpinBox) from consuming wheel events and accidentally changing values.

    When unfocused, the wheel event is ignored and forwarded up the widget hierarchy
    to allow the enclosing QAbstractScrollArea (such as QScrollArea or QTableWidget) to scroll.
    When focused, the wheel event is processed normally by the widget.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Wheel:
            widget = obj if isinstance(obj, QWidget) else None
            if widget:
                has_focus = widget.hasFocus()
                if not has_focus:
                    for child in widget.findChildren(QWidget):
                        if child.hasFocus():
                            has_focus = True
                            break

                if not has_focus:
                    event.ignore()
                    parent = widget.parentWidget()
                    while parent is not None:
                        if isinstance(parent, QAbstractScrollArea):
                            QApplication.sendEvent(parent.viewport(), event)
                            return True
                        parent = parent.parentWidget()
                    return True

        return super().eventFilter(obj, event)


GUARD_INPUT_CLASSES = (QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox, QAbstractSpinBox)


def install_wheel_guard_on_inputs(target: QWidget) -> None:
    """Ensure all QComboBox, QDateEdit, QSpinBox, and QDoubleSpinBox controls within
    target (or target itself) have StrongFocus policy and ignore wheel events when unfocused."""
    guard = getattr(target, "_wheel_scroll_guard_filter", None)
    if guard is None:
        guard = WheelScrollGuardFilter(target)
        setattr(target, "_wheel_scroll_guard_filter", guard)

    widgets = []
    if isinstance(target, GUARD_INPUT_CLASSES):
        widgets.append(target)
    for cls in GUARD_INPUT_CLASSES:
        widgets.extend(target.findChildren(cls))

    for w in set(widgets):
        w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        w.installEventFilter(guard)


def format_utc_to_local(utc_str: Optional[str], default: str = "لم يدخل بعد") -> str:
    """Format an ISO UTC datetime string into device local datetime string 'YYYY-MM-DD HH:MM'.

    Preserves database representation unchanged while presenting clear local time in UI.
    Uses isolate_ltr to enforce consistent Date-then-Time display across RTL contexts.
    """
    if not utc_str or not str(utc_str).strip():
        return default
    try:
        s = str(utc_str).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone()
        return isolate_ltr(local_dt.strftime("%Y-%m-%d %H:%M"))
    except Exception:
        cleaned = str(utc_str).replace("T", " ")[:16]
        return isolate_ltr(cleaned) if cleaned else default


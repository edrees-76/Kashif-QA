"""Dialog utilities and responsive helpers for KASHIF-QA GUI."""

from typing import Optional
from PyQt6.QtCore import Qt, QPoint, QObject, QEvent, QTimer, QRect
from PyQt6.QtWidgets import QDialog, QDateEdit, QApplication


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


def adjust_dialog_size_to_screen(
    dialog: QDialog,
    max_ratio: float = 0.90,
    preferred_width: Optional[int] = None,
    preferred_height: Optional[int] = None,
) -> None:
    """Adjust dialog size so it never exceeds max_ratio of the available screen geometry."""
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


"""Automatic idle screen lock for KASHIF-QA.

Locks the interface after a period of user inactivity and prompts for password verification.
"""

import sqlite3
from typing import Optional

from PyQt6.QtCore import QEvent, QObject, QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from kashif.gui.theme import COLORS, get_stylesheet
from kashif.services.audit_service import record_audit_event
from kashif.services.security import AuthenticationError, authenticate_user
from kashif.services.session import Session

DEFAULT_IDLE_MINUTES = 15
_ACTIVITY_EVENTS = {
    QEvent.Type.KeyPress,
    QEvent.Type.MouseButtonPress,
    QEvent.Type.MouseMove,
    QEvent.Type.Wheel,
    QEvent.Type.TouchBegin,
}


class UnlockDialog(QDialog):
    """Modal dialog asking locked user to enter password to resume session."""

    def __init__(self, conn: sqlite3.Connection, session: Session, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.session = session
        self.logged_out = False

        self.setWindowTitle("قفل الشاشة للخمول — منظومة كاشف")
        self.setMinimumWidth(400)
        self.setModal(True)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        icon_label = QLabel("🔒")
        icon_label.setStyleSheet("font-size: 32px;")
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        title = QLabel(f"تم قفل الشاشة تلقائياً لخمول المستخدم\n({self.session.full_name})")
        title.setProperty("class", "h2")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        form = QFormLayout()
        self.edit_password = QLineEdit()
        self.edit_password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("كلمة المرور:", self.edit_password)
        layout.addLayout(form)

        self.lbl_error = QLabel()
        self.lbl_error.setProperty("class", "error-text")
        layout.addWidget(self.lbl_error)

        # Buttons: Unlock vs Logout
        btn_box = QHBoxLayout()
        self.btn_unlock = QPushButton("استئناف العمل")
        self.btn_unlock.clicked.connect(self._handle_unlock)

        self.btn_logout = QPushButton("تسجيل الخروج")
        self.btn_logout.setProperty("class", "secondary")
        self.btn_logout.clicked.connect(self._handle_logout)

        btn_box.addWidget(self.btn_unlock)
        btn_box.addWidget(self.btn_logout)
        layout.addLayout(btn_box)

    def _handle_unlock(self) -> None:
        pw = self.edit_password.text().strip()
        if not pw:
            self.lbl_error.setText("يرجى إدخال كلمة المرور.")
            return

        try:
            authenticate_user(self.conn, self.session.username, pw)
            record_audit_event(
                conn=self.conn,
                action="UNLOCK",
                user_id=self.session.user_id,
                entity_type="USER",
                entity_id=self.session.user_id,
                new_value={"event": "SESSION_UNLOCKED", "username": self.session.username},
                commit=True,
            )
            self.accept()
        except AuthenticationError as e:
            self.edit_password.clear()
            self.lbl_error.setText(str(e))

    def _handle_logout(self) -> None:
        self.logged_out = True
        self.accept()

    def reject(self) -> None:
        # Closing window or pressing ESC does NOT bypass unlock screen
        pass


class IdleLock(QObject):
    """Watches application input events and locks after timeout."""

    def __init__(self, window, conn: sqlite3.Connection, minutes: int = DEFAULT_IDLE_MINUTES):
        super().__init__(window)
        self.window = window
        self.conn = conn
        self.minutes = minutes
        self.locked = False

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._on_timeout)

        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

        self.restart()

    def restart(self) -> None:
        """Reset or restart the inactivity timer."""
        if self.minutes > 0 and not self.locked:
            self.timer.start(self.minutes * 60 * 1000)
        else:
            self.timer.stop()

    def eventFilter(self, obj, ev):
        if not self.locked and ev.type() in _ACTIVITY_EVENTS and self.timer.isActive():
            self.timer.start()
        return False

    def _on_timeout(self) -> None:
        if not self.window or not hasattr(self.window, "session") or not self.window.session:
            return
        self.locked = True

        # Close all open secondary dialogs/windows without asking the user
        if hasattr(self.window, "close_secondary_windows"):
            self.window.close_secondary_windows()

        # Hide window content during lock to prevent unauthorized inspection of lab measurements
        central = self.window.centralWidget() if hasattr(self.window, "centralWidget") else None
        if central:
            central.setVisible(False)

        dlg = UnlockDialog(self.conn, self.window.session, parent=self.window)
        dlg.exec()

        if central:
            central.setVisible(True)

        self.locked = False
        if dlg.logged_out:
            if hasattr(self.window, "_execute_logout"):
                self.window._execute_logout()
            self.window.close()
        else:
            self.restart()



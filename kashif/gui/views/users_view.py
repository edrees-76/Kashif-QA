"""Users management view for KASHIF-QA administrators.

Provides user listing, account creation, activation/deactivation,
and password reset capabilities with full audit trail logging.
"""

import sqlite3
from typing import Optional, Tuple

from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from kashif.gui.bidi import format_mixed_text
from kashif.gui.theme import COLORS, get_stylesheet, status_color
from kashif.services.security import (
    MIN_PASSWORD_LENGTH,
    SecurityError,
    admin_reset_user_password,
    create_user,
    list_users,
    set_user_active_status,
)
from kashif.services.session import ROLES, Session


class CreateUserDialog(QDialog):
    """Dialog for creating a new user account."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("إضافة مستخدم جديد — منظومة كاشف")
        self.setMinimumWidth(420)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        form = QFormLayout()
        self.edit_user = QLineEdit()
        self.edit_name = QLineEdit()
        self.combo_role = QComboBox()
        for r in ROLES:
            self.combo_role.addItem(r, r)

        self.edit_pw = QLineEdit()
        self.edit_pw.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("اسم المستخدم:", self.edit_user)
        form.addRow("الاسم المعروض:", self.edit_name)
        form.addRow("الدور / الرتبة:", self.combo_role)
        form.addRow("كلمة المرور المؤقتة:", self.edit_pw)
        layout.addLayout(form)

        hint = QLabel(f"• الحد الأدنى: {MIN_PASSWORD_LENGTH} أحرف. سيُفرض تغييرها عند أول دخول.")
        hint.setProperty("class", "hint")
        layout.addWidget(hint)

        self.lbl_error = QLabel()
        self.lbl_error.setProperty("class", "error-text")
        layout.addWidget(self.lbl_error)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton("حفظ وإنشاء الحساب")
        btn_ok.clicked.connect(self._validate)
        btn_cancel = QPushButton("إلغاء")
        btn_cancel.setProperty("class", "secondary")
        btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _validate(self) -> None:
        if not self.edit_user.text().strip() or not self.edit_name.text().strip():
            self.lbl_error.setText("يرجى ملء الحقول المطلوبة.")
            return
        if len(self.edit_pw.text()) < MIN_PASSWORD_LENGTH:
            self.lbl_error.setText(f"كلمة المرور قصيرة جداً (الحد الأدنى {MIN_PASSWORD_LENGTH} أحرف).")
            return
        self.accept()

    def values(self):
        return (
            self.edit_user.text().strip(),
            self.edit_name.text().strip(),
            self.combo_role.currentData(),
            self.edit_pw.text()
        )


class ResetPasswordAdminDialog(QDialog):
    """Dialog for administrator resetting a user's password."""

    def __init__(self, username: str, parent=None):
        super().__init__(parent)
        self.username = username
        self.setWindowTitle(f"إعادة تعيين كلمة المرور — {username}")
        self.setMinimumWidth(380)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        layout.addWidget(QLabel(f"إعادة تعيين كلمة المرور لحساب: <b>{self.username}</b>"))
        form = QFormLayout()
        self.edit_pw = QLineEdit()
        self.edit_pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("كلمة المرور المؤقتة الجديدة:", self.edit_pw)
        layout.addLayout(form)

        self.lbl_error = QLabel()
        self.lbl_error.setProperty("class", "error-text")
        layout.addWidget(self.lbl_error)

        btn_row = QHBoxLayout()
        btn_ok = QPushButton("تأكيد التعيين")
        btn_ok.clicked.connect(self._validate)
        btn_cancel = QPushButton("إلغاء")
        btn_cancel.setProperty("class", "secondary")
        btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _validate(self) -> None:
        if len(self.edit_pw.text()) < MIN_PASSWORD_LENGTH:
            self.lbl_error.setText(f"الحد الأدنى لكلمة المرور {MIN_PASSWORD_LENGTH} أحرف.")
            return
        self.accept()

    def get_password(self) -> str:
        return self.edit_pw.text()


import os

def _show_msg(parent, level: str, title: str, text: str) -> None:
    """Show message box in UI, skipping modal execution during pytest runs."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        if level == "info":
            QMessageBox.information(parent, title, text)
        elif level == "warn":
            QMessageBox.warning(parent, title, text)
        elif level == "error":
            QMessageBox.critical(parent, title, text)


class UsersManagementDialog(QDialog):
    """Main users administration view for ADMIN role."""

    def __init__(self, conn: sqlite3.Connection, session: Session, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.session = session

        self.setWindowTitle("إدارة المستخدمين والصلاحيات — KASHIF-QA")
        self.resize(850, 520)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())

        self._init_ui()
        self.refresh_users()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        # Header bar
        header_row = QHBoxLayout()
        title = QLabel("سجل المستخدمين وإدارة الوصول")
        title.setProperty("class", "title")
        header_row.addWidget(title)
        header_row.addStretch()

        self.btn_add = QPushButton("+ إضافة مستخدم جديد")
        self.btn_add.clicked.connect(self._on_add_user)
        header_row.addWidget(self.btn_add)
        layout.addLayout(header_row)

        # Users table
        self.table = QTableWidget()
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "م", "اسم المستخدم", "الاسم المعروض", "الدور", "الحالة", "تغيير المرور؟", "آخر دخول"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        # Action Buttons
        actions_row = QHBoxLayout()
        self.btn_toggle_active = QPushButton("تفعيل / تعطيل الحساب")
        self.btn_toggle_active.setProperty("class", "secondary")
        self.btn_toggle_active.clicked.connect(self._on_toggle_active)

        self.btn_reset_pw = QPushButton("إعادة تعيين كلمة المرور")
        self.btn_reset_pw.setProperty("class", "secondary")
        self.btn_reset_pw.clicked.connect(self._on_reset_pw)

        actions_row.addWidget(self.btn_toggle_active)
        actions_row.addWidget(self.btn_reset_pw)
        actions_row.addStretch()

        btn_close = QPushButton("إغلاق")
        btn_close.setProperty("class", "secondary")
        btn_close.clicked.connect(self.accept)
        actions_row.addWidget(btn_close)

        layout.addLayout(actions_row)

    def refresh_users(self) -> None:
        users = list_users(self.conn, self.session.user_id)
        self.table.setRowCount(len(users))

        for row_idx, u in enumerate(users):
            self.table.setItem(row_idx, 0, QTableWidgetItem(str(u["id"])))
            self.table.setItem(row_idx, 1, QTableWidgetItem(u["username"]))
            self.table.setItem(row_idx, 2, QTableWidgetItem(u["full_name"]))
            self.table.setItem(row_idx, 3, QTableWidgetItem(u["role"]))

            status_str = "نشط" if u["is_active"] else "معطّل"
            status_item = QTableWidgetItem(status_str)
            status_item.setForeground(QColor(status_color(bool(u["is_active"]))))
            self.table.setItem(row_idx, 4, status_item)

            must_str = "نعم" if u["must_change_password"] else "لا"
            self.table.setItem(row_idx, 5, QTableWidgetItem(must_str))

            last_login = (u["last_login_at"] or "لم يدخل بعد")[:19].replace("T", " ")
            self.table.setItem(row_idx, 6, QTableWidgetItem(last_login))

    def _get_selected_user_id(self) -> Optional[Tuple[int, str, bool]]:
        current_row = self.table.currentRow()
        if current_row < 0:
            _show_msg(self, "warn", "تنبيه", "يرجى تحديد مستخدم من الجدول أولاً.")
            return None
        user_id = int(self.table.item(current_row, 0).text())
        username = self.table.item(current_row, 1).text()
        is_active = "نشط" in self.table.item(current_row, 4).text()
        return user_id, username, is_active

    def _on_add_user(self) -> None:
        dlg = CreateUserDialog(self)
        if dlg.exec():
            u, name, role, pw = dlg.values()
            try:
                create_user(
                    conn=self.conn,
                    operator_user_id=self.session.user_id,
                    username=u,
                    full_name=name,
                    role=role,
                    password=pw,
                    must_change_password=True,
                )
                _show_msg(self, "info", "نجاح", f"تم إنشاء المستخدم {u} بنجاح.")
                self.refresh_users()
            except Exception as e:
                _show_msg(self, "error", "خطأ", str(e))

    def _on_toggle_active(self) -> None:
        sel = self._get_selected_user_id()
        if not sel:
            return
        uid, uname, is_active = sel
        new_status = not is_active
        action_word = "تفعيل" if new_status else "تعطيل"

        try:
            set_user_active_status(self.conn, self.session.user_id, uid, new_status)
            _show_msg(self, "info", "نجاح", f"تم {action_word} حساب ({uname}) بنجاح.")
            self.refresh_users()
        except SecurityError as e:
            _show_msg(self, "warn", "حظر أمني", str(e))
        except Exception as e:
            _show_msg(self, "error", "خطأ", str(e))

    def _on_reset_pw(self) -> None:
        sel = self._get_selected_user_id()
        if not sel:
            return
        uid, uname, _ = sel
        dlg = ResetPasswordAdminDialog(uname, self)
        if dlg.exec():
            new_pw = dlg.get_password()
            try:
                admin_reset_user_password(self.conn, self.session.user_id, uid, new_pw)
                _show_msg(self, "info", "نجاح", f"تم إعادة تعيين كلمة المرور لحساب {uname} بنجاح.")
                self.refresh_users()
            except Exception as e:
                _show_msg(self, "error", "خطأ", str(e))


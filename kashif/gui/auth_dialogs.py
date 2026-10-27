"""Authentication and initial setup dialogs for KASHIF-QA.

Implements FirstAdminDialog, LoginDialog with persistent lockout,
MasterRecoveryKeyDialog, and ChangePasswordDialog.
"""

import os
import sqlite3
from typing import Optional, Tuple

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtGui import QGuiApplication, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from kashif.gui.bidi import format_mixed_text
from kashif.gui.theme import COLORS, get_stylesheet, logo_file
from kashif.services.security import (
    AuthenticationError,
    MIN_PASSWORD_LENGTH,
    authenticate_user,
    change_own_password,
    create_first_admin,
    list_active_usernames,
)
from kashif.services.session import Session


class FirstAdminDialog(QDialog):
    """Initial system setup dialog: Administrator chooses their own credentials."""

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.created_admin: Optional[sqlite3.Row] = None
        self.recovery_key: Optional[str] = None

        self.setWindowTitle("التهيئة الأولى — إنشاء حساب مدير النظام")
        self.setMinimumWidth(480)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # Header Info Card
        card = QFrame()
        card.setProperty("class", "card")
        c_layout = QVBoxLayout(card)
        c_layout.setSpacing(6)
        
        t_label = QLabel("مرحباً بك في منظومة «كاشف» (KASHIF-QA)")
        t_label.setProperty("class", "title")
        d_label = QLabel(
            "هذه هي البداية الأولى للمنظومة على هذا الجهاز المعملي.\n"
            "يرجى إنشاء حساب مدير النظام الأول وتحديد كلمة مرور قوية من اختيارك."
        )
        d_label.setProperty("class", "subtitle")
        d_label.setWordWrap(True)
        c_layout.addWidget(t_label)
        c_layout.addWidget(d_label)
        layout.addWidget(card)

        # Form
        form = QFormLayout()
        form.setSpacing(12)

        self.edit_user = QLineEdit("admin")
        self.edit_name = QLineEdit("مدير النظام")
        self.edit_pw1 = QLineEdit()
        self.edit_pw1.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_pw2 = QLineEdit()
        self.edit_pw2.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("اسم المستخدم (Username):", self.edit_user)
        form.addRow("الاسم المعروض (Display Name):", self.edit_name)
        form.addRow("كلمة المرور (Password):", self.edit_pw1)
        form.addRow("تأكيد كلمة المرور:", self.edit_pw2)
        layout.addLayout(form)

        # Clear any error message whenever user types
        self.edit_user.textChanged.connect(lambda: self.lbl_error.setText(""))
        self.edit_name.textChanged.connect(lambda: self.lbl_error.setText(""))
        self.edit_pw1.textChanged.connect(lambda: self.lbl_error.setText(""))
        self.edit_pw2.textChanged.connect(lambda: self.lbl_error.setText(""))

        hint = QLabel(f"• الحد الأدنى لكلمة المرور: {MIN_PASSWORD_LENGTH} أحرف.")
        hint.setProperty("class", "hint")
        layout.addWidget(hint)

        self.lbl_error = QLabel("")
        self.lbl_error.setProperty("class", "error-text")
        layout.addWidget(self.lbl_error)

        # Buttons
        self.btn_submit = QPushButton("إنشاء الحساب ومتابعة التهيئة")
        self.btn_submit.setAutoDefault(False)
        self.btn_submit.setDefault(False)
        self.btn_submit.clicked.connect(self._handle_submit)
        layout.addWidget(self.btn_submit)

    def _handle_submit(self) -> None:
        user = self.edit_user.text().strip()
        name = self.edit_name.text().strip()
        pw1 = self.edit_pw1.text()
        pw2 = self.edit_pw2.text()

        if not user or not name or not pw1 or not pw2:
            self.lbl_error.setText("يرجى ملء كافة الحقول المطلوبة.")
            return

        if len(pw1) < MIN_PASSWORD_LENGTH:
            self.lbl_error.setText(f"كلمة المرور قصيرة جداً (الحد الأدنى {MIN_PASSWORD_LENGTH} أحرف).")
            return


        if pw1 != pw2:
            self.lbl_error.setText("كلمتا المرور غير متطابقتين.")
            return

        try:
            admin_row, recovery_key = create_first_admin(
                conn=self.conn,
                username=user,
                full_name=name,
                password=pw1
            )
            self.created_admin = admin_row
            self.recovery_key = recovery_key
            self.accept()
        except Exception as e:
            self.lbl_error.setText(f"خطأ: {str(e)}")


class MasterRecoveryKeyDialog(QDialog):
    """Displays the Master Recovery Key once after initial admin creation."""

    def __init__(self, recovery_key: str, parent=None):
        super().__init__(parent)
        self.recovery_key = recovery_key

        self.setWindowTitle("مفتاح الاستعادة الرئيسي للطوارئ — KASHIF-QA")
        self.resize(600, 380)
        self.setModal(True)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        warn_card = QFrame()
        warn_card.setProperty("class", "warn-card")
        w_layout = QVBoxLayout(warn_card)
        w_title = QLabel("⚠️ مفتاح الاستعادة الرئيسي (Master Recovery Key)")
        w_title.setProperty("class", "warn-title")
        w_desc = QLabel(
            "تم إنشاء مفتاح الاستعادة الرقمي الخاص بهذا المعمل.\n"
            "هذا المفتاح يُعرض الآن فقط ويستخدم لاستعادة الوصول الإداري إذا فُقدت كلمة المرور.\n"
            "احفظ المفتاح في مكان آمن أو خزنة المعمل ولا تشاركه."
        )
        w_desc.setProperty("class", "hint")
        w_desc.setWordWrap(True)
        w_layout.addWidget(w_title)
        w_layout.addWidget(w_desc)
        layout.addWidget(warn_card)

        # Recovery key box
        key_row = QHBoxLayout()
        edit_key = QLineEdit(self.recovery_key)
        edit_key.setReadOnly(True)
        edit_key.setProperty("class", "key-field")
        self.btn_copy = QPushButton("نسخ")
        self.btn_copy.setMinimumWidth(96)
        self.btn_copy.clicked.connect(lambda: self._copy(self.btn_copy))
        key_row.addWidget(edit_key)
        key_row.addWidget(self.btn_copy)
        layout.addLayout(key_row)

        self.btn_confirm = QPushButton("حفظت المفتاح وأريد المتابعة")
        self.btn_confirm.setProperty("class", "btn-success")
        self.btn_confirm.clicked.connect(self.accept)
        layout.addWidget(self.btn_confirm)


    def _copy(self, btn: QPushButton) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(self.recovery_key)
        btn.setText("تم!")
        QTimer.singleShot(1500, lambda: btn.setText("نسخ"))

    def reject(self) -> None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            super().reject()
            return
        reply = QMessageBox.question(
            self,
            "تأكيد حفظ المفتاح",
            "هل تأكدت من حفظ مفتاح الاستعادة؟ لن يُعرض ثانية.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            super().reject()



class LoginDialog(QDialog):
    """User authentication dialog with persistent lockout and remember username."""

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.session: Optional[Session] = None
        self._settings = QSettings("KashifNuclear", "KASHIF-QA")
        self._is_closing: bool = False

        self.setWindowTitle("تسجيل الدخول — منظومة كاشف")
        self.setMinimumWidth(440)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # Header with Logo
        head_row = QHBoxLayout()
        logo_label = QLabel()
        logo_path = logo_file()
        if os.path.exists(logo_path):
            pix = QPixmap(logo_path).scaled(70, 70, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo_label.setPixmap(pix)
            self.setWindowIcon(QIcon(logo_path))
        head_row.addWidget(logo_label)

        title_box = QVBoxLayout()
        t1 = QLabel("منظومة «كاشف» (KASHIF-QA)")
        t1.setProperty("class", "h1")
        t2 = QLabel("ضبط جودة الكواشف الإشعاعية ومطيافية أشعة جاما")
        t2.setProperty("class", "hint")
        title_box.addWidget(t1)
        title_box.addWidget(t2)
        head_row.addLayout(title_box)
        head_row.addStretch()
        layout.addLayout(head_row)

        form = QFormLayout()
        form.setSpacing(12)

        # Username selection (Active users list)
        self.combo_user = QComboBox()
        active_users = list_active_usernames(self.conn)
        for username, full_name in active_users:
            label = f"{full_name} ({username})" if full_name != username else username
            self.combo_user.addItem(label, username)

        self.edit_password = QLineEdit()
        self.edit_password.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("اسم المستخدم:", self.combo_user)
        form.addRow("كلمة المرور:", self.edit_password)
        layout.addLayout(form)

        # Remember username checkbox
        self.chk_remember = QCheckBox("تذكّر اسم المستخدم")
        saved_user = self._settings.value("login/remembered_username", "")
        if saved_user:
            idx = self.combo_user.findData(saved_user)
            if idx >= 0:
                self.combo_user.setCurrentIndex(idx)
                self.chk_remember.setChecked(True)
        layout.addWidget(self.chk_remember)

        # Error label
        self.lbl_error = QLabel()
        self.lbl_error.setProperty("class", "error-text")
        self.lbl_error.setWordWrap(True)
        layout.addWidget(self.lbl_error)

        # Buttons
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("تسجيل الدخول")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("إلغاء")
        bb.accepted.connect(self._handle_login)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

        # Focus
        if self.chk_remember.isChecked() and self.combo_user.currentIndex() >= 0:
            self.edit_password.setFocus()
        else:
            self.combo_user.setFocus()

    def _handle_login(self) -> None:
        username = self.combo_user.currentData() or self.combo_user.currentText().strip()
        password = self.edit_password.text()

        if not username or not password:
            self.lbl_error.setText("يرجى إدخال اسم المستخدم وكلمة المرور.")
            return

        try:
            user_row = authenticate_user(self.conn, username, password)
            self.session = Session.from_row(user_row)

            # Remember user setting
            if self.chk_remember.isChecked():
                self._settings.setValue("login/remembered_username", username)
            else:
                self._settings.remove("login/remembered_username")

            self.accept()
        except AuthenticationError as e:
            self.edit_password.clear()
            self.lbl_error.setText(str(e))
        except Exception as e:
            self.lbl_error.setText(f"خطأ غير متوقع: {str(e)}")

    def accept(self) -> None:
        """Mark as closing and accept dialog upon successful login."""
        self._is_closing = True
        super().accept()

    def closeEvent(self, event) -> None:
        """Prompt confirmation when user clicks X on Login screen to terminate application."""
        if self._is_closing or self.result() == QDialog.DialogCode.Accepted:
            event.accept()
            return

        reply = QMessageBox.question(
            self,
            "تأكيد الخروج",
            "هل تريد إغلاق المنظومة وإنهاء البرنامج؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._is_closing = True
            event.accept()
        else:
            event.ignore()

    def reject(self) -> None:
        """Handle Cancel or ESC on Login screen with confirmation before rejecting."""
        if self._is_closing:
            super().reject()
            return

        reply = QMessageBox.question(
            self,
            "تأكيد الخروج",
            "هل تريد إغلاق المنظومة وإنهاء البرنامج؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._is_closing = True
            super().reject()



class ChangePasswordDialog(QDialog):

    """Forced password change dialog (used when must_change_password=True)."""

    def __init__(self, conn: sqlite3.Connection, user_id: int, username: str, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.user_id = user_id
        self.username = username
        self.password_changed = False

        self.setWindowTitle("تغيير كلمة المرور — منظومة كاشف")
        self.setMinimumWidth(440)
        self.setModal(True)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)

        title = QLabel(f"تغيير كلمة المرور لحساب ({self.username})")
        title.setProperty("class", "title")
        desc = QLabel("يجب تغيير كلمة المرور عند تسجيل الدخول الأول أو بعد إعادة التعيين.")
        desc.setProperty("class", "subtitle")
        layout.addWidget(title)
        layout.addWidget(desc)

        form = QFormLayout()
        self.edit_old = QLineEdit()
        self.edit_old.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_new1 = QLineEdit()
        self.edit_new1.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_new2 = QLineEdit()
        self.edit_new2.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("كلمة المرور الحالية:", self.edit_old)
        form.addRow("كلمة المرور الجديدة:", self.edit_new1)
        form.addRow("تأكيد كلمة المرور الجديدة:", self.edit_new2)
        layout.addLayout(form)

        hint = QLabel(f"• الحد الأدنى: {MIN_PASSWORD_LENGTH} أحرف.")
        hint.setProperty("class", "hint")
        layout.addWidget(hint)

        self.lbl_error = QLabel()
        self.lbl_error.setProperty("class", "error-text")
        layout.addWidget(self.lbl_error)

        self.btn_submit = QPushButton("حفظ كلمة المرور الجديدة")
        self.btn_submit.clicked.connect(self._handle_change)
        layout.addWidget(self.btn_submit)

    def _handle_change(self) -> None:
        old_pw = self.edit_old.text()
        new1 = self.edit_new1.text()
        new2 = self.edit_new2.text()

        if len(new1) < MIN_PASSWORD_LENGTH:
            self.lbl_error.setText(f"كلمة المرور الجديدة قصيرة جداً (الحد الأدنى {MIN_PASSWORD_LENGTH} أحرف).")
            return
        if new1 != new2:
            self.lbl_error.setText("كلمتا المرور الجديدتان غير متطابقتين.")
            return

        try:
            change_own_password(self.conn, self.user_id, old_pw, new1)
            self.password_changed = True
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                QMessageBox.information(self, "نجاح", "تم تغيير كلمة المرور بنجاح.")
            self.accept()
        except Exception as e:
            self.lbl_error.setText(str(e))


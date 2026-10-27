"""Initial administrator credentials dialog for KASHIF-QA.

Displays the one-time generated admin password and Master Recovery Key
in a secure modal dialog with copy buttons. Never written to disk or logs.
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from kashif.gui.theme import LAB_DARK_QSS


class InitialCredentialsDialog(QDialog):
    """Modal dialog displayed ONLY ONCE upon initial system bootstrapping."""

    def __init__(self, admin_password: str, recovery_key: str, parent=None):
        super().__init__(parent)
        self.admin_password = admin_password
        self.recovery_key = recovery_key
        self.confirmed = False

        self.setWindowTitle("إعداد أمني أولي — بيانات مدير النظام ومفتاح الاستعادة")
        self.resize(660, 480)
        self.setMinimumSize(560, 420)
        self.setModal(True)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(LAB_DARK_QSS)

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(16)

        # Header warning box
        warn_box = QFrame()
        warn_box.setStyleSheet(
            "background-color: rgba(239, 68, 68, 0.15); border: 1px solid #EF4444; border-radius: 8px; padding: 12px;"
        )
        warn_layout = QVBoxLayout(warn_box)
        warn_title = QLabel("🚨 تنبيه أمني إلزامي — يُعرض لمرة واحدة فقط")
        warn_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #EF4444;")
        warn_desc = QLabel(
            "تم إنشاء حساب مدير النظام الافتراضي (admin) ومفتاح الاستعادة الرئيسي للطوارئ (Master Recovery Key).\n"
            "احفظ هذه البيانات في مكان آمن فوراً؛ لن تظهر هذه النافذة أو هذه القيم مرة ثانية مطلقاً."
        )
        warn_desc.setStyleSheet("font-size: 13px; color: #F8FAFC; line-height: 1.5;")
        warn_desc.setWordWrap(True)
        warn_layout.addWidget(warn_title)
        warn_layout.addWidget(warn_desc)
        layout.addWidget(warn_box)

        # Credentials card
        card = QFrame()
        card.setProperty("class", "card")
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(12)

        # 1. Username
        card_layout.addWidget(QLabel("اسم المستخدم الإداري (Username):"))
        u_row = QHBoxLayout()
        u_edit = QLineEdit("admin")
        u_edit.setReadOnly(True)
        u_edit.setStyleSheet(
            "background-color: #070D1F; color: #00F2FE; font-weight: bold; padding: 8px; border: 1px solid #334155; border-radius: 4px;"
        )
        btn_copy_u = QPushButton("نسخ")
        btn_copy_u.setFixedWidth(70)
        btn_copy_u.clicked.connect(lambda: self._copy_to_clipboard("admin", btn_copy_u))
        u_row.addWidget(u_edit)
        u_row.addWidget(btn_copy_u)
        card_layout.addLayout(u_row)

        # 2. Initial Password
        card_layout.addWidget(QLabel("كلمة المرور المؤقتة (Temporary Password):"))
        p_row = QHBoxLayout()
        p_edit = QLineEdit(self.admin_password)
        p_edit.setReadOnly(True)
        p_edit.setStyleSheet(
            "background-color: #070D1F; color: #F8FAFC; font-family: monospace; font-size: 13px; padding: 8px; border: 1px solid #334155; border-radius: 4px;"
        )
        btn_copy_p = QPushButton("نسخ")
        btn_copy_p.setFixedWidth(70)
        btn_copy_p.clicked.connect(lambda: self._copy_to_clipboard(self.admin_password, btn_copy_p))
        p_row.addWidget(p_edit)
        p_row.addWidget(btn_copy_p)
        card_layout.addLayout(p_row)

        # 3. Master Recovery Key
        card_layout.addWidget(QLabel("مفتاح الاستعادة الرئيسي للطوارئ (Master Recovery Key):"))
        r_row = QHBoxLayout()
        r_edit = QLineEdit(self.recovery_key)
        r_edit.setReadOnly(True)
        r_edit.setStyleSheet(
            "background-color: #070D1F; color: #F59E0B; font-family: monospace; font-size: 12px; padding: 8px; border: 1px solid #334155; border-radius: 4px;"
        )
        btn_copy_r = QPushButton("نسخ")
        btn_copy_r.setFixedWidth(70)
        btn_copy_r.clicked.connect(lambda: self._copy_to_clipboard(self.recovery_key, btn_copy_r))
        r_row.addWidget(r_edit)
        r_row.addWidget(btn_copy_r)
        card_layout.addLayout(r_row)

        layout.addWidget(card)

        # Confirmation Button
        btn_confirm = QPushButton("✓ حفظت المفتاح وأؤكد المتابعة")
        btn_confirm.setStyleSheet(
            "background: #10B981; color: #0B132B; font-size: 14px; font-weight: bold; padding: 12px; border-radius: 6px;"
        )
        btn_confirm.clicked.connect(self._on_confirm)
        layout.addWidget(btn_confirm)

    def _copy_to_clipboard(self, text: str, btn: QPushButton) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
        old_text = btn.text()
        btn.setText("تم!")
        btn.setStyleSheet("background-color: #10B981; color: #0B132B;")
        QTimer.singleShot(1500, lambda: self._reset_btn(btn, old_text))

    def _reset_btn(self, btn: QPushButton, text: str) -> None:
        btn.setText(text)
        btn.setStyleSheet("")

    def _on_confirm(self) -> None:
        self.confirmed = True
        self.accept()

    def reject(self) -> None:
        # Require confirmation before closing
        reply = QMessageBox.question(
            self,
            "تأكيد الحفظ",
            "هل تأكدت من حفظ كلمة المرور ومفتاح الاستعادة؟ لن تتمكن من رؤيتهما مرة ثانية.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            super().reject()

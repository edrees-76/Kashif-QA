"""Main application window for KASHIF-QA.

Displays institutional seal logo, system status badges, version info,
active session details, and Role-Based Access Control actions.
Supports dynamic switching between Light (default) and Dark themes.
"""

import os
import sqlite3
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from kashif import __version__
from kashif.gui.bidi import format_mixed_text
from kashif.gui.idle_lock import IdleLock
from kashif.gui.theme import COLORS, get_stylesheet, logo_file, set_mode
from kashif.gui.views.users_view import UsersManagementDialog
from kashif.services.audit_service import record_audit_event, verify_audit_chain
from kashif.services.session import Session


class MainWindow(QMainWindow):
    """KASHIF-QA primary desktop window with session and role awareness."""

    def __init__(
        self,
        db_conn: Optional[sqlite3.Connection] = None,
        session: Optional[Session] = None,
        on_logout: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.db_conn = db_conn
        self.session = session
        self.on_logout_callback = on_logout
        self.current_theme = "light"
        self.idle_lock: Optional[IdleLock] = None
        self._is_logging_out: bool = False

        self.setWindowTitle(f"كاشف — منظومة ضبط الجودة واعتماد الكواشف الإشعاعية (KASHIF-QA) v{__version__}")
        self.resize(1200, 800)
        self.setMinimumSize(980, 680)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        self._init_ui()
        self._check_system_health()

        # Attach Idle Lock if DB and session are present
        if self.db_conn and self.session:
            self.idle_lock = IdleLock(window=self, conn=self.db_conn, minutes=15)

    def _init_ui(self) -> None:
        """Initialize UI components and layout."""
        set_mode(self.current_theme)
        self.setStyleSheet(get_stylesheet(self.current_theme))

        # Central widget with scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.setCentralWidget(scroll)

        container = QWidget()
        scroll.setWidget(container)

        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(32, 20, 32, 24)
        main_layout.setSpacing(18)

        # 1. Top Action & Navigation Bar
        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)

        # User indicator pill
        user_name = self.session.full_name if self.session else "غير مسجل"
        user_role = self.session.role if self.session else "—"
        self.lbl_user_badge = QLabel(f"👤 {user_name} | الرتبة: {user_role}")
        self.lbl_user_badge.setProperty("class", "user-badge")
        top_bar.addWidget(self.lbl_user_badge)

        top_bar.addStretch()

        # Users Management button (ADMIN only)
        self.btn_users_mgmt = QPushButton("👥 إدارة المستخدمين")
        self.btn_users_mgmt.setProperty("class", "secondary")
        if self.session and self.session.can("manage_users"):
            self.btn_users_mgmt.setVisible(True)
            self.btn_users_mgmt.clicked.connect(self._open_users_management)
        else:
            self.btn_users_mgmt.setVisible(False)
        top_bar.addWidget(self.btn_users_mgmt)

        # Theme toggle button
        self.btn_theme = QPushButton("🌙 الوضع الداكن")
        self.btn_theme.setProperty("class", "secondary")
        self.btn_theme.clicked.connect(self._toggle_theme)
        top_bar.addWidget(self.btn_theme)

        # Logout button
        self.btn_logout = QPushButton("🚪 تسجيل الخروج")
        self.btn_logout.setProperty("class", "danger")
        self.btn_logout.clicked.connect(self.handle_logout)
        top_bar.addWidget(self.btn_logout)

        main_layout.addLayout(top_bar)

        # 2. Header with Official Logo & Titles
        header_card = QFrame()
        header_card.setProperty("class", "card")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(20, 18, 20, 18)
        header_layout.setSpacing(24)

        # Logo
        logo_label = QLabel()
        self.logo_label = logo_label
        logo_path = os.path.join(os.path.dirname(__file__), "assets", "kashif_logo.png")
        if os.path.exists(logo_path):
            self._apply_logo()
            self.setWindowIcon(QIcon(logo_path))
        else:
            logo_label.setText("⚛️")
            logo_label.setStyleSheet("font-size: 60px;")

        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(logo_label)

        # Title text block with BiDi isolation
        title_box = QVBoxLayout()
        title_box.setSpacing(6)

        app_title = QLabel(format_mixed_text("منظومة «كاشف» الرقمية لضبط جودة واعتماد الكواشف الإشعاعية"))
        app_title.setProperty("class", "title")

        en_title = QLabel("KASHIF-QA: Nuclear Radiation Detector Quality Assurance & Spectroscopy System")
        en_title.setProperty("class", "subtitle")

        std_ref = QLabel(format_mixed_text(
            "المرجعية القياسية الدولية: IAEA Procedure Nº MRNI-507 (HPGe) / MRNI-504 (NaI) & TECDOC-1599"
        ))
        std_ref.setProperty("class", "hint")

        badges_row = QHBoxLayout()
        badges_row.setSpacing(10)

        v_badge = QLabel(format_mixed_text(f"الإصدار: v{__version__} (Phase 1: Entry & Core)"))
        v_badge.setProperty("class", "badge-success")

        gate_badge = QLabel("بوابة الاعتماد: Gate G2 مُجتازة بنجاح ✓")
        gate_badge.setProperty("class", "badge-success")

        badges_row.addWidget(v_badge)
        badges_row.addWidget(gate_badge)
        badges_row.addStretch()

        title_box.addWidget(app_title)
        title_box.addWidget(en_title)
        title_box.addWidget(std_ref)
        title_box.addLayout(badges_row)

        header_layout.addLayout(title_box, stretch=1)
        main_layout.addWidget(header_card)

        # 3. System Status Overview (Cards Grid)
        grid_layout = QHBoxLayout()
        grid_layout.setSpacing(16)

        card_db = self._create_status_card(
            title="قاعدة بيانات SQLite (WAL)",
            desc="تخزين محلي معزول بنمط الكتابة المسبقة لمنع تلف القفل وتحقيق أعلى استقرار.",
            status_text="🟢 نمط WAL نشط",
            status_style="status-success"
        )
        grid_layout.addWidget(card_db)

        card_audit = self._create_status_card(
            title="سجل التدقيق الأمني التشفيري",
            desc="سلسلة هاش SHA-256 مع تريجرز تمنع التعديل والحذف وتأمين معاملات BEGIN IMMEDIATE.",
            status_text="🔒 سلسلة الهاش سليمة ومحمية",
            status_style="status-info"
        )
        grid_layout.addWidget(card_audit)

        card_sec = self._create_status_card(
            title="التوثيق وقفل الحساب التلقائي",
            desc="تجزئة Argon2id/PBKDF2 مع قفل دائم بعد 5 محاولات فاشلة ومفتاح استعادة رئيسي.",
            status_text="🛡️ الحماية النشطة مفعلة",
            status_style="status-neutral"
        )
        grid_layout.addWidget(card_sec)

        main_layout.addLayout(grid_layout)

        # 4. Phase 1 Roadmap & Scope Box
        roadmap_card = QFrame()
        roadmap_card.setProperty("class", "card")
        road_layout = QVBoxLayout(roadmap_card)
        road_layout.setSpacing(10)

        road_title = QLabel(format_mixed_text("مسار بناء Phase 1 (الدخول والهوية والنواة الفيزيائية والسجلات):"))
        road_title.setProperty("class", "h2")
        road_layout.addWidget(road_title)

        road_desc = QLabel(format_mixed_text(
            "• الدورة 1 (المنجزة): واجهة الدخول، الجلسات والصلاحيات (Session RBAC)، قفل الخمول، تطبيق الهوية البصرية، وتحقق الخط العربي.\n"
            "• الدورة 2 (التالية): نماذج Pydantic، الحسابات الفيزيائية لمعايير IAEA، حاسبة الاضمحلال، محرك قواعد القرار، وسجلات الكواشف والمصادر والأجهزة."
        ))
        road_desc.setProperty("class", "body")
        road_layout.addWidget(road_desc)

        main_layout.addWidget(roadmap_card)
        main_layout.addStretch()

        # 5. Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.lbl_db_status = QLabel("🟢 قاعدة البيانات: متصلة بنجاح")
        self.lbl_audit_status = QLabel("🔒 سجل التدقيق: سليم ومحمي")
        self.lbl_status_user = QLabel(f"👤 {user_name} ({user_role})")

        self.status_bar.addWidget(self.lbl_db_status)
        self.status_bar.addWidget(self.lbl_audit_status)
        self.status_bar.addPermanentWidget(self.lbl_status_user)

    def _apply_logo(self) -> None:
        """Show the logo variant matching the active theme."""
        path = logo_file(self.current_theme)
        if os.path.exists(path):
            self.logo_label.setPixmap(QPixmap(path).scaled(
                125, 125, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _create_status_card(self, title: str, desc: str, status_text: str, status_style: str) -> QFrame:
        """Create a stylized information card widget."""
        card = QFrame()
        card.setProperty("class", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        t_label = QLabel(format_mixed_text(title))
        t_label.setProperty("class", "h2")

        d_label = QLabel(format_mixed_text(desc))
        d_label.setProperty("class", "hint")
        d_label.setWordWrap(True)

        s_label = QLabel(status_text)
        s_label.setProperty("class", status_style)

        layout.addWidget(t_label)
        layout.addWidget(d_label)
        layout.addStretch()
        layout.addWidget(s_label)
        return card

    def _toggle_theme(self) -> None:
        """Switch between Light (default) and Dark theme modes."""
        if self.current_theme == "light":
            self.current_theme = "dark"
            self.btn_theme.setText("☀️ الوضع الفاتح")
        else:
            self.current_theme = "light"
            self.btn_theme.setText("🌙 الوضع الداكن")

        set_mode(self.current_theme)
        self.setStyleSheet(get_stylesheet(self.current_theme))
        self._apply_logo()

    def _open_users_management(self) -> None:
        """Open the administrative users management dialog."""
        if not self.db_conn or not self.session:
            return
        dlg = UsersManagementDialog(conn=self.db_conn, session=self.session, parent=self)
        dlg.exec()

    def close_secondary_windows(self) -> None:
        """Close all open secondary dialogs and child windows without prompting for save.
        
        Traverses top-level widgets in reverse order to close deepest child dialogs first.
        """
        app = QApplication.instance()
        if not app:
            return
        widgets = app.topLevelWidgets()
        for w in reversed(widgets):
            if w is self or not w.isVisible():
                continue
            try:
                w.close()
            except Exception:
                pass

    def closeEvent(self, event) -> None:
        """Prompt confirmation when user clicks X or when close() is invoked."""
        if self._is_logging_out:
            event.accept()
            return

        reply = QMessageBox.question(
            self,
            "تأكيد الخروج",
            "هل تريد الخروج من المنظومة؟",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            event.ignore()
            return

        self._execute_logout()
        event.accept()

    def handle_logout(self) -> None:
        """Triggered by logout button. Invokes window close which handles confirmation."""
        self.close()

    def _execute_logout(self) -> None:
        """Execute orderly logout: stop idle timer, close secondary windows, log audit, clear session."""
        if self._is_logging_out:
            return
        self._is_logging_out = True

        # 1. Stop idle lock timer
        if hasattr(self, "idle_lock") and self.idle_lock and hasattr(self.idle_lock, "timer"):
            self.idle_lock.timer.stop()

        # 2. Close all secondary windows without save prompt
        self.close_secondary_windows()

        # 3. Record LOGOUT audit event if session is present
        if self.db_conn and self.session:
            try:
                record_audit_event(
                    conn=self.db_conn,
                    action="LOGOUT",
                    user_id=self.session.user_id,
                    entity_type="USER",
                    entity_id=self.session.user_id,
                    new_value={"event": "USER_LOGOUT", "username": self.session.username},
                    commit=True,
                )
            except Exception:
                pass

        # 4. Clear session from memory
        self.session = None

        # 5. Trigger logout callback (returns to login screen in run.py)
        if self.on_logout_callback:
            self.on_logout_callback()


    def _check_system_health(self) -> None:
        """Verify DB connectivity and audit chain health on launch."""
        if not self.db_conn:
            self.lbl_db_status.setText("🔴 قاعدة البيانات: غير متصلة")
            self.lbl_audit_status.setText("⚠️ حالة السجل: مجهولة")
            return

        try:
            cursor = self.db_conn.cursor()
            cursor.execute("PRAGMA journal_mode;")
            mode_row = cursor.fetchone()
            mode = mode_row[0].upper() if mode_row else "UNKNOWN"
            cursor.close()

            self.lbl_db_status.setText(f"🟢 قاعدة البيانات: متصلة ({mode})")

            is_valid, _ = verify_audit_chain(self.db_conn)
            if is_valid:
                self.lbl_audit_status.setText("🔒 سجل التدقيق: سليم ومحمي")
            else:
                self.lbl_audit_status.setText("⚠️ تنبيه: تم رصد خلل في سلسلة الهاش")
        except Exception as e:
            self.lbl_db_status.setText(f"🔴 خطأ في قاعدة البيانات: {str(e)[:30]}")

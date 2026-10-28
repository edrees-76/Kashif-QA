"""Main application window for KASHIF-QA.

Implements Decision 12:
- Right-aligned collapsible sidebar navigation built from a single configuration list
- Role-based section visibility (session.can())
- Dashboard with real-time summary cards (detectors count, instruments count,
  expired calibration instruments via R-INST-01, sources needing attention via R-SRC-01)
  Clicking summary cards opens the respective section
- Global quick search across detectors, instruments, and sources
- Clean top bar (Dark mode toggle, User badge, Logout)
- Orderly logout and idle lock closing all secondary windows without save prompt
"""

from __future__ import annotations

from datetime import date, datetime
import logging
import os
import sqlite3
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kashif import __version__
from kashif.core.decay import decay_activity
from kashif.core.rules import eval_r_inst_01_calibration, eval_r_src_01_source_activity
from kashif.db.repositories import (
    DetectorRepository,
    InstrumentRepository,
    SourceRepository,
    quick_search,
)
from kashif.gui.bidi import format_mixed_text
from kashif.gui.idle_lock import IdleLock
from kashif.gui.navigation import SidebarNavigation
from kashif.gui.theme import COLORS, get_stylesheet, logo_file, set_mode
from kashif.gui.views.detectors_view import DetectorDetailDialog, DetectorsView
from kashif.gui.views.instruments_view import InstrumentDetailDialog, InstrumentsView
from kashif.gui.views.sources_view import SourceDetailDialog, SourcesView
from kashif.gui.views.users_view import UsersManagementDialog
from kashif.services.audit_service import record_audit_event, verify_audit_chain
from kashif.services.session import Session

logger = logging.getLogger(__name__)


def _show_msg(parent: QWidget, level: str, title: str, text: str) -> None:
    """Show message box in UI."""
    if level == "info":
        QMessageBox.information(parent, title, text)
    elif level == "warn":
        QMessageBox.warning(parent, title, text)
    elif level == "error":
        QMessageBox.critical(parent, title, text)


class QuickSearchResultsDialog(QDialog):
    """Dialog displaying multiple search results from global quick search."""

    def __init__(self, results: List[Dict[str, Any]], conn: sqlite3.Connection, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.results = results
        self.conn = conn
        self.setWindowTitle(f"نتائج البحث السريع ({len(results)} نتائج)")
        self.resize(650, 360)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["النوع", "العنوان", "الوصف والبيانات", "المنشأة"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._open_selected)

        self.table.setRowCount(len(self.results))
        for row, r in enumerate(self.results):
            type_label = "كاشف" if r["entity_type"] == "detector" else ("جهاز فحص" if r["entity_type"] == "instrument" else "مصدر مشع")
            self.table.setItem(row, 0, QTableWidgetItem(type_label))
            self.table.setItem(row, 1, QTableWidgetItem(r["title"]))
            self.table.setItem(row, 2, QTableWidgetItem(r["subtitle"]))
            self.table.setItem(row, 3, QTableWidgetItem(r["facility"]))

        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.btn_open = QPushButton("فتح بطاقة السجل")
        self.btn_open.clicked.connect(self._open_selected)
        btn_close = QPushButton("إغلاق")
        btn_close.setProperty("class", "secondary")
        btn_close.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_open)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _open_selected(self) -> None:
        selected = self.table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        item = self.results[row]
        self._open_entity(item)
        self.accept()

    def _open_entity(self, item: Dict[str, Any]) -> None:
        etype = item["entity_type"]
        eid = item["id"]
        if etype == "detector":
            repo = DetectorRepository(self.conn)
            data = repo.get_by_id(eid)
            if data:
                dlg = DetectorDetailDialog(data, parent=self.parentWidget())
                dlg.exec()
        elif etype == "instrument":
            repo = InstrumentRepository(self.conn)
            data = repo.get_by_id(eid)
            if data:
                dlg = InstrumentDetailDialog(data, parent=self.parentWidget())
                dlg.exec()
        elif etype == "source":
            repo = SourceRepository(self.conn)
            data = repo.get_by_id(eid)
            if data:
                dlg = SourceDetailDialog(data, parent=self.parentWidget())
                dlg.exec()


class MainWindow(QMainWindow):
    """KASHIF-QA primary desktop window with navigation and role awareness."""

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
        self.resize(1280, 840)
        self.setMinimumSize(1020, 700)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)

        self._init_ui()
        self._check_system_health()
        self.refresh_dashboard()

        # Attach Idle Lock if DB and session are present
        if self.db_conn and self.session:
            self.idle_lock = IdleLock(window=self, conn=self.db_conn, minutes=15)

    def _init_ui(self) -> None:
        """Initialize UI components and navigation layout."""
        set_mode(self.current_theme)
        self.setStyleSheet(get_stylesheet(self.current_theme))

        # Root central container
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 1. Right Sidebar Navigation
        self.sidebar = SidebarNavigation(session=self.session, parent=self)
        self.sidebar.section_selected.connect(self._on_navigation_changed)
        root_layout.addWidget(self.sidebar)

        # 2. Main Content Area (Left/Center)
        content_container = QWidget()
        content_layout = QVBoxLayout(content_container)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(14)

        # Top Bar: User info, Global Search, Theme, Logout
        top_bar = QHBoxLayout()
        top_bar.setSpacing(12)

        # User indicator pill
        user_name = self.session.full_name if self.session else "غير مسجل"
        user_role = self.session.role if self.session else "—"
        self.lbl_user_badge = QLabel(f"👤 {user_name} | {user_role}")
        self.lbl_user_badge.setProperty("class", "user-badge")
        top_bar.addWidget(self.lbl_user_badge)

        # Global Quick Search
        self.edit_quick_search = QLineEdit()
        self.edit_quick_search.setProperty("class", "search-box")
        self.edit_quick_search.setPlaceholderText("🔍 بحث سريع عن رقم تسلسلي، كاشف، جهاز، مصدر...")
        self.edit_quick_search.returnPressed.connect(self.handle_quick_search)
        top_bar.addWidget(self.edit_quick_search, stretch=2)
        top_bar.addStretch()

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

        content_layout.addLayout(top_bar)

        # Stacked Pages Widget
        self.stack = QStackedWidget()

        # Page 0: Dashboard
        self.dashboard_widget = self._create_dashboard_page()
        self.stack.addWidget(self.dashboard_widget)

        # Page 1: Detectors View
        if self.db_conn and self.session:
            self.detectors_view = DetectorsView(conn=self.db_conn, session=self.session, parent=self)
            self.stack.addWidget(self.detectors_view)

            # Page 2: Instruments View
            self.instruments_view = InstrumentsView(conn=self.db_conn, session=self.session, parent=self)
            self.stack.addWidget(self.instruments_view)

            # Page 3: Sources View
            self.sources_view = SourcesView(conn=self.db_conn, session=self.session, parent=self)
            self.stack.addWidget(self.sources_view)
        else:
            self.detectors_view = QWidget()
            self.instruments_view = QWidget()
            self.sources_view = QWidget()
            self.stack.addWidget(self.detectors_view)
            self.stack.addWidget(self.instruments_view)
            self.stack.addWidget(self.sources_view)

        content_layout.addWidget(self.stack, stretch=1)
        root_layout.addWidget(content_container, stretch=1)

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.lbl_db_status = QLabel("🟢 قاعدة البيانات: متصلة بنجاح")
        self.lbl_audit_status = QLabel("🔒 سجل التدقيق: سليم ومحمي")
        self.lbl_status_user = QLabel(f"👤 {user_name} ({user_role})")

        self.status_bar.addWidget(self.lbl_db_status)
        self.status_bar.addWidget(self.lbl_audit_status)
        self.status_bar.addPermanentWidget(self.lbl_status_user)

    def _create_dashboard_page(self) -> QWidget:
        """Create scrollable Dashboard with summary cards and institutional seal."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        scroll.setWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 16)
        layout.setSpacing(16)

        # 1. Header with Official Logo & Titles
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

        # Title text block
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

        v_badge = QLabel(format_mixed_text(f"الإصدار: v{__version__} (Phase 1: Registries & Repos)"))
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
        layout.addWidget(header_card)

        # 2. Interactive Summary Cards Row (Decision 12 & TASK-1.7)
        summary_row = QHBoxLayout()
        summary_row.setSpacing(14)

        # Card 1: Detectors count
        self.card_detectors = self._create_summary_card(
            title="الكواشف الإشعاعية",
            count_text="0",
            sub_text="كواشف HPGe و NaI النشطة",
            on_click=lambda: self.navigate_to_section("detectors"),
        )
        summary_row.addWidget(self.card_detectors)

        # Card 2: Instruments count
        self.card_instruments = self._create_summary_card(
            title="أجهزة الفحص والمعايرة",
            count_text="0",
            sub_text="مجهزات الجهد والمضخمات وMCA",
            on_click=lambda: self.navigate_to_section("instruments"),
        )
        summary_row.addWidget(self.card_instruments)

        # Card 3: Expired Instruments (R-INST-01)
        self.card_expired = self._create_summary_card(
            title="أجهزة منتهية المعايرة",
            count_text="0",
            sub_text="R-INST-01: تتطلب تجديداً أو تبريراً",
            on_click=lambda: self.navigate_to_section("instruments"),
            is_warning=True,
        )
        summary_row.addWidget(self.card_expired)

        # Card 4: Sources attention (R-SRC-01)
        self.card_sources = self._create_summary_card(
            title="مصادر تحتاج انتباه",
            count_text="0",
            sub_text="R-SRC-01: فعالية خارج النطاق الموصى به",
            on_click=lambda: self.navigate_to_section("sources"),
            is_warning=True,
        )
        summary_row.addWidget(self.card_sources)

        layout.addLayout(summary_row)

        # 3. System Status Overview Cards
        grid_layout = QHBoxLayout()
        grid_layout.setSpacing(14)

        card_db = self._create_status_card(
            title="قاعدة بيانات SQLite (WAL)",
            desc="تخزين محلي معزول بنمط الكتابة المسبقة مع Migration 003 للسجلات الفنية وقيود التفرد المركبة.",
            status_text="🟢 نمط WAL نشط",
            status_style="status-success",
        )
        grid_layout.addWidget(card_db)

        card_audit = self._create_status_card(
            title="سجل التدقيق الأمني التشفيري",
            desc="سلسلة هاش SHA-256 للمعاملات الذرية تمنع التعديل والحذف وتضمن عدم وجود صفوف يتيمة.",
            status_text="🔒 سلسلة الهاش سليمة ومحمية",
            status_style="status-info",
        )
        grid_layout.addWidget(card_audit)

        card_sec = self._create_status_card(
            title="التوثيق وقفل الحساب التلقائي",
            desc="تجزئة Argon2id/PBKDF2 مع قفل دائم بعد 5 محاولات فاشلة ومفتاح استعادة رئيسي.",
            status_text="🛡️ الحماية النشطة مفعلة",
            status_style="status-neutral",
        )
        grid_layout.addWidget(card_sec)

        layout.addLayout(grid_layout)

        # 4. Phase 1 Roadmap Card
        roadmap_card = QFrame()
        roadmap_card.setProperty("class", "card")
        road_layout = QVBoxLayout(roadmap_card)
        road_layout.setSpacing(8)

        road_title = QLabel(format_mixed_text("مسار بناء منظومة «كاشف» (Phase 1):"))
        road_title.setProperty("class", "h2")
        road_layout.addWidget(road_title)

        road_desc = QLabel(format_mixed_text(
            "• الدورة 1 (منجزة): واجهة الدخول والجلسات (Session RBAC)، قفل الخمول، وتطبيق الهوية البصرية.\n"
            "• الدورة 2A (منجزة): النواة الحسابية المجردة لمعايير IAEA، حاسبة الاضمحلال، ومحرك قواعد القرار (100% تغطية).\n"
            "• الدورة 2B (الجارية): ترحيل 003، المستودعات الذرية، التنقل بالشريط الجانبي، وسجلات الكواشف والأجهزة والمصادر."
        ))
        road_desc.setProperty("class", "body")
        road_layout.addWidget(road_desc)

        layout.addWidget(roadmap_card)
        layout.addStretch()

        return scroll

    def _create_summary_card(
        self,
        title: str,
        count_text: str,
        sub_text: str,
        on_click: Callable[[], None],
        is_warning: bool = False,
    ) -> QFrame:
        """Create a clickable statistic summary card."""
        card = QFrame()
        card.setProperty("class", "summary-card")
        card.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        lbl_title = QLabel(title)
        lbl_title.setProperty("class", "summary-title")

        lbl_count = QLabel(count_text)
        lbl_count.setProperty("class", "summary-count-warning" if is_warning else "summary-count")

        lbl_sub = QLabel(sub_text)
        lbl_sub.setProperty("class", "summary-sub")
        lbl_sub.setWordWrap(True)

        layout.addWidget(lbl_title)
        layout.addWidget(lbl_count)
        layout.addWidget(lbl_sub)

        # Store reference on card for dynamic updates
        card.lbl_count = lbl_count  # type: ignore
        card.mousePressEvent = lambda event: on_click()  # type: ignore

        return card

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

    def refresh_dashboard(self) -> None:
        """Query registry counts and rules to update dashboard summary cards."""
        if not self.db_conn:
            return

        try:
            cursor = self.db_conn.cursor()

            # 1. Detectors Count (Active)
            cursor.execute("SELECT COUNT(*) FROM detectors WHERE status != 'RETIRED';")
            det_count = cursor.fetchone()[0]
            if hasattr(self.card_detectors, "lbl_count"):
                self.card_detectors.lbl_count.setText(str(det_count))

            # 2. Instruments Count (Active)
            cursor.execute("SELECT COUNT(*) FROM instruments WHERE status != 'RETIRED';")
            inst_count = cursor.fetchone()[0]
            if hasattr(self.card_instruments, "lbl_count"):
                self.card_instruments.lbl_count.setText(str(inst_count))

            # 3. Expired Instruments via R-INST-01
            cursor.execute("SELECT id, cal_expiry FROM instruments WHERE status != 'RETIRED' AND cal_expiry IS NOT NULL;")
            exp_rows = cursor.fetchall()
            expired_count = 0
            for r in exp_rows:
                inst_id = r[0]
                exp_str = r[1]
                try:
                    exp_date = date.fromisoformat(exp_str)
                    res = eval_r_inst_01_calibration(exp_date)
                    if res.verdict.value == "WARNING":
                        expired_count += 1
                except Exception as e:
                    logger.warning("Corrupt cal_expiry '%s' for instrument %s: %s", exp_str, inst_id, e)
                    expired_count += 1
            if hasattr(self.card_expired, "lbl_count"):
                self.card_expired.lbl_count.setText(str(expired_count))

            # 4. Sources Needing Attention via R-SRC-01 (Only applies to Co-60 and Cs-137)
            cursor.execute(
                """
                SELECT s.id, si.nuclide_code, si.cert_activity_bq, si.cert_date, n.half_life_days
                FROM sources s
                JOIN source_isotopes si ON s.id = si.source_id
                JOIN nuclides n ON si.nuclide_code = n.code
                WHERE s.status != 'RETIRED' AND si.nuclide_code IN ('Co-60', 'Cs-137');
                """
            )
            src_rows = cursor.fetchall()
            today = date.today()
            out_of_spec_sources = set()
            for r in src_rows:
                source_id = r[0]
                nuclide = r[1]
                cert_act = r[2]
                cert_date_str = r[3]
                hl_days = r[4]
                try:
                    cert_d = date.fromisoformat(cert_date_str)
                    elapsed = (today - cert_d).days
                    if elapsed >= 0:
                        cur_act = decay_activity(cert_act, float(elapsed), hl_days)
                        res = eval_r_src_01_source_activity(cur_act)
                        if res.verdict.value != "PASS":
                            out_of_spec_sources.add(source_id)
                    else:
                        logger.warning("Future certificate date '%s' for source %s", cert_date_str, source_id)
                        out_of_spec_sources.add(source_id)
                except Exception as e:
                    logger.warning("Corrupt date or activity for source %s: %s", source_id, e)
                    out_of_spec_sources.add(source_id)
            attention_count = len(out_of_spec_sources)
            if hasattr(self.card_sources, "lbl_count"):
                self.card_sources.lbl_count.setText(str(attention_count))

            cursor.close()
        except Exception as e:
            logger.error("Failed to refresh dashboard stats: %s", e)

    def navigate_to_section(self, section_id: str) -> None:
        """Switch navigation sidebar and stacked widget to the requested section."""
        self.sidebar.set_active_section(section_id)
        self._on_navigation_changed(section_id)

    def _on_navigation_changed(self, section_id: str) -> None:
        """Handle sidebar section selection."""
        if section_id == "dashboard":
            self.refresh_dashboard()
            self.stack.setCurrentIndex(0)
        elif section_id == "detectors":
            if hasattr(self, "detectors_view") and hasattr(self.detectors_view, "refresh"):
                self.detectors_view.refresh()
            self.stack.setCurrentIndex(1)
        elif section_id == "instruments":
            if hasattr(self, "instruments_view") and hasattr(self.instruments_view, "refresh"):
                self.instruments_view.refresh()
            self.stack.setCurrentIndex(2)
        elif section_id == "sources":
            if hasattr(self, "sources_view") and hasattr(self.sources_view, "refresh"):
                self.sources_view.refresh()
            self.stack.setCurrentIndex(3)
        elif section_id == "users":
            self._open_users_management()

    def handle_quick_search(self) -> None:
        """Global quick search across detectors, instruments, and sources."""
        query = self.edit_quick_search.text().strip()
        if not query or not self.db_conn:
            return

        results = quick_search(self.db_conn, query)
        if not results:
            _show_msg(self, "info", "نتائج البحث", f"لم يتم العثور على أي كواشف أو أجهزة أو مصادر مطابقة لـ: {query}")
            return

        if len(results) == 1:
            # Open detail dialog directly
            r = results[0]
            eid = r["id"]
            if r["entity_type"] == "detector":
                data = DetectorRepository(self.db_conn).get_by_id(eid)
                if data:
                    dlg = DetectorDetailDialog(data, parent=self)
                    dlg.exec()
            elif r["entity_type"] == "instrument":
                data = InstrumentRepository(self.db_conn).get_by_id(eid)
                if data:
                    dlg = InstrumentDetailDialog(data, parent=self)
                    dlg.exec()
            elif r["entity_type"] == "source":
                data = SourceRepository(self.db_conn).get_by_id(eid)
                if data:
                    dlg = SourceDetailDialog(data, parent=self)
                    dlg.exec()
        else:
            # Open results picker dialog
            dlg = QuickSearchResultsDialog(results=results, conn=self.db_conn, parent=self)
            dlg.exec()

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
        self.refresh_dashboard()

    def _apply_logo(self) -> None:
        """Show the logo variant matching active theme."""
        path = logo_file(self.current_theme)
        if os.path.exists(path) and hasattr(self, "logo_label"):
            self.logo_label.setPixmap(QPixmap(path).scaled(
                125, 125, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _open_users_management(self) -> None:
        """Open administrative users management dialog."""
        if not self.db_conn or not self.session:
            return
        dlg = UsersManagementDialog(conn=self.db_conn, session=self.session, parent=self)
        dlg.exec()

    def close_secondary_windows(self) -> None:
        """Close all open secondary dialogs and child windows without prompting for save."""
        app = QApplication.instance()
        if not app:
            return
        widgets = app.topLevelWidgets()
        for w in reversed(widgets):
            if w is self or not w.isVisible():
                continue
            try:
                w.close()
            except Exception as e:
                logger.error("Error closing secondary dialog: %s", e)

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
        """Orderly logout: stop idle timer, close secondary windows, log audit, clear session."""
        if self._is_logging_out:
            return
        self._is_logging_out = True

        # 1. Stop idle lock timer and remove event filter
        if hasattr(self, "idle_lock") and self.idle_lock:
            self.idle_lock.stop()

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
            except Exception as e:
                logger.error("Error recording logout audit: %s", e)

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

"""Main application window for KASHIF-QA.

Displays institutional seal logo, system status badges, version info,
and database connection status bar in a modern dark laboratory aesthetic.
"""

import os
import sqlite3
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from kashif import __version__
from kashif.gui.theme import LAB_DARK_QSS
from kashif.services.audit_service import verify_audit_chain


class MainWindow(QMainWindow):
    """KASHIF-QA primary desktop window."""

    def __init__(self, db_conn: Optional[sqlite3.Connection] = None):
        super().__init__()
        self.db_conn = db_conn
        
        self.setWindowTitle(f"كاشف — منظومة ضبط الجودة واعتماد الكواشف الإشعاعية (KASHIF-QA) v{__version__}")
        self.resize(1180, 780)
        self.setMinimumSize(960, 640)
        
        self._init_ui()
        self._check_system_health()

    def _init_ui(self) -> None:
        """Initialize UI components and layout."""
        self.setStyleSheet(LAB_DARK_QSS)
        
        # Central widget with scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.setCentralWidget(scroll)
        
        container = QWidget()
        scroll.setWidget(container)
        
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(32, 24, 32, 24)
        main_layout.setSpacing(20)
        
        # 1. Header with Official Logo & Titles
        header_card = QFrame()
        header_card.setProperty("class", "card")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(20, 20, 20, 20)
        header_layout.setSpacing(24)
        
        # Logo
        logo_label = QLabel()
        logo_path = os.path.join(os.path.dirname(__file__), "assets", "kashif_logo.png")
        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            logo_label.setPixmap(pixmap.scaled(130, 130, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.setWindowIcon(QIcon(logo_path))
        else:
            logo_label.setText("⚛️")
            logo_label.setStyleSheet("font-size: 70px;")
            
        logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(logo_label)
        
        # Title text block
        title_box = QVBoxLayout()
        title_box.setSpacing(6)
        
        app_title = QLabel("منظومة «كاشف» الرقمية لضبط جودة واعتماد الكواشف الإشعاعية")
        app_title.setProperty("class", "title")
        app_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #00F2FE;")
        
        en_title = QLabel("KASHIF-QA: Nuclear Radiation Detector Quality Assurance & Spectroscopy System")
        en_title.setProperty("class", "subtitle")
        en_title.setStyleSheet("font-size: 13px; color: #94A3B8;")
        
        std_ref = QLabel("المرجعية القياسية: IAEA Procedure Nº MRNI-507 (HPGe) / MRNI-504 (NaI) & TECDOC-1599")
        std_ref.setStyleSheet("font-size: 12px; color: #64748B;")
        
        badges_row = QHBoxLayout()
        badges_row.setSpacing(10)
        
        v_badge = QLabel(f"الإصدار: v{__version__} (Phase 0: Skeleton)")
        v_badge.setProperty("class", "badge-cyan")
        
        gate_badge = QLabel("بوابة الاعتماد: Gate G1 مُجتازة ✔")
        gate_badge.setProperty("class", "badge-green")
        
        badges_row.addWidget(v_badge)
        badges_row.addWidget(gate_badge)
        badges_row.addStretch()
        
        title_box.addWidget(app_title)
        title_box.addWidget(en_title)
        title_box.addWidget(std_ref)
        title_box.addLayout(badges_row)
        
        header_layout.addLayout(title_box, stretch=1)
        main_layout.addWidget(header_card)
        
        # 2. System Status Overview (Cards Grid)
        grid_layout = QHBoxLayout()
        grid_layout.setSpacing(16)
        
        # Card 1: Database
        card_db = self._create_status_card(
            title="قاعدة بيانات SQLite",
            desc="تخزين محلي معزول بنمط الكتابة المسبقة (WAL Mode) لمنع تلف القفل وضمان الأداء الأقصى.",
            status_text="🟢 نمط WAL نشط",
            status_style="color: #10B981; font-weight: bold;"
        )
        grid_layout.addWidget(card_db)
        
        # Card 2: Cryptographic Audit
        card_audit = self._create_status_card(
            title="سجل التدقيق الأمني التشفيري",
            desc="سلسلة هاش SHA-256 محكمة مع تريجرز SQLite تمنع التعديل والحذف وتضمن عدم القابلية للتزوير.",
            status_text="🔒 سلسلة الهاش سليمة",
            status_style="color: #00F2FE; font-weight: bold;"
        )
        grid_layout.addWidget(card_audit)
        
        # Card 3: Authentication & Security
        card_sec = self._create_status_card(
            title="التوثيق وتشفير كلمات المرور",
            desc="تجزئة بكلمات سرية بمعيار Argon2id (وبديل PBKDF2 القياسي)، مع مفتاح استعادة رقمي للطوارئ.",
            status_text="🛡️ Argon2id مفعل",
            status_style="color: #60A5FA; font-weight: bold;"
        )
        grid_layout.addWidget(card_sec)
        
        main_layout.addLayout(grid_layout)
        
        # 3. Roadmap & Readiness Box
        roadmap_card = QFrame()
        roadmap_card.setProperty("class", "card")
        road_layout = QVBoxLayout(roadmap_card)
        road_layout.setSpacing(10)
        
        road_title = QLabel("حالة المرحلة الحالية ومسار البناء الفني:")
        road_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #E2E8F0;")
        road_layout.addWidget(road_title)
        
        road_desc = QLabel(
            "• Phase 0 (الحالية): الهيكل العظمي، الأمان الأولي، وتجهيز البيئة (جاهزة لاعتماد G2).\n"
            "• Phase 1 (التالية): النواة الفيزيائية، محرك قواعد القرار، وسجلات الكواشف والأجهزة والمصادر.\n"
            "• Phase 2: معالج فحص كواشف الجرمانيوم التفاعلي ذو المراحل الخمس (HPGe Wizard).\n"
            "• Phase 3: فحص كواشف يوديد الصوديوم (NaI(Tl)) ومحرك تقارير IAEA Annex II بصيغة PDF."
        )
        road_desc.setStyleSheet("font-size: 13px; color: #94A3B8; line-height: 1.6;")
        road_layout.addWidget(road_desc)
        
        main_layout.addWidget(roadmap_card)
        main_layout.addStretch()
        
        # 4. Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        self.lbl_db_status = QLabel("🟢 قاعدة البيانات: متصلة بنجاح")
        self.lbl_audit_status = QLabel("🔒 سجل التدقيق: سليم ومحمي بالتريجرز")
        self.lbl_user_status = QLabel("👤 المستخدم: admin (مدير النظام)")
        
        self.status_bar.addWidget(self.lbl_db_status)
        self.status_bar.addWidget(self.lbl_audit_status)
        self.status_bar.addPermanentWidget(self.lbl_user_status)

    def _create_status_card(self, title: str, desc: str, status_text: str, status_style: str) -> QFrame:
        """Create a stylized information card widget."""
        card = QFrame()
        card.setProperty("class", "card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        
        t_label = QLabel(title)
        t_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #F8FAFC;")
        
        d_label = QLabel(desc)
        d_label.setStyleSheet("font-size: 12px; color: #94A3B8;")
        d_label.setWordWrap(True)
        
        s_label = QLabel(status_text)
        s_label.setStyleSheet(f"font-size: 13px; {status_style}")
        
        layout.addWidget(t_label)
        layout.addWidget(d_label)
        layout.addStretch()
        layout.addWidget(s_label)
        return card

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
            
            # Verify audit chain
            is_valid, _ = verify_audit_chain(self.db_conn)
            if is_valid:
                self.lbl_audit_status.setText("🔒 سجل التدقيق: سليم ومحمي")
            else:
                self.lbl_audit_status.setText("⚠️ تنبيه: تم رصد خلل في سلسلة الهاش")
        except Exception as e:
            self.lbl_db_status.setText(f"🔴 خطأ في قاعدة البيانات: {str(e)[:30]}")

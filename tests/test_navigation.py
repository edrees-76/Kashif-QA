"""Tests for sidebar navigation and dashboard integration (TASK-1.7 & Decision 12).

Tests:
- Role-Based section visibility in the sidebar navigation (ADMIN, LAB_MANAGER, TECHNICIAN)
- Unready sections are never shown
- Sidebar expand and collapse toggle behavior
- Selection signal and active item highlighting
- Dashboard summary cards click navigation to respective sections
- Dashboard counts accuracy reflecting active records and R-INST-01 / R-SRC-01 rules
"""

from datetime import date, timedelta
import sqlite3
import pytest
from PyQt6.QtCore import Qt

from kashif.core.models import (
    DetectorType,
    HPGeDetector,
    SemiconductorType,
    Instrument,
    InstrumentType,
    Nuclide,
    RadioactiveSource,
    SourceIsotope,
)
from kashif.db.migrations import apply_migrations, init_migrations_table
from kashif.db.repositories import (
    DetectorRepository,
    InstrumentRepository,
    SourceRepository,
)
from kashif.gui.main_window import MainWindow
from kashif.gui.navigation import NAV_ITEMS, SidebarNavigation
from kashif.services.session import (
    ROLE_ADMIN,
    ROLE_LAB_MANAGER,
    ROLE_TECHNICIAN,
    Session,
)


@pytest.fixture(autouse=True)
def auto_accept_close_confirmation(monkeypatch):
    """Automatically answer Yes to any confirmation dialog during test teardown."""
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_migrations_table(conn)
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture
def admin_session():
    return Session(user_id=1, username="admin", full_name="مدير النظام", role=ROLE_ADMIN)


@pytest.fixture
def manager_session():
    return Session(user_id=2, username="manager", full_name="مدير المعمل", role=ROLE_LAB_MANAGER)


@pytest.fixture
def technician_session():
    return Session(user_id=3, username="tech", full_name="فني القياسات", role=ROLE_TECHNICIAN)


# ---------------------------------------------------------------------------
# Sidebar Role Visibility Matrix
# ---------------------------------------------------------------------------

def test_sidebar_role_visibility_matrix(qtbot, admin_session, manager_session, technician_session):
    """Verify that each role only sees its permitted sections in the navigation sidebar."""
    # 1. Admin sees: dashboard, detectors, instruments, sources, users
    admin_nav = SidebarNavigation(session=admin_session)
    qtbot.addWidget(admin_nav)
    admin_sections = admin_nav.get_visible_sections()
    assert admin_sections == ["dashboard", "detectors", "instruments", "sources", "users"]

    # 2. Lab Manager sees: dashboard, detectors, instruments, sources (NO users)
    mgr_nav = SidebarNavigation(session=manager_session)
    qtbot.addWidget(mgr_nav)
    mgr_sections = mgr_nav.get_visible_sections()
    assert mgr_sections == ["dashboard", "detectors", "instruments", "sources"]
    assert "users" not in mgr_sections

    # 3. Technician sees: dashboard, detectors, instruments, sources (NO users)
    tech_nav = SidebarNavigation(session=technician_session)
    qtbot.addWidget(tech_nav)
    tech_sections = tech_nav.get_visible_sections()
    assert tech_sections == ["dashboard", "detectors", "instruments", "sources"]
    assert "users" not in tech_sections


def test_sidebar_unready_sections_are_not_shown():
    """Verify that unready sections (like inspections, reports, history) are not in NAV_ITEMS."""
    item_ids = [item["id"] for item in NAV_ITEMS]
    assert "inspections" not in item_ids
    assert "reports" not in item_ids
    assert "history" not in item_ids
    assert "audit" not in item_ids


def test_sidebar_collapse_and_expand_toggle(qtbot, admin_session):
    """Test collapsing to icons and expanding back with width and text adjustments."""
    nav = SidebarNavigation(session=admin_session)
    qtbot.addWidget(nav)
    nav.show()

    # Initial expanded state
    assert not nav.is_collapsed
    assert nav.btn_toggle.text() == "◀"
    assert nav.lbl_title.isVisible()
    assert nav.maximumWidth() >= 190
    assert "الكواشف" in nav._buttons["detectors"].text()

    # Click toggle -> Collapse
    nav.btn_toggle.click()
    assert nav.is_collapsed
    assert nav.btn_toggle.text() == "▶"
    assert not nav.lbl_title.isVisible()
    assert nav.maximumWidth() <= 70
    assert nav._buttons["detectors"].text() == "⚛️"

    # Click toggle -> Expand
    nav.btn_toggle.click()
    assert not nav.is_collapsed
    assert nav.btn_toggle.text() == "◀"
    assert nav.lbl_title.isVisible()
    assert "الكواشف" in nav._buttons["detectors"].text()


def test_sidebar_selection_signal_and_highlight(qtbot, manager_session):
    """Test clicking a sidebar button emits signal and updates property class."""
    nav = SidebarNavigation(session=manager_session)
    qtbot.addWidget(nav)

    emitted = []
    nav.section_selected.connect(emitted.append)

    # Click on "instruments"
    nav._buttons["instruments"].click()
    assert emitted == ["instruments"]
    assert nav.active_section_id == "instruments"
    assert nav._buttons["instruments"].property("class") == "nav-item-active"
    assert nav._buttons["detectors"].property("class") == "nav-item"


# ---------------------------------------------------------------------------
# Dashboard Integration & Summary Cards
# ---------------------------------------------------------------------------

def test_dashboard_summary_cards_click_navigation(qtbot, db_conn, manager_session):
    """Test that clicking dashboard summary cards switches to the corresponding view."""
    win = MainWindow(db_conn=db_conn, session=manager_session)
    qtbot.addWidget(win)
    win.show()

    # Initial page is Dashboard (index 0)
    assert win.stack.currentIndex() == 0

    # 1. Click card_detectors -> navigates to Detectors (index 1)
    win.card_detectors.mousePressEvent(None)
    assert win.stack.currentIndex() == 1
    assert win.sidebar.active_section_id == "detectors"

    # Return to dashboard via sidebar
    win.sidebar._buttons["dashboard"].click()
    assert win.stack.currentIndex() == 0

    # 2. Click card_instruments -> navigates to Instruments (index 2)
    win.card_instruments.mousePressEvent(None)
    assert win.stack.currentIndex() == 2
    assert win.sidebar.active_section_id == "instruments"

    # Return to dashboard
    win.sidebar._buttons["dashboard"].click()
    assert win.stack.currentIndex() == 0

    # 3. Click card_expired -> navigates to Instruments (index 2)
    win.card_expired.mousePressEvent(None)
    assert win.stack.currentIndex() == 2

    # Return to dashboard
    win.sidebar._buttons["dashboard"].click()
    assert win.stack.currentIndex() == 0

    # 4. Click card_sources -> navigates to Sources (index 3)
    win.card_sources.mousePressEvent(None)
    assert win.stack.currentIndex() == 3
    assert win.sidebar.active_section_id == "sources"


def test_dashboard_summary_counts_accuracy(qtbot, db_conn, technician_session):
    """Verify that summary counts match database contents and rules evaluation."""
    det_repo = DetectorRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)
    src_repo = SourceRepository(db_conn)

    # Seed 2 detectors (1 active, 1 retired)
    d1 = det_repo.create(HPGeDetector(code="DET-A1", manufacturer="Ortec", model="G1", serial_number="SN-A1", nominal_voltage=3000, semiconductor_type=SemiconductorType.P_TYPE), technician_session)
    d2 = det_repo.create(HPGeDetector(code="DET-A2", manufacturer="Ortec", model="G2", serial_number="SN-A2", nominal_voltage=3000, semiconductor_type=SemiconductorType.P_TYPE), technician_session)
    det_repo.retire(d2, technician_session, "Retired")

    # Seed 2 instruments (1 valid cal, 1 expired cal)
    inst_repo.create(Instrument(
        instrument_type=InstrumentType.HV_SUPPLY,
        manufacturer="Canberra",
        model="3106",
        serial_number="HV-OK",
        cal_expiry=date.today() + timedelta(days=180),
    ), technician_session)

    inst_repo.create(Instrument(
        instrument_type=InstrumentType.AMPLIFIER,
        manufacturer="Canberra",
        model="2022",
        serial_number="AMP-EXP",
        cal_expiry=date.today() - timedelta(days=30),  # Expired!
    ), technician_session)

    src_repo.create(RadioactiveSource(
        source_code="SRC-OK",
        serial_number="CO-OK-1",
        isotopes=[SourceIsotope(nuclide_code="Co-60", cert_activity_bq=37000.0, cert_date=date.today())],
    ), technician_session)

    src_repo.create(RadioactiveSource(
        source_code="SRC-LOW",
        serial_number="CS-LOW-1",
        isotopes=[SourceIsotope(nuclide_code="Cs-137", cert_activity_bq=5000.0, cert_date=date.today())],
    ), technician_session)

    # Initialize main window and refresh
    win = MainWindow(db_conn=db_conn, session=technician_session)
    qtbot.addWidget(win)
    win.show()
    win.refresh_dashboard()

    # Active detectors: exactly 1
    assert win.card_detectors.lbl_count.text() == "1"

    # Active instruments: exactly 2
    assert win.card_instruments.lbl_count.text() == "2"

    # Expired instruments: exactly 1 (AMP-EXP)
    assert win.card_expired.lbl_count.text() == "1"

    # Sources needing attention: exactly 1 (SRC-LOW)
    assert win.card_sources.lbl_count.text() == "1"

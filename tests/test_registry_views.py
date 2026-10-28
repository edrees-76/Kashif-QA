"""PyQt6 GUI tests for Technical Registries and Views (TASK-1.7).

Follows Cycle 1 lessons rigorously:
- Asserts that form fields and action buttons are truly isVisible() after show().
- Verifies both Light and Dark modes.
- Tests that secondary dialogs close cleanly upon close_secondary_windows().
- Tests add/edit/retire interactions for Detectors, Instruments, and Sources.
- Tests R-INST-01 calibration expiry red badge (icon + text).
- Tests interactive decay calculator and R-SRC-01 activity badge.
- Tests global quick search.
"""

from datetime import date, timedelta
import sqlite3
import pytest
from PyQt6.QtCore import QDate, Qt

from kashif.core.decay import decay_activity
from kashif.core.models import (
    CrystalType,
    DetectorType,
    HPGeDetector,
    Instrument,
    InstrumentType,
    NaIDetector,
    Nuclide,
    RadioactiveSource,
    SourceIsotope,
)
from kashif.db.migrations import apply_migrations, init_migrations_table
from kashif.db.repositories import (
    DetectorRepository,
    InstrumentRepository,
    NuclideRepository,
    SourceRepository,
)
from kashif.gui.main_window import MainWindow
from kashif.gui.theme import get_stylesheet, set_mode
from kashif.gui.views.detectors_view import (
    AddEditDetectorDialog,
    DetectorDetailDialog,
    DetectorsView,
)
from kashif.gui.views.instruments_view import (
    AddEditInstrumentDialog,
    InstrumentDetailDialog,
    InstrumentsView,
)
from kashif.gui.views.sources_view import (
    AddCustomNuclideDialog,
    AddEditSourceDialog,
    SourceDetailDialog,
    SourcesView,
)
from kashif.services.session import (
    ROLE_ADMIN,
    ROLE_LAB_MANAGER,
    ROLE_TECHNICIAN,
    Session,
)


@pytest.fixture(autouse=True)
def auto_accept_close_confirmation(monkeypatch):
    """Automatically answer Yes to confirmation dialogs and show modal dialogs non-blockingly during tests."""
    from PyQt6.QtWidgets import QMessageBox, QDialog
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    def non_blocking_exec(self):
        self.show()
        return 1
    monkeypatch.setattr(QDialog, "exec", non_blocking_exec)


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_migrations_table(conn)
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture
def tech_session():
    return Session(user_id=1, username="tech", full_name="فني قياسات", role=ROLE_TECHNICIAN)


@pytest.fixture
def admin_session():
    return Session(user_id=2, username="admin", full_name="مدير النظام", role=ROLE_ADMIN)


# ---------------------------------------------------------------------------
# Detectors View & Dialog Tests
# ---------------------------------------------------------------------------

def test_add_hpge_detector_dialog_visibility_and_creation(qtbot, db_conn, tech_session):
    """Test HPGe detector dialog: verify isVisible() on all fields and submit."""
    repo = DetectorRepository(db_conn)
    dlg = AddEditDetectorDialog(repo=repo, session=tech_session)
    qtbot.addWidget(dlg)
    dlg.show()

    # Lesson check: assert form fields and buttons are actually visible
    assert dlg.radio_hpge.isVisible()
    assert dlg.edit_code.isVisible()
    assert dlg.edit_manufacturer.isVisible()
    assert dlg.edit_model.isVisible()
    assert dlg.edit_serial.isVisible()
    assert dlg.edit_voltage.isVisible()
    assert dlg.btn_save.isVisible()
    assert dlg.btn_cancel.isVisible()

    # Fill HPGe data
    dlg.edit_code.setText("HPGE-LAB-01")
    dlg.edit_manufacturer.setText("Mirion")
    dlg.edit_model.setText("Canberra-BE3830")
    dlg.edit_serial.setText("SN-88310")
    dlg.edit_voltage.setText("3500")
    dlg.edit_facility.setText("معمل أشعة جاما")

    # Click Save
    dlg.btn_save.click()

    # Verify record in DB
    rec = repo.get_by_code("HPGE-LAB-01")
    assert rec is not None
    assert rec["manufacturer"] == "Mirion"
    assert rec["nominal_voltage"] == 3500.0


def test_add_nai_detector_dialog_visibility_and_creation(qtbot, db_conn, tech_session):
    """Test NaI detector dialog: switch radio to NaI, verify NaI fields isVisible()."""
    repo = DetectorRepository(db_conn)
    dlg = AddEditDetectorDialog(repo=repo, session=tech_session)
    qtbot.addWidget(dlg)
    dlg.show()

    # Switch to NaI
    dlg.radio_nai.click()
    assert dlg.nai_group.isVisible()
    assert not dlg.hpge_group.isVisible()

    assert dlg.edit_nai_diam.isVisible()
    assert dlg.edit_nai_height.isVisible()
    assert dlg.edit_nai_voltage.isVisible()
    assert dlg.edit_nai_res.isVisible()

    # Fill NaI data
    dlg.edit_code.setText("NAI-LAB-01")
    dlg.edit_manufacturer.setText("Saint-Gobain")
    dlg.edit_model.setText("3M3/3")
    dlg.edit_serial.setText("SG-9912")
    dlg.edit_nai_diam.setText("3.0")
    dlg.edit_nai_height.setText("3.0")
    dlg.edit_nai_voltage.setText("900")
    dlg.edit_nai_res.setText("7.2")

    dlg.btn_save.click()

    rec = repo.get_by_code("NAI-LAB-01")
    assert rec is not None
    assert rec["detector_type"] == "NAI"
    assert rec["diameter_inch"] == 3.0
    assert rec["nominal_resolution_pct"] == 7.2


def test_detectors_view_table_and_retire(qtbot, db_conn, tech_session):
    """Test detectors view table population, details dialog, and retirement."""
    repo = DetectorRepository(db_conn)
    d_id = repo.create(HPGeDetector(
        code="HPGE-VIEW",
        manufacturer="Ortec",
        model="GEM",
        serial_number="SN-VIEW-1",
        nominal_voltage=2800,
        facility_name="المعمل المركزي",
    ), tech_session)

    view = DetectorsView(conn=db_conn, session=tech_session)
    qtbot.addWidget(view)
    view.show()

    assert view.table.isVisible()
    assert view.btn_add.isVisible()
    assert view.btn_details.isVisible()
    assert view.btn_edit.isVisible()
    assert view.btn_retire.isVisible()
    assert view.table.rowCount() == 1
    assert view.table.item(0, 1).text() == "HPGE-VIEW"

    # Select row and retire directly via repo (simulating confirmed prompt)
    repo.retire(d_id, tech_session, "Retired in test")
    view.refresh()
    assert view.table.item(0, 7).text() == "متقاعد"


# ---------------------------------------------------------------------------
# Instruments View & Dialog Tests
# ---------------------------------------------------------------------------

def test_add_instrument_dialog_visibility_and_creation(qtbot, db_conn, tech_session):
    """Test instrument dialog: check visibility of structured fields and create."""
    repo = InstrumentRepository(db_conn)
    dlg = AddEditInstrumentDialog(repo=repo, session=tech_session)
    qtbot.addWidget(dlg)
    dlg.show()

    assert dlg.combo_type.isVisible()
    assert dlg.edit_mfr.isVisible()
    assert dlg.edit_model.isVisible()
    assert dlg.edit_serial.isVisible()
    assert dlg.edit_ripple.isVisible()
    assert dlg.edit_channels.isVisible()
    assert dlg.date_cal.isVisible()
    assert dlg.date_expiry.isVisible()
    assert dlg.btn_save.isVisible()

    dlg.combo_type.setCurrentIndex(1)  # Choose MCA instead of placeholder
    dlg.edit_mfr.setText("Canberra")
    dlg.edit_model.setText("Multiport-II")
    dlg.edit_serial.setText("MP-8192-A")
    dlg.edit_channels.setText("8192")
    dlg.edit_facility.setText("معمل القياسات")

    dlg.btn_save.click()

    records = repo.list_all()
    assert any(r["serial_number"] == "MP-8192-A" for r in records)


def test_instruments_view_expired_calibration_red_badge(qtbot, db_conn, tech_session):
    """Verify red badge with icon and text appears for expired instrument."""
    repo = InstrumentRepository(db_conn)

    # 1. Expired instrument
    repo.create(Instrument(
        instrument_type=InstrumentType.HV_SUPPLY,
        manufacturer="Ortec",
        model="659",
        serial_number="EXP-HV-01",
        cal_expiry=date.today() - timedelta(days=10),  # Expired
    ), tech_session)

    # 2. Valid instrument
    repo.create(Instrument(
        instrument_type=InstrumentType.AMPLIFIER,
        manufacturer="Ortec",
        model="672",
        serial_number="VAL-AMP-01",
        cal_expiry=date.today() + timedelta(days=120),  # Valid
    ), tech_session)

    view = InstrumentsView(conn=db_conn, session=tech_session)
    qtbot.addWidget(view)
    view.show()

    assert view.table.rowCount() == 2

    # Check cell widgets for calibration badges
    badge0 = view.table.cellWidget(0, 6)
    badge1 = view.table.cellWidget(1, 6)

    assert badge0 is not None
    assert badge1 is not None

    texts = [badge0.text(), badge1.text()]
    classes = [badge0.property("class"), badge1.property("class")]

    assert "⚠️ منتهي المعايرة" in texts
    assert "✓ سارية" in texts
    assert "badge-expired" in classes
    assert "badge-valid" in classes


# ---------------------------------------------------------------------------
# Sources View & Interactive Decay Calculator Tests
# ---------------------------------------------------------------------------

def test_add_source_dialog_visibility_and_creation(qtbot, db_conn, tech_session):
    """Test multi-isotope source dialog: check field visibility, unit selection, and create."""
    repo = SourceRepository(db_conn)
    dlg = AddEditSourceDialog(repo=repo, session=tech_session)
    qtbot.addWidget(dlg)
    dlg.show()

    assert dlg.edit_code.isVisible()
    assert dlg.edit_serial.isVisible()
    assert dlg.btn_add_iso.isVisible()
    assert dlg.btn_new_custom_nuclide.isVisible()
    assert dlg.tbl_isotopes.isVisible()
    assert dlg.btn_save.isVisible()

    dlg.edit_code.setText("SRC-CO60-TEST")
    dlg.edit_serial.setText("SN-C60-99")
    dlg.edit_facility.setText("المعمل النووي")

    # Row 0 has pre-populated Co-60
    assert dlg.tbl_isotopes.rowCount() == 1
    edit_act = dlg.tbl_isotopes.cellWidget(0, 1)
    combo_unit = dlg.tbl_isotopes.cellWidget(0, 2)
    assert edit_act is not None
    assert combo_unit is not None

    edit_act.setText("37.0")
    idx_kbq = combo_unit.findData("kBq")
    combo_unit.setCurrentIndex(idx_kbq)  # 37.0 kBq = 37,000 Bq

    dlg.btn_save.click()

    rec = repo.get_by_code("SRC-CO60-TEST")
    assert rec is not None
    assert rec["isotopes"][0]["cert_activity_bq"] == 37000.0


def test_sources_view_interactive_decay_calculation(qtbot, db_conn, tech_session):
    """Test that changing target date in sources view updates activity and R-SRC-01 badge."""
    repo = SourceRepository(db_conn)
    cert_d = date(2020, 1, 1)

    repo.create(RadioactiveSource(
        source_code="SRC-CS-DECAY",
        serial_number="SN-CS-CALC",
        isotopes=[SourceIsotope(nuclide_code="Cs-137", cert_activity_bq=100000.0, cert_date=cert_d)],
    ), tech_session)

    view = SourcesView(conn=db_conn, session=tech_session)
    qtbot.addWidget(view)
    view.show()

    assert view.date_calc_global.isVisible()
    assert view.table.rowCount() == 1

    # Activity at today
    act_col_text = view.table.item(0, 6).text()
    assert any(u in act_col_text for u in ["Bq", "kBq", "MBq", "GBq"])

    # Change calculation date to 30 years later (approx 1 half life)
    future_date = QDate(2050, 1, 1)
    view.date_calc_global.setDate(future_date)

    # Re-check activity has decreased
    new_act_text = view.table.item(0, 6).text()
    assert new_act_text != act_col_text


# ---------------------------------------------------------------------------
# Global Quick Search & Details Modal Tests
# ---------------------------------------------------------------------------

def test_global_quick_search_finds_and_opens_dialog(qtbot, db_conn, tech_session):
    """Test top bar quick search finds entity and opens detail dialog."""
    det_repo = DetectorRepository(db_conn)
    det_repo.create(HPGeDetector(
        code="HPGE-QUICK-SN",
        manufacturer="Mirion",
        model="BEGe",
        serial_number="SN-UNIQUE-9999",
        nominal_voltage=3000,
    ), tech_session)

    win = MainWindow(db_conn=db_conn, session=tech_session)
    qtbot.addWidget(win)
    win.show()

    # Search for unique serial number
    win.edit_quick_search.setText("SN-UNIQUE-9999")
    win.handle_quick_search()

    # Check that secondary dialog opened and is visible
    from PyQt6.QtWidgets import QApplication
    top_widgets = QApplication.instance().topLevelWidgets()
    dlg = next((w for w in top_widgets if isinstance(w, DetectorDetailDialog)), None)
    assert dlg is not None
    dlg.close()


# ---------------------------------------------------------------------------
# Theme Switching (Light & Dark Mode Support)
# ---------------------------------------------------------------------------

def test_theme_toggle_without_hardcoded_colors(qtbot, db_conn, tech_session):
    """Test switching between Light and Dark mode renders without crashes or inline errors."""
    win = MainWindow(db_conn=db_conn, session=tech_session)
    qtbot.addWidget(win)
    win.show()

    assert win.current_theme == "light"
    assert "الوضع الداكن" in win.btn_theme.text()

    # Switch to Dark Mode
    win.btn_theme.click()
    assert win.current_theme == "dark"
    assert "الوضع الفاتح" in win.btn_theme.text()

    # Switch back to Light Mode
    win.btn_theme.click()
    assert win.current_theme == "light"


# ---------------------------------------------------------------------------
# Secondary Windows Closing on Logout / Idle Lock
# ---------------------------------------------------------------------------

def test_close_secondary_windows_closes_dialogs(qtbot, db_conn, tech_session):
    """Test that close_secondary_windows automatically closes open dialogs without prompting."""
    win = MainWindow(db_conn=db_conn, session=tech_session)
    qtbot.addWidget(win)
    win.show()

    # Open a secondary dialog
    dlg1 = AddEditDetectorDialog(repo=DetectorRepository(db_conn), session=tech_session, parent=win)
    dlg1.show()
    assert dlg1.isVisible()

    dlg2 = AddEditInstrumentDialog(repo=InstrumentRepository(db_conn), session=tech_session, parent=win)
    dlg2.show()
    assert dlg2.isVisible()

    # Invoke close_secondary_windows
    win.close_secondary_windows()

    assert not dlg1.isVisible()
    assert not dlg2.isVisible()


# ---------------------------------------------------------------------------
# Comprehensive UI Workflow Tests (Edit, Detail, Retire, Filter, Validation)
# ---------------------------------------------------------------------------

def test_detectors_view_full_workflow(qtbot, db_conn, tech_session):
    repo = DetectorRepository(db_conn)
    det_id = repo.create(HPGeDetector(
        code="HPGE-WF-1",
        manufacturer="Canberra",
        model="GC2018",
        serial_number="SN-WF-1",
        nominal_voltage=3000.0,
        facility_name="معمل ألفا",
        notes="ملاحظات أولية",
    ), tech_session)

    view = DetectorsView(conn=db_conn, session=tech_session)
    qtbot.addWidget(view)
    view.show()
    assert view.isVisible()

    # Search & Facility filter
    view.edit_search.setText("GC2018")
    assert view.table.rowCount() == 1
    view.edit_search.setText("غير_موجود")
    assert view.table.rowCount() == 0
    view.edit_search.clear()

    # Filter facility
    view.combo_facility.setCurrentText("معمل ألفا")
    assert view.table.rowCount() == 1

    # Select row 0
    view.table.selectRow(0)

    # Details Dialog & Edit Dialog via view handlers
    view._on_view_details()
    view._on_edit_detector()

    # Edit Dialog directly to modify fields
    dlg_edit = AddEditDetectorDialog(repo=repo, session=tech_session, detector_id=det_id, parent=view)
    qtbot.addWidget(dlg_edit)
    dlg_edit.show()
    dlg_edit.edit_model.setText("GC2018-V2")
    dlg_edit.btn_save.click()
    dlg_edit.close()

    # Verify update
    upd = repo.get_by_id(det_id)
    assert upd["model"] == "GC2018-V2"

    # Test Validation Failure in Add Dialog
    dlg_invalid = AddEditDetectorDialog(repo=repo, session=tech_session, parent=view)
    dlg_invalid.show()
    dlg_invalid.edit_code.setText("")  # Empty code
    dlg_invalid.btn_save.click()  # Should not raise exception, displays error
    dlg_invalid.close()

    # Retire
    view.table.setCurrentCell(0, 0)
    view._on_retire_detector()
    assert repo.get_by_id(det_id)["status"] == "RETIRED"


def test_instruments_view_full_workflow(qtbot, db_conn, tech_session):
    repo = InstrumentRepository(db_conn)
    inst_id = repo.create(Instrument(
        instrument_type=InstrumentType.HV_SUPPLY,
        manufacturer="ORTEC",
        model="659",
        serial_number="SN-INST-WF1",
        facility_name="معمل بيتا",
        ripple_mv=4.5,
        notes="فحص أولي",
    ), tech_session)

    view = InstrumentsView(conn=db_conn, session=tech_session)
    qtbot.addWidget(view)
    view.show()
    assert view.isVisible()

    # Filters
    view.edit_search.setText("659")
    assert view.table.rowCount() == 1
    view.edit_search.clear()

    view.combo_facility.setCurrentText("معمل بيتا")
    assert view.table.rowCount() == 1

    # Select row 0
    view.table.setCurrentCell(0, 0)

    # Details Dialog & Edit Dialog via view handlers
    view._on_view_details()
    view._on_edit_instrument()

    # Direct edit
    dlg_edit = AddEditInstrumentDialog(repo=repo, session=tech_session, instrument_id=inst_id, parent=view)
    qtbot.addWidget(dlg_edit)
    dlg_edit.show()
    dlg_edit.edit_model.setText("659-UPGRADED")
    dlg_edit.btn_save.click()
    dlg_edit.close()

    assert repo.get_by_id(inst_id)["model"] == "659-UPGRADED"

    # Validation failure in Add Dialog
    dlg_invalid = AddEditInstrumentDialog(repo=repo, session=tech_session, parent=view)
    dlg_invalid.show()
    dlg_invalid.edit_mfr.setText("")  # Missing manufacturer
    dlg_invalid.btn_save.click()
    dlg_invalid.close()

    # Retire
    view.table.setCurrentCell(0, 0)
    view._on_retire_instrument()
    assert repo.get_by_id(inst_id)["status"] == "RETIRED"


def test_sources_view_full_workflow(qtbot, db_conn, tech_session):
    repo = SourceRepository(db_conn)
    src_id = repo.create(RadioactiveSource(
        source_code="SRC-WF-01",
        serial_number="SN-SRC-WF1",
        isotopes=[SourceIsotope(nuclide_code="Cs-137", cert_activity_bq=37000.0, cert_date=date(2025, 1, 1))],
        facility_name="معمل غاما",
    ), tech_session)

    view = SourcesView(conn=db_conn, session=tech_session)
    qtbot.addWidget(view)
    view.show()
    assert view.isVisible()

    # Filters
    view.edit_search.setText("SRC-WF")
    assert view.table.rowCount() == 1
    view.edit_search.clear()

    # Select row 0
    view.table.setCurrentCell(0, 0)

    # Details Dialog & Edit Dialog via view handlers
    view._on_view_details()
    view._on_edit_source()

    # Direct edit
    dlg_edit = AddEditSourceDialog(repo=repo, session=tech_session, source_id=src_id, parent=view)
    qtbot.addWidget(dlg_edit)
    dlg_edit.show()
    dlg_edit.edit_notes.setText("تعديل ملاحظة المصدر")
    dlg_edit.btn_save.click()
    dlg_edit.close()

    assert repo.get_by_id(src_id)["notes"] == "تعديل ملاحظة المصدر"

    # Test Add Dialog auto half-life and unit conversion
    dlg_add = AddEditSourceDialog(repo=repo, session=tech_session, parent=view)
    dlg_add.show()

    # Test unit kBq
    edit_act = dlg_add.tbl_isotopes.cellWidget(0, 1)
    combo_unit = dlg_add.tbl_isotopes.cellWidget(0, 2)
    idx_kbq = combo_unit.findData("kBq")
    combo_unit.setCurrentIndex(idx_kbq)
    edit_act.setText("10")
    dlg_add.edit_code.setText("SRC-NEW-KBQ")
    dlg_add.edit_serial.setText("SN-NEW-KBQ")
    dlg_add.btn_save.click()

    # Verify Bq saved was 10,000
    rec = next(r for r in repo.list_all() if r["source_code"] == "SRC-NEW-KBQ")
    assert rec["isotopes"][0]["cert_activity_bq"] == 10000.0

    dlg_add.close()

    # Retire
    view.table.setCurrentCell(0, 0)
    view._on_retire_source()
    assert repo.get_by_id(src_id)["status"] == "RETIRED"


def test_quick_search_results_dialog_multiple_matches(qtbot, db_conn, tech_session):
    from kashif.gui.main_window import QuickSearchResultsDialog
    from kashif.db.repositories import quick_search
    det_repo = DetectorRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)
    src_repo = SourceRepository(db_conn)

    d_id = det_repo.create(HPGeDetector(code="DET-MULTI-1", manufacturer="Ortec", model="M1", serial_number="SN-M1", nominal_voltage=3000), tech_session)
    i_id = inst_repo.create(Instrument(instrument_type=InstrumentType.NIM_BIN, manufacturer="Ortec", model="M1-BIN", serial_number="SN-M2"), tech_session)
    s_id = src_repo.create(RadioactiveSource(
        source_code="SRC-MULTI-1",
        serial_number="SN-M3",
        isotopes=[SourceIsotope(nuclide_code="Co-60", cert_activity_bq=50000, cert_date=date(2025, 1, 1))],
    ), tech_session)

    results = quick_search(db_conn, "MULTI")
    assert len(results) >= 2

    dlg = QuickSearchResultsDialog(results=results, conn=db_conn)
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.isVisible()
    assert dlg.table.rowCount() == len(results)

    # Select and open
    dlg.table.selectRow(0)
    dlg._open_selected()


def test_instrument_detail_dialog_and_edit_full_coverage(qtbot, db_conn, tech_session):
    repo = InstrumentRepository(db_conn)
    inst = Instrument(
        instrument_type=InstrumentType.AMPLIFIER,
        manufacturer="Canberra",
        model="2022",
        serial_number="SN-FULL-INST",
        facility_name="معمل متقدم",
        ripple_mv=3.2,
        regulation_pct=0.005,
        channels_count=4096,
        shaping_range_min_us=1.0,
        shaping_range_max_us=8.0,
        cal_date=date(2025, 1, 1),
        cal_expiry=date(2026, 1, 1),
        notes="جهاز شامل للتغطية",
    )
    inst_id = repo.create(inst, tech_session)
    data = repo.get_by_id(inst_id)

    # Detail dialog
    dlg_detail = InstrumentDetailDialog(data)
    qtbot.addWidget(dlg_detail)
    dlg_detail.show()
    assert dlg_detail.isVisible()
    dlg_detail.close()

    # Edit dialog with full data loading
    dlg_edit = AddEditInstrumentDialog(repo=repo, session=tech_session, instrument_id=inst_id)
    qtbot.addWidget(dlg_edit)
    dlg_edit.show()
    assert dlg_edit.edit_mfr.text() == "Canberra"
    assert dlg_edit.edit_channels.text() == "4096"
    assert dlg_edit.edit_ripple.text() == "3.2"
    assert dlg_edit.edit_regulation.text() == "0.005"

    # Test DuplicateEntityError handling in dialog
    dlg_dup = AddEditInstrumentDialog(repo=repo, session=tech_session)
    qtbot.addWidget(dlg_dup)
    dlg_dup.show()
    idx_amp = dlg_dup.combo_type.findData(InstrumentType.AMPLIFIER.value)
    dlg_dup.combo_type.setCurrentIndex(idx_amp if idx_amp >= 0 else 1)
    dlg_dup.edit_mfr.setText("Canberra")
    dlg_dup.edit_model.setText("2022")
    dlg_dup.edit_serial.setText("SN-FULL-INST")  # Duplicate (Canberra, SN-FULL-INST)
    dlg_dup.btn_save.click()
    assert "مكرر" in dlg_dup.lbl_error.text()
    dlg_dup.close()
    dlg_edit.close()


def test_detector_detail_and_edit_nai_coverage(qtbot, db_conn, tech_session):
    repo = DetectorRepository(db_conn)
    nai = NaIDetector(
        code="NAI-DETAIL-1",
        manufacturer="Bicron",
        model="2M2",
        serial_number="SN-NAI-BICRON",
        nominal_voltage=900,
        nominal_resolution_pct=6.8,
        diameter_inch=2.0,
        height_inch=2.0,
        facility_name="معمل إشعاع",
        notes="كاشف وميضي لاختبار التغطية",
    )
    d_id = repo.create(nai, tech_session)
    data = repo.get_by_id(d_id)

    # Detail dialog
    dlg_det = DetectorDetailDialog(data)
    qtbot.addWidget(dlg_det)
    dlg_det.show()
    assert dlg_det.isVisible()
    dlg_det.close()

    # Edit dialog
    dlg_edit = AddEditDetectorDialog(repo=repo, session=tech_session, detector_id=d_id)
    qtbot.addWidget(dlg_edit)
    dlg_edit.show()
    assert dlg_edit.radio_nai.isChecked()
    dlg_edit.close()

    # Duplicate code error handling
    dlg_dup = AddEditDetectorDialog(repo=repo, session=tech_session)
    qtbot.addWidget(dlg_dup)
    dlg_dup.show()
    dlg_dup.edit_code.setText("NAI-DETAIL-1")  # Duplicate
    dlg_dup.edit_manufacturer.setText("Bicron")
    dlg_dup.edit_model.setText("Other")
    dlg_dup.edit_serial.setText("SN-DIFF")
    dlg_dup.edit_voltage.setText("800")
    dlg_dup.btn_save.click()
    assert "مسجل مسبقاً" in dlg_dup.lbl_error.text()
    dlg_dup.close()


def test_retirement_confirmation_accept_and_reject(qtbot, db_conn, tech_session, monkeypatch):
    """Test actual retirement path: rejection leaves record ACTIVE, acceptance marks RETIRED."""
    from PyQt6.QtWidgets import QMessageBox

    det_repo = DetectorRepository(db_conn)
    d_id = det_repo.create(HPGeDetector(
        code="HPGE-RETIRE-TEST",
        manufacturer="Canberra",
        model="GC1018",
        serial_number="SN-RET-10",
        nominal_voltage=2500,
    ), tech_session)

    view = DetectorsView(conn=db_conn, session=tech_session)
    qtbot.addWidget(view)
    view.show()
    view.table.setCurrentCell(0, 0)

    # 1. User rejects confirmation (clicks No)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    view._on_retire_detector()
    assert det_repo.get_by_id(d_id)["status"] == "ACTIVE"

    # 2. User accepts confirmation (clicks Yes)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    view._on_retire_detector()
    assert det_repo.get_by_id(d_id)["status"] == "RETIRED"


def test_corrupt_date_in_database_graceful_handling(qtbot, db_conn, tech_session, caplog):
    """Corrupt date in database must NOT crash and must show warning '⚠️ بيانات غير صالحة'."""
    cursor = db_conn.cursor()

    # Insert instrument with corrupt calibration date
    cursor.execute("""
    INSERT INTO instruments (
        instrument_type, manufacturer, model, serial_number,
        cal_date, cal_expiry, status, created_at, updated_at
    ) VALUES (
        'AMPLIFIER', 'Canberra', '2022', 'SN-CORRUPT-INST',
        'invalid-cal-date', 'invalid-expiry-date', 'ACTIVE', '2026-01-01', '2026-01-01'
    );
    """)

    # Insert source with corrupt certificate date
    cursor.execute("""
    INSERT INTO sources (source_code, serial_number, is_active, status, created_at, updated_at)
    VALUES ('SRC-CORRUPT-DATE', 'SN-CORRUPT-01', 1, 'ACTIVE', '2026-01-01', '2026-01-01');
    """)
    source_id = cursor.lastrowid
    cursor.execute("""
    INSERT INTO source_isotopes (source_id, nuclide_code, cert_activity_bq, cert_date)
    VALUES (?, 'Cs-137', 50000.0, 'corrupt-cert-date');
    """, (source_id,))
    db_conn.commit()

    # InstrumentsView should render corrupt badge
    inst_view = InstrumentsView(conn=db_conn, session=tech_session)
    qtbot.addWidget(inst_view)
    inst_view.show()

    # Find the row with SN-CORRUPT-INST
    corrupt_row = -1
    for r in range(inst_view.table.rowCount()):
        if inst_view.table.item(r, 4) and inst_view.table.item(r, 4).text() == "SN-CORRUPT-INST":
            corrupt_row = r
            break
    assert corrupt_row >= 0
    badge = inst_view.table.cellWidget(corrupt_row, 6)
    assert badge is not None
    assert "⚠️ بيانات غير صالحة" in badge.text()

    # Dashboard should refresh safely and count corrupt items as needing attention
    win = MainWindow(db_conn=db_conn, session=tech_session)
    qtbot.addWidget(win)
    win.show()
    win.refresh_dashboard()
    assert int(win.card_expired.lbl_count.text()) >= 1
    assert int(win.card_sources.lbl_count.text()) >= 1


def test_calendar_widget_theme_contrast_and_not_black(qtbot):
    """Item 4: QCalendarWidget grab() verifies background is not pure black and text is readable."""
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QCalendarWidget

    for mode in ["light", "dark"]:
        set_mode(mode)
        cal = QCalendarWidget()
        cal.setStyleSheet(get_stylesheet())
        qtbot.addWidget(cal)
        cal.show()
        cal.adjustSize()

        pix = cal.grab()
        assert not pix.isNull()
        img = pix.toImage()
        assert img.width() > 100 and img.height() > 100

        # Sample pixel from center of calendar
        center_color = img.pixelColor(img.width() // 2, img.height() // 2)
        # Background must never be pure black #000000
        assert not (center_color.red() == 0 and center_color.green() == 0 and center_color.blue() == 0), (
            f"Calendar widget background is pure black in {mode} mode!"
        )

        # In light mode background should be bright (e.g. > 150)
        if mode == "light":
            assert center_color.red() > 150 and center_color.green() > 150 and center_color.blue() > 150
        # In dark mode background should be warm dark (#161B22), not #000000
        else:
            assert center_color.red() > 5 and center_color.green() > 5 and center_color.blue() > 5

    # Reset theme to light
    set_mode("light")


def test_add_custom_nuclide_dialog_ui(qtbot, db_conn, tech_session):
    """Item 12-b: UI test for AddCustomNuclideDialog creation workflow."""
    n_repo = NuclideRepository(db_conn)
    mgr_session = Session(user_id=20, username="mgr", full_name="مدير المعمل", role=ROLE_LAB_MANAGER)

    dlg = AddCustomNuclideDialog(repo=n_repo, session=mgr_session)
    qtbot.addWidget(dlg)
    dlg.show()

    dlg.edit_code.setText("Ga-68")
    dlg.edit_name_ar.setText("غاليوم-68")
    dlg.edit_name_en.setText("Gallium-68")
    dlg.edit_hl_val.setText("67.71")
    idx_m = dlg.combo_hl_unit.findData("m")
    dlg.combo_hl_unit.setCurrentIndex(idx_m)
    dlg.edit_reference.setText("DDEP 2024 / NuDat")

    dlg.btn_save.click()

    saved = n_repo.get_by_code("Ga-68")
    assert saved is not None
    assert saved["name_ar"] == "غاليوم-68"
    assert saved["half_life_unit"] == "m"
    assert saved["is_custom"] == 1
    assert saved["confirmed"] == 0
    # 67.71 minutes / 1440 = ~0.04702 days
    assert 0.046 < saved["half_life_days"] < 0.048


def test_instrument_detail_dialog_corrupt_date_badge(qtbot, caplog):
    """Item 1: InstrumentDetailDialog shows '⚠️ بيانات غير صالحة' for corrupt cal_expiry in both light and dark mode."""
    corrupt_data = {
        "id": 999,
        "instrument_type": "AMPLIFIER",
        "manufacturer": "ORTEC",
        "model": "672",
        "serial_number": "SN-CORRUPT-DETAIL",
        "cal_date": "2024-01-01",
        "cal_expiry": "NOT-A-VALID-ISO-DATE",
        "is_in_service": 1,
        "facility_name": "معمل الكواشف",
    }

    for theme in ["light", "dark"]:
        set_mode(theme)
        dlg = InstrumentDetailDialog(corrupt_data)
        dlg.setStyleSheet(get_stylesheet())
        qtbot.addWidget(dlg)
        dlg.show()

        assert hasattr(dlg, "lbl_cal_badge")
        assert dlg.lbl_cal_badge.isVisible()
        assert dlg.lbl_cal_badge.text() == "⚠️ بيانات غير صالحة"
        assert dlg.lbl_cal_badge.property("class") == "badge-expired"
        dlg.close()

    set_mode("light")


def test_calendar_popup_contrast_ratio_verification(qtbot):
    """البند 5: اختبار التباين الحقيقي عبر عيّنة بكسلات grab() لنافذة التقويم المنبثقة بالوضعين.

    منهجية الفحص:
    1. التقاط صورة نقطية للنافذة/العناصر بالوضعين الفاتح والداكن (grab).
    2. فحص المناطق الثلاث الرئيسية:
       - شريط التنقّل وأسهم التنقّل (Nav bar with arrows).
       - صف رأس الأيام (Day names header row).
       - خلايا تواريخ الأيام (Day cells area).
    3. أخذ عيّنة بكسلات من كل منطقة وحساب الإضاءة النسبية (Relative Luminance) للون الأفتح والأغمق.
    4. حساب نسبة التباين وفق معادلة WCAG: CR = (L_lighter + 0.05) / (L_darker + 0.05).
    5. التأكد من أن نسبة التباين >= 3:1 (الحد العملي الأدنى لعيّنات البكسل المعالجة بالتنعيم Antialiasing).
    """
    from PyQt6.QtWidgets import QCalendarWidget, QTableView, QToolButton
    from kashif.gui.theme import relative_luminance

    def calc_cr(color_hex_1: str, color_hex_2: str) -> float:
        l1 = relative_luminance(color_hex_1)
        l2 = relative_luminance(color_hex_2)
        return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

    for mode in ["light", "dark"]:
        set_mode(mode)
        cal = QCalendarWidget()
        cal.setStyleSheet(get_stylesheet())
        qtbot.addWidget(cal)
        cal.show()
        cal.adjustSize()

        # 1. شريط التنقّل وزر السهم (Navigation Button & Arrow)
        prev_btn = cal.findChild(QToolButton, "qt_calendar_prevmonth")
        assert prev_btn is not None and prev_btn.isVisible()
        img_btn = prev_btn.grab().toImage()
        btn_colors = set(img_btn.pixelColor(x, y).name() for x in range(img_btn.width()) for y in range(img_btn.height()))
        lum_btn = [(c, relative_luminance(c)) for c in btn_colors]
        darkest_btn = min(lum_btn, key=lambda x: x[1])[0]
        lightest_btn = max(lum_btn, key=lambda x: x[1])[0]
        cr_nav = calc_cr(darkest_btn, lightest_btn)
        assert cr_nav >= 3.0, f"Navigation arrow contrast {cr_nav:.2f}:1 is below 3:1 in {mode} mode"

        # 2. صف رأس أسماء الأيام (Day names header row - Row 0)
        view = cal.findChild(QTableView, "qt_calendar_calendarview")
        assert view is not None and view.isVisible()
        img_view = view.grab().toImage()
        dpr = img_view.devicePixelRatio()
        row0_h = int((view.rowHeight(0) or 30) * dpr)
        hdr_colors = set(img_view.pixelColor(x, y).name() for x in range(img_view.width()) for y in range(min(row0_h, img_view.height())))
        lum_hdr = [(c, relative_luminance(c)) for c in hdr_colors]
        darkest_hdr = min(lum_hdr, key=lambda x: x[1])[0]
        lightest_hdr = max(lum_hdr, key=lambda x: x[1])[0]
        cr_hdr = calc_cr(darkest_hdr, lightest_hdr)
        assert cr_hdr >= 3.0, f"Day names header contrast {cr_hdr:.2f}:1 is below 3:1 in {mode} mode"

        # 3. خلايا تواريخ الأيام (Day cells grid)
        cell_colors = set(img_view.pixelColor(x, y).name() for x in range(img_view.width()) for y in range(row0_h, img_view.height()))
        lum_cells = [(c, relative_luminance(c)) for c in cell_colors]
        darkest_cells = min(lum_cells, key=lambda x: x[1])[0]
        lightest_cells = max(lum_cells, key=lambda x: x[1])[0]
        cr_cells = calc_cr(darkest_cells, lightest_cells)
        assert cr_cells >= 3.0, f"Day cells contrast {cr_cells:.2f}:1 is below 3:1 in {mode} mode"

        cal.close()

    set_mode("light")


def test_source_dialog_isotopes_table_visible_rows_count(qtbot, db_conn, tech_session):
    """البند 5: التحقق من استيعاب جدول النظائر لصفين مرئيين على الأقل وعزل الاتجاه اللاتيني."""
    from PyQt6.QtWidgets import QHeaderView
    from kashif.gui.bidi import LRI, PDI

    repo = SourceRepository(db_conn)

    for theme in ["light", "dark"]:
        set_mode(theme)
        dlg = AddEditSourceDialog(repo=repo, session=tech_session)
        dlg.setStyleSheet(get_stylesheet())
        qtbot.addWidget(dlg)
        dlg.show()

        # 1. تحقق من الرؤية والحد الأدنى للارتفاع
        assert dlg.tbl_isotopes.isVisible()
        assert dlg.tbl_isotopes.minimumHeight() >= 135

        # 2. تحقق من اختصار عنوان عمود التاريخ (لا قص في النص)
        header_date_text = dlg.tbl_isotopes.horizontalHeaderItem(3).text()
        assert header_date_text == "تاريخ الشهادة (*)"

        # 3. تحقق من وضع تمدد عمود النظير وتناسب الأعمدة
        iso_hdr = dlg.tbl_isotopes.horizontalHeader()
        assert iso_hdr.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
        assert iso_hdr.sectionResizeMode(3) == QHeaderView.ResizeMode.ResizeToContents

        # 4. تحقق من عزل الاتجاه اللاتيني لرمز النظير لمنع التداخل والقص في القائمة
        combo_nuclide = dlg.tbl_isotopes.cellWidget(0, 0)
        assert combo_nuclide is not None
        first_item_text = combo_nuclide.itemText(0)
        assert LRI in first_item_text and PDI in first_item_text

        # 5. إضافة صف ثانٍ والتحقق من أن ارتفاع الجدول يستوعب صفين كاملين على الأقل دون قص
        dlg.btn_add_iso.click()
        assert dlg.tbl_isotopes.rowCount() == 2

        header_h = dlg.tbl_isotopes.horizontalHeader().height() or 34
        row0_h = dlg.tbl_isotopes.rowHeight(0) or 38
        row1_h = dlg.tbl_isotopes.rowHeight(1) or 38
        needed_h = header_h + row0_h + row1_h

        assert dlg.tbl_isotopes.minimumHeight() >= needed_h or dlg.tbl_isotopes.height() >= needed_h

        dlg.close()

    set_mode("light")


def test_theme_calendar_arrow_files_and_status_color(qtbot, monkeypatch):
    """Test theme helper functions: calendar_arrow_files and status_color."""
    import os
    from kashif.gui.theme import calendar_arrow_files, status_color, logo_file, COLORS

    # 1. Existing assets return valid files
    prev_icon, next_icon = calendar_arrow_files()
    assert os.path.exists(prev_icon)
    assert os.path.exists(next_icon)

    # 2. Status colors in both modes
    assert status_color(True, "dark") == "#4ADE80"
    assert status_color(False, "dark") == "#F87171"
    assert status_color(True, "light") == COLORS["success"]
    assert status_color(False, "light") == COLORS["error"]

    # 3. Logo file in both modes
    assert os.path.exists(logo_file("light"))
    assert os.path.exists(logo_file("dark"))

    # 4. Fallback creation branch coverage
    orig_exists = os.path.exists
    monkeypatch.setattr(
        os.path,
        "exists",
        lambda p: False if "cal_arrow" in str(p) else orig_exists(p)
    )
    p1, p2 = calendar_arrow_files()
    assert p1.endswith(".png")
    assert p2.endswith(".png")









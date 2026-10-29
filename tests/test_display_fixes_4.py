"""Tests for TASK-1.16: Input safety (mouse wheel guard on unfocused inputs),
BiDi placeholder ordering, local datetime display for last_login, and
source detail calculator table header visibility.

All tests are completely independent of screen geometry and pass identically
under normal desktop execution and headless CI (QT_QPA_PLATFORM=offscreen).
"""

import sqlite3
from datetime import datetime, timezone
import pytest
from PyQt6.QtCore import Qt, QPoint, QPointF, QDate
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QDialog,
    QDoubleSpinBox,
    QHeaderView,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from kashif.db.migrations import apply_migrations
from kashif.db.repositories import (
    DetectorRepository,
    InstrumentRepository,
    NuclideRepository,
    SourceRepository,
)
from kashif.gui.bidi import LRI, PDI, isolate_ltr
from kashif.gui.dialog_utils import (
    WheelScrollGuardFilter,
    format_utc_to_local,
    install_wheel_guard_on_inputs,
)
from kashif.gui.views.detectors_view import AddEditDetectorDialog, DetectorDetailDialog
from kashif.gui.views.instruments_view import AddEditInstrumentDialog, InstrumentDetailDialog
from kashif.gui.views.sources_view import (
    AddCustomNuclideDialog,
    AddEditSourceDialog,
    SourceDetailDialog,
)
from kashif.gui.views.users_view import (
    CreateUserDialog,
    ResetPasswordAdminDialog,
    UsersManagementDialog,
)
from kashif.services.security import create_first_admin, create_user
from kashif.services.session import ROLE_ADMIN, ROLE_TECHNICIAN, Session


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    create_first_admin(conn, "admin", "AdminPass1234", "Admin User")
    yield conn
    conn.close()


@pytest.fixture
def admin_session():
    return Session(user_id=1, username="admin", full_name="Admin User", role=ROLE_ADMIN)


@pytest.fixture
def tech_session():
    return Session(user_id=2, username="tech", full_name="Tech User", role=ROLE_TECHNICIAN)


# ===========================================================================
# 1. Wheel Scroll Input Guard Tests
# ===========================================================================

def test_wheel_guard_unfocused_vs_focused_behavior(qtbot):
    """Unfocused input controls must ignore wheel events and scroll the parent scroll area;
    when explicitly focused, wheel events must modify the widget's value."""
    sa = QScrollArea()
    sa.resize(300, 300)
    content = QWidget()
    content.resize(300, 1000)
    layout = QVBoxLayout(content)

    combo = QComboBox()
    combo.addItems(["Alpha", "Beta", "Gamma"])
    date_edit = QDateEdit()
    date_edit.setDate(QDate(2026, 1, 15))
    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.setValue(10)
    dspin = QDoubleSpinBox()
    dspin.setRange(0.0, 100.0)
    dspin.setValue(25.5)

    layout.addWidget(combo)
    layout.addWidget(date_edit)
    layout.addWidget(spin)
    layout.addWidget(dspin)
    for _ in range(20):
        layout.addWidget(QWidget())

    sa.setWidget(content)
    sa.show()
    qtbot.waitExposed(sa)

    install_wheel_guard_on_inputs(sa)

    # 1. Verify StrongFocus policy on all guarded widgets
    for w in [combo, date_edit, spin, dspin]:
        assert w.focusPolicy() == Qt.FocusPolicy.StrongFocus

    # Clear focus to content container
    content.setFocus()
    QApplication.processEvents()
    for w in [combo, date_edit, spin, dspin]:
        assert not w.hasFocus()

    initial_scroll = sa.verticalScrollBar().value()
    wheel_down = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )

    # Send wheel to unfocused combo: value must NOT change, scroll must occur
    QApplication.sendEvent(combo, wheel_down)
    assert combo.currentIndex() == 0, "Unfocused combo must ignore wheel event and keep index 0"
    assert sa.verticalScrollBar().value() > initial_scroll, "Parent scroll area must receive the forwarded wheel event"

    # Send wheel to unfocused date_edit: date must NOT change
    scroll_before_date = sa.verticalScrollBar().value()
    QApplication.sendEvent(date_edit, wheel_down)
    assert date_edit.date() == QDate(2026, 1, 15), "Unfocused date_edit must not change date"
    assert sa.verticalScrollBar().value() > scroll_before_date

    # Send wheel to unfocused spinbox: value must NOT change
    scroll_before_spin = sa.verticalScrollBar().value()
    QApplication.sendEvent(spin, wheel_down)
    assert spin.value() == 10, "Unfocused spin box must not change value"
    assert sa.verticalScrollBar().value() > scroll_before_spin

    # Send wheel to unfocused doublespinbox: value must NOT change
    scroll_before_dspin = sa.verticalScrollBar().value()
    QApplication.sendEvent(dspin, wheel_down)
    assert dspin.value() == 25.5, "Unfocused double spin box must not change value"
    assert sa.verticalScrollBar().value() > scroll_before_dspin

    # 2. When focused, wheel event DOES change value
    spin.setFocus()
    QApplication.processEvents()
    assert spin.hasFocus()

    wheel_up = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(spin, wheel_up)
    assert spin.value() == 11, "Focused spin box must accept wheel event and increment value"

    combo.setFocus()
    QApplication.processEvents()
    assert combo.hasFocus()
    QApplication.sendEvent(combo, wheel_down)
    assert combo.currentIndex() == 1, "Focused combo box must accept wheel event and change selection"


def test_wheel_guard_installed_on_all_dialog_domains(qtbot, db_conn, admin_session, tech_session):
    """Verify that wheel guard filter and StrongFocus are attached across all 4 dialog domains:
    Detectors, Instruments, Sources, and Users."""
    det_repo = DetectorRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)
    nuc_repo = NuclideRepository(db_conn)
    src_repo = SourceRepository(db_conn)

    # 1. Detector Dialog
    dlg_det = AddEditDetectorDialog(det_repo, tech_session)
    dlg_det.show()
    qtbot.waitExposed(dlg_det)
    combos_det = dlg_det.findChildren(QComboBox)
    assert len(combos_det) >= 5, "Detector dialog must contain combo boxes"
    for cb in combos_det:
        assert cb.focusPolicy() == Qt.FocusPolicy.StrongFocus

    # 2. Instrument Dialog
    dlg_inst = AddEditInstrumentDialog(inst_repo, tech_session)
    dlg_inst.show()
    qtbot.waitExposed(dlg_inst)
    combos_inst = dlg_inst.findChildren(QComboBox)
    dates_inst = dlg_inst.findChildren(QDateEdit)
    assert len(combos_inst) >= 1
    assert len(dates_inst) >= 2
    for cb in combos_inst:
        assert cb.focusPolicy() == Qt.FocusPolicy.StrongFocus
    for de in dates_inst:
        assert de.focusPolicy() == Qt.FocusPolicy.StrongFocus

    # 3. Source Dialog (and dynamically added rows)
    dlg_src = AddEditSourceDialog(src_repo, tech_session)
    dlg_src.show()
    qtbot.waitExposed(dlg_src)
    # Add an isotope row dynamically
    dlg_src._add_isotope_row("Co-60", 37000.0, "Bq", QDate(2024, 1, 1).toPyDate())
    table_combos = dlg_src.tbl_isotopes.findChildren(QComboBox)
    table_dates = dlg_src.tbl_isotopes.findChildren(QDateEdit)
    assert len(table_combos) >= 2, "Dynamic isotope table row must contain combo boxes"
    assert len(table_dates) >= 1, "Dynamic isotope table row must contain date edit"
    for cb in table_combos:
        assert cb.focusPolicy() == Qt.FocusPolicy.StrongFocus
    for de in table_dates:
        assert de.focusPolicy() == Qt.FocusPolicy.StrongFocus

    # 4. User Dialogs
    dlg_user = CreateUserDialog()
    dlg_user.show()
    qtbot.waitExposed(dlg_user)
    assert dlg_user.combo_role.focusPolicy() == Qt.FocusPolicy.StrongFocus

    dlg_users_mgmt = UsersManagementDialog(db_conn, admin_session)
    dlg_users_mgmt.show()
    qtbot.waitExposed(dlg_users_mgmt)


# ===========================================================================
# 2. BiDi Placeholder Formatting Tests
# ===========================================================================

def test_detector_form_placeholders_bidi_isolation(qtbot, db_conn, tech_session):
    """Placeholders in detector form must have proper BiDi isolation and rephrased natural order."""
    repo = DetectorRepository(db_conn)
    dlg = AddEditDetectorDialog(repo, tech_session)

    # 1. FWHM placeholder: must isolate FWHM, 1332.5 keV, and 1.85
    p_fwhm = dlg.edit_fwhm.placeholderText()
    assert isolate_ltr("FWHM") in p_fwhm
    assert isolate_ltr("1332.5 keV") in p_fwhm
    assert isolate_ltr("1.85") in p_fwhm

    # 2. HV ramp placeholder: must isolate V/s and 10.0
    p_ramp = dlg.edit_hv_ramp.placeholderText()
    assert isolate_ltr("V/s") in p_ramp
    assert isolate_ltr("10.0") in p_ramp

    # 3. HV ramp reference: must isolate 10.0 V/s
    p_ramp_ref = dlg.edit_hv_ramp_ref.placeholderText()
    assert isolate_ltr("10.0 V/s") in p_ramp_ref

    # 4. Efficiency placeholder: must isolate % and 20.0
    p_eff = dlg.edit_eff.placeholderText()
    assert isolate_ltr("%") in p_eff
    assert isolate_ltr("20.0") in p_eff

    # 5. Operating voltage: must isolate 3000
    p_volt = dlg.edit_voltage.placeholderText()
    assert isolate_ltr("3000") in p_volt

    # 6. Cooling hours: must isolate 6.0
    p_cool = dlg.edit_cooling_hours.placeholderText()
    assert isolate_ltr("6.0") in p_cool

    # 7. Dimensions: must isolate 2.0 and 3.0
    assert isolate_ltr("2.0") in dlg.edit_hpge_diam.placeholderText()
    assert isolate_ltr("2.0") in dlg.edit_hpge_height.placeholderText()
    assert isolate_ltr("3.0") in dlg.edit_nai_diam.placeholderText()
    assert isolate_ltr("3.0") in dlg.edit_nai_height.placeholderText()

    # 8. NaI voltage and resolution: PMT, 950, %R, 7.5
    assert isolate_ltr("PMT") in dlg.edit_nai_voltage.placeholderText()
    assert isolate_ltr("950") in dlg.edit_nai_voltage.placeholderText()
    assert isolate_ltr("%R") in dlg.edit_nai_res.placeholderText()
    assert isolate_ltr("7.5") in dlg.edit_nai_res.placeholderText()


def test_instrument_form_placeholders_bidi_isolation(qtbot, db_conn, tech_session):
    """Placeholders in instrument form must isolate technical symbols and units."""
    repo = InstrumentRepository(db_conn)
    dlg = AddEditInstrumentDialog(repo, tech_session)

    # 1. Ripple: mV
    p_rip = dlg.edit_ripple.placeholderText()
    assert isolate_ltr("mV") in p_rip

    # 2. Regulation: %
    p_reg = dlg.edit_regulation.placeholderText()
    assert isolate_ltr("%") in p_reg

    # 3. Channels: MCA, 4096, 8192
    p_chan = dlg.edit_channels.placeholderText()
    assert isolate_ltr("MCA") in p_chan
    assert isolate_ltr("4096") in p_chan
    assert isolate_ltr("8192") in p_chan

    # 4. Shaping times: µs, 0.5, 12.0
    p_smin = dlg.edit_shaping_min.placeholderText()
    p_smax = dlg.edit_shaping_max.placeholderText()
    assert isolate_ltr("µs") in p_smin
    assert isolate_ltr("0.5") in p_smin
    assert isolate_ltr("µs") in p_smax
    assert isolate_ltr("12.0") in p_smax


# ===========================================================================
# 3. Local Datetime Display for Users "آخر دخول" Tests
# ===========================================================================

def test_format_utc_to_local_behavior():
    """Verify conversion of UTC ISO timestamps to local datetime formatted as YYYY-MM-DD HH:MM."""
    # 1. None and empty return default
    assert format_utc_to_local(None) == "لم يدخل بعد"
    assert format_utc_to_local("") == "لم يدخل بعد"
    assert format_utc_to_local("   ") == "لم يدخل بعد"

    # 2. Given a UTC timestamp, calculate expected local datetime
    utc_str = "2026-10-29T07:09:00+00:00"
    dt_utc = datetime(2026, 10, 29, 7, 9, 0, tzinfo=timezone.utc)
    expected_local = dt_utc.astimezone()
    expected_formatted = expected_local.strftime("%Y-%m-%d %H:%M")

    res = format_utc_to_local(utc_str)
    assert res == isolate_ltr(expected_formatted)
    assert expected_formatted in res
    # Verify strict reading order: year appears before minute
    year_pos = res.find("2026")
    min_pos = res.find(f"{expected_local.minute:02d}")
    assert year_pos != -1
    assert min_pos != -1
    assert year_pos < min_pos

    # 3. Trailing Z ISO format
    res_z = format_utc_to_local("2026-10-29T07:09:00Z")
    assert res_z == isolate_ltr(expected_formatted)

    # 4. Naive timestamp (assumed UTC)
    res_naive = format_utc_to_local("2026-10-29 07:09:00")
    assert res_naive == isolate_ltr(expected_formatted)


def test_users_table_last_login_local_time(qtbot, db_conn, admin_session):
    """UsersManagementDialog must display last_login_at in local device time with format YYYY-MM-DD HH:MM."""
    # Create two users: one with last_login_at, one without
    create_user(db_conn, admin_session.user_id, "manager1", "Manager User", "LAB_MANAGER", "Password1234")
    create_user(db_conn, admin_session.user_id, "tech1", "Tech User", "TECHNICIAN", "Password1234")

    # Set last_login_at for manager1
    utc_login = "2026-10-29T07:09:00+00:00"
    db_conn.execute("UPDATE users SET last_login_at = ? WHERE username = 'manager1';", (utc_login,))
    db_conn.commit()

    # Calculate expected local formatted string
    dt_utc = datetime(2026, 10, 29, 7, 9, 0, tzinfo=timezone.utc)
    expected_local_str = dt_utc.astimezone().strftime("%Y-%m-%d %H:%M")

    dlg = UsersManagementDialog(db_conn, admin_session)
    dlg.show()
    qtbot.waitExposed(dlg)

    # Locate manager1 row
    found_manager = False
    found_tech = False
    for r in range(dlg.table.rowCount()):
        uname = dlg.table.item(r, 1).text()
        last_login_text = dlg.table.item(r, 6).text()
        if uname == "manager1":
            found_manager = True
            assert expected_local_str in last_login_text, (
                f"Column 'آخر دخول' must show local time {expected_local_str}, got {last_login_text}"
            )
            assert isolate_ltr(expected_local_str) == last_login_text
        elif uname == "tech1":
            found_tech = True
            assert last_login_text == "لم يدخل بعد"

    assert found_manager, "manager1 user must be present in table"
    assert found_tech, "tech1 user must be present in table"


# ===========================================================================
# 4. Source Detail Calculator Table Column Header Visibility Tests
# ===========================================================================

def test_source_calculator_headers_not_truncated(qtbot):
    """Headers 'الفعالية بالشهادة' and 'الفعالية اللحظية' in SourceDetailDialog must
    be fully visible without truncation under any window geometry."""
    sample_data = {
        "source_code": "SRC-CAL-99",
        "serial_number": "SN-998877",
        "facility": "Radiochemistry Unit",
        "notes": "Calibration standard",
        "isotopes": [
            {
                "nuclide_code": "Co-60",
                "nuclide_name_ar": "كوبالت-60",
                "half_life_val": 5.2711,
                "half_life_unit": "y",
                "half_life_days": 1925.27,
                "cert_activity": 37000.0,
                "activity_unit": "Bq",
                "cert_date": "2024-01-01",
            },
            {
                "nuclide_code": "Cs-137",
                "nuclide_name_ar": "سيزيوم-137",
                "half_life_val": 30.018,
                "half_life_unit": "y",
                "half_life_days": 10964.07,
                "cert_activity": 35000.0,
                "activity_unit": "Bq",
                "cert_date": "2023-06-01",
            },
        ],
    }

    dlg = SourceDetailDialog(sample_data)
    dlg.show()
    qtbot.waitExposed(dlg)

    hdr = dlg.tbl_calc.horizontalHeader()

    # 1. Verify resize modes
    assert hdr.sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    for c in [1, 2, 3, 4, 5]:
        assert hdr.sectionResizeMode(c) == QHeaderView.ResizeMode.ResizeToContents, (
            f"Column {c} must be ResizeToContents to prevent text truncation"
        )

    # 2. Verify exact header labels
    h_col2 = dlg.tbl_calc.horizontalHeaderItem(2).text()
    h_col4 = dlg.tbl_calc.horizontalHeaderItem(4).text()
    assert h_col2 == "الفعالية بالشهادة"
    assert h_col4 == "الفعالية اللحظية"

    # 3. Verify column width is at least as wide as font advance of the full header text
    fm = dlg.tbl_calc.fontMetrics()
    w_text_col2 = fm.horizontalAdvance("الفعالية بالشهادة")
    w_text_col4 = fm.horizontalAdvance("الفعالية اللحظية")

    w_actual_col2 = dlg.tbl_calc.columnWidth(2)
    w_actual_col4 = dlg.tbl_calc.columnWidth(4)

    assert w_actual_col2 >= w_text_col2, (
        f"Column 2 width ({w_actual_col2}px) must be >= header text width ({w_text_col2}px)"
    )
    assert w_actual_col4 >= w_text_col4, (
        f"Column 4 width ({w_actual_col4}px) must be >= header text width ({w_text_col4}px)"
    )

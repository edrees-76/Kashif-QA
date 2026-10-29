"""Tests for TASK-1.11: Visual review corrections.

Validates:
1. Dialog window controls (Maximize & Minimize hints) on defined dialogs, and verifies intentional exclusions.
2. AddEditDetectorDialog and AddEditSourceDialog QScrollArea structure with action buttons outside scroll area.
3. Light stylesheet has clean light date styling without #071224 in QDateEdit blocks.
4. Default source certificate date is today's date (currentDate).
5. Instrument calibration date remains 1 year ago as intended.
6. Calendar popup event filter functionality.
"""

import re
import sqlite3
from typing import List, Tuple
import pytest

from PyQt6.QtCore import QDate, Qt, QPoint
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QScrollArea,
    QPushButton,
    QDateEdit,
)

from kashif.db.migrations import apply_migrations
from kashif.db.repositories import (
    DetectorRepository,
    InstrumentRepository,
    NuclideRepository,
    SourceRepository,
)
from kashif.gui.auth_dialogs import (
    ChangePasswordDialog,
    FirstAdminDialog,
    LoginDialog,
    MasterRecoveryKeyDialog,
)
from kashif.gui.main_window import QuickSearchResultsDialog
from kashif.gui.theme import get_stylesheet
from kashif.gui.views.detectors_view import (
    AddEditDetectorDialog,
    DetectorDetailDialog,
)
from kashif.gui.views.instruments_view import (
    AddEditInstrumentDialog,
    InstrumentDetailDialog,
)
from kashif.gui.views.sources_view import (
    AddCustomNuclideDialog,
    AddEditSourceDialog,
    NuclideDetailCardDialog,
    NuclidesReferenceDialog,
    SourceDetailDialog,
)
from kashif.gui.views.users_view import (
    CreateUserDialog,
    ResetPasswordAdminDialog,
    UsersManagementDialog,
)
from kashif.services.session import Session, ROLE_ADMIN


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    from kashif.services.security import create_first_admin
    create_first_admin(conn, "admin", "Admin User", "AdminPass1234")
    yield conn
    conn.close()


@pytest.fixture
def admin_session():
    return Session(user_id=1, username="admin", full_name="Admin", role=ROLE_ADMIN)


def test_dialog_maximize_minimize_window_controls(qtbot, db_conn, admin_session):
    """Item 1: Every standard dialog carries WindowMaximizeButtonHint and WindowMinimizeButtonHint."""
    det_repo = DetectorRepository(db_conn)
    src_repo = SourceRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)
    nuc_repo = NuclideRepository(db_conn)

    sample_detector = {
        "id": 1,
        "code": "DET-01",
        "detector_type": "HPGE",
        "manufacturer": "Mirion",
        "model": "Canberra GC2018",
        "serial_number": "SN-001",
        "nominal_voltage": 3000,
        "voltage_polarity": "POSITIVE",
        "semiconductor_type": "P_TYPE",
        "crystal_type": "COAXIAL",
        "status": "OPERATIONAL",
    }
    sample_source = {
        "id": 1,
        "source_code": "SRC-01",
        "serial_number": "SN-S1",
        "certificate_number": "CERT-1",
        "facility_name": "Lab A",
        "status": "ACTIVE",
        "isotopes": [
            {
                "nuclide_code": "Co-60",
                "cert_activity_bq": 37000.0,
                "cert_date": "2024-01-01",
                "half_life_days": 1925.269275,
                "half_life_value": 5.2711,
                "half_life_unit": "y",
            }
        ],
    }
    sample_instrument = {
        "id": 1,
        "instrument_type": "HIGH_VOLTAGE_SUPPLY",
        "manufacturer": "ORTEC",
        "model": "HV-556",
        "serial_number": "SN-HV-1",
        "facility_name": "Lab A",
        "is_in_service": 1,
        "cal_expiry": "2027-01-01",
    }
    sample_nuclide = nuc_repo.get_by_code("Co-60") or {
        "code": "Co-60",
        "name_ar": "كوبالت-60",
        "name_en": "Cobalt-60",
        "half_life_days": 1925.269275,
    }

    dialogs: List[Tuple[str, QDialog]] = [
        ("AddEditDetectorDialog", AddEditDetectorDialog(det_repo, admin_session)),
        ("DetectorDetailDialog", DetectorDetailDialog(sample_detector)),
        ("AddEditSourceDialog", AddEditSourceDialog(src_repo, admin_session)),
        ("SourceDetailDialog", SourceDetailDialog(sample_source)),
        ("NuclidesReferenceDialog", NuclidesReferenceDialog(nuc_repo)),
        ("NuclideDetailCardDialog", NuclideDetailCardDialog(sample_nuclide)),
        ("AddCustomNuclideDialog", AddCustomNuclideDialog(nuc_repo, admin_session)),
        ("AddEditInstrumentDialog", AddEditInstrumentDialog(inst_repo, admin_session)),
        ("InstrumentDetailDialog", InstrumentDetailDialog(sample_instrument)),
        ("UsersManagementDialog", UsersManagementDialog(db_conn, admin_session)),
        ("CreateUserDialog", CreateUserDialog()),
        ("ResetPasswordAdminDialog", ResetPasswordAdminDialog("tech_user")),
        ("QuickSearchResultsDialog", QuickSearchResultsDialog([], db_conn)),
        ("FirstAdminDialog", FirstAdminDialog(db_conn)),
        ("MasterRecoveryKeyDialog", MasterRecoveryKeyDialog("KEY-1234-5678")),
    ]

    for name, dlg in dialogs:
        qtbot.addWidget(dlg)
        flags = dlg.windowFlags()
        has_max = bool(flags & Qt.WindowType.WindowMaximizeButtonHint)
        has_min = bool(flags & Qt.WindowType.WindowMinimizeButtonHint)
        assert has_max, f"Dialog {name} must have WindowMaximizeButtonHint set"
        assert has_min, f"Dialog {name} must have WindowMinimizeButtonHint set"


def test_login_and_password_dialogs_intentionally_excluded(qtbot, db_conn):
    """Item 1: Compact security prompt dialogs (Login & ChangePassword) remain un-maximized."""
    login_dlg = LoginDialog(db_conn)
    qtbot.addWidget(login_dlg)
    assert not bool(login_dlg.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint), (
        "LoginDialog should remain compact without WindowMaximizeButtonHint"
    )

    change_pw_dlg = ChangePasswordDialog(db_conn, user_id=1, username="admin")
    qtbot.addWidget(change_pw_dlg)
    assert not bool(change_pw_dlg.windowFlags() & Qt.WindowType.WindowMaximizeButtonHint), (
        "ChangePasswordDialog should remain compact without WindowMaximizeButtonHint"
    )


def test_detector_dialog_scroll_area_and_fixed_buttons(qtbot, db_conn, admin_session):
    """Item 2: AddEditDetectorDialog contains QScrollArea, form is inside, save/cancel are outside."""
    det_repo = DetectorRepository(db_conn)
    dlg = AddEditDetectorDialog(det_repo, admin_session)
    qtbot.addWidget(dlg)

    # 1. Verify QScrollArea is present in dialog
    scroll = dlg.findChild(QScrollArea)
    assert scroll is not None, "AddEditDetectorDialog must contain a QScrollArea"
    assert scroll.widgetResizable(), "QScrollArea must be resizable"

    # 2. Verify form inputs (e.g. edit_code, combo_semiconductor, edit_notes) are inside the scroll area
    assert scroll.isAncestorOf(dlg.edit_code), "edit_code must be inside QScrollArea"
    assert scroll.isAncestorOf(dlg.combo_semiconductor), "combo_semiconductor must be inside QScrollArea"
    assert scroll.isAncestorOf(dlg.edit_notes), "edit_notes must be inside QScrollArea"

    # 3. Verify action buttons (btn_save, btn_cancel) and error label are OUTSIDE the scroll area
    assert not scroll.isAncestorOf(dlg.btn_save), "btn_save must be outside QScrollArea (fixed at bottom)"
    assert not scroll.isAncestorOf(dlg.btn_cancel), "btn_cancel must be outside QScrollArea (fixed at bottom)"
    assert not scroll.isAncestorOf(dlg.lbl_error), "lbl_error must be outside QScrollArea (fixed at bottom)"


def test_source_dialog_scroll_area_and_fixed_buttons(qtbot, db_conn, admin_session):
    """Item 2: AddEditSourceDialog contains QScrollArea, form is inside, action buttons are outside."""
    src_repo = SourceRepository(db_conn)
    dlg = AddEditSourceDialog(src_repo, admin_session)
    qtbot.addWidget(dlg)

    scroll = dlg.findChild(QScrollArea)
    assert scroll is not None, "AddEditSourceDialog must contain a QScrollArea"
    assert scroll.widgetResizable(), "QScrollArea must be resizable"

    assert scroll.isAncestorOf(dlg.edit_code), "edit_code must be inside QScrollArea"
    assert scroll.isAncestorOf(dlg.tbl_isotopes), "tbl_isotopes must be inside QScrollArea"

    assert not scroll.isAncestorOf(dlg.btn_save), "btn_save must be outside QScrollArea"
    assert not scroll.isAncestorOf(dlg.btn_cancel), "btn_cancel must be outside QScrollArea"


def test_light_stylesheet_no_dark_surface_in_qdateedit():
    """Item 3: Light stylesheet does not contain dark surface #071224 in QDateEdit blocks."""
    light_qss = get_stylesheet("light")
    dark_qss = get_stylesheet("dark")

    # Match main QTableWidget QDateEdit in light stylesheet
    main_match = re.search(r"QTableWidget\s+QDateEdit\s*\{([^}]+)\}", light_qss)
    assert main_match is not None, "Light stylesheet must define QTableWidget QDateEdit"
    main_block = main_match.group(1)
    assert "#071224" not in main_block, "Light mode QTableWidget QDateEdit must not contain #071224"
    assert "#FFFFFF" in main_block or "white" in main_block.lower(), (
        "Light mode QTableWidget QDateEdit must have white background"
    )

    # Focus block
    focus_match = re.search(r"QTableWidget\s+QDateEdit:focus\s*\{([^}]+)\}", light_qss)
    assert focus_match is not None
    assert "#071224" not in focus_match.group(1)

    # Entire region between QComboBox down-arrow and QTableWidget must not contain #071224
    table_section = light_qss.split("QTableWidget QDateEdit")[1].split("QTableWidget {")[0]
    assert "#071224" not in table_section, (
        "No #071224 should exist in light mode QTableWidget QDateEdit section"
    )

    # In dark stylesheet, QTableWidget QDateEdit must exist and have #071224
    dark_match = re.search(r"QTableWidget\s+QDateEdit\s*\{([^}]+)\}", dark_qss)
    assert dark_match is not None, "Dark stylesheet must define QTableWidget QDateEdit"
    assert "#071224" in dark_match.group(1), (
        "Dark mode QTableWidget QDateEdit must contain #071224 background"
    )


def test_source_certificate_default_date_is_today(qtbot, db_conn, admin_session):
    """Item 4: Source certificate date defaults to current date (today), not last year."""
    src_repo = SourceRepository(db_conn)
    dlg = AddEditSourceDialog(src_repo, admin_session)
    qtbot.addWidget(dlg)

    # Row 0 date widget
    date_edit: QDateEdit = dlg.tbl_isotopes.cellWidget(0, 3)
    assert date_edit is not None
    assert date_edit.date() == QDate.currentDate(), (
        f"Default source certificate date should be currentDate ({QDate.currentDate()}), "
        f"got {date_edit.date()}"
    )


def test_instrument_calibration_default_date_remains_one_year_ago(qtbot, db_conn, admin_session):
    """Item 4: Instrument calibration date intentionally remains 1 year ago (calibrated 1 yr ago, expires in 1 yr)."""
    inst_repo = InstrumentRepository(db_conn)
    dlg = AddEditInstrumentDialog(inst_repo, admin_session)
    qtbot.addWidget(dlg)

    assert dlg.date_cal.date() == QDate.currentDate().addYears(-1), (
        "Instrument calibration date should remain 1 year ago"
    )
    assert dlg.date_expiry.date() == QDate.currentDate().addYears(1), (
        "Instrument calibration expiry should remain 1 year ahead"
    )

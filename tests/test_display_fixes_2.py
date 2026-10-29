"""Tests for TASK-1.13: Display fixes in cards and responsive dialog button layout.

Covers:
1. R-SRC-01 badge in SourceDetailDialog decay table (full text, BiDi isolation with isolate_ltr,
   and QHeaderView.ResizeMode.ResizeToContents on column 5).
2. Reference source label ('المصدر المرجعي المعتمد') in NuclideDetailCardDialog
   having word wrap enabled (setWordWrap(True)).
3. Save and Cancel action buttons on dialog maximize/wide screens constrained to
   readable content width (<=1000px) and centered horizontally, with safe fallback
   for dialogs without such buttons.
"""

import sqlite3
import pytest
from datetime import datetime, timezone
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QHeaderView, QLabel, QPushButton, QDialog, QVBoxLayout

from kashif.db.migrations import apply_migrations
from kashif.db.repositories import (
    DetectorRepository,
    InstrumentRepository,
    NuclideRepository,
    SourceRepository,
)
from kashif.gui.bidi import LRI, PDI, isolate_ltr
from kashif.gui.dialog_utils import (
    ResponsiveButtonContainer,
    adjust_dialog_size_to_screen,
    constrain_dialog_content_width,
)
from kashif.gui.views.detectors_view import AddEditDetectorDialog, DetectorDetailDialog
from kashif.gui.views.instruments_view import AddEditInstrumentDialog, InstrumentDetailDialog
from kashif.gui.views.sources_view import (
    AddCustomNuclideDialog,
    AddEditSourceDialog,
    NuclideDetailCardDialog,
    SourceDetailDialog,
)
from kashif.gui.views.users_view import CreateUserDialog, ResetPasswordAdminDialog
from kashif.services.security import create_first_admin
from kashif.services.session import ROLE_ADMIN, ROLE_TECHNICIAN, Session


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    create_first_admin(conn, "admin", "Admin User", "AdminPass1234")
    yield conn
    conn.close()


@pytest.fixture
def tech_session():
    return Session(user_id=2, username="tech", full_name="Tech User", role=ROLE_TECHNICIAN)


@pytest.fixture
def admin_session():
    return Session(user_id=1, username="admin", full_name="Admin User", role=ROLE_ADMIN)


# ===========================================================================
# 1. R-SRC-01 Badge in SourceDetailDialog
# ===========================================================================

def test_source_detail_dialog_r_src_01_badge_and_column_resize(qtbot):
    """Item 1: R-SRC-01 badge in SourceDetailDialog is fully visible, not truncated,
    uses ResizeToContents on column 5, and isolates R-SRC-01 with isolate_ltr."""
    data = {
        "source_code": "SRC-AM241",
        "serial_number": "SN-9988",
        "certificate_number": "CERT-2020-01",
        "facility_name": "معمل القياسات الإشعاعية",
        "status": "ACTIVE",
        "isotopes": [
            {
                "nuclide_code": "Am-241",
                "name_ar": "أمريسيوم-241",
                "half_life_value": 432.6,
                "half_life_unit": "y",
                "half_life_days": 158000.0,
                "cert_activity_bq": 37000.0,
                "cert_date": "2020-01-01",
            }
        ],
    }

    dlg = SourceDetailDialog(data)
    qtbot.addWidget(dlg)
    dlg.show()
    QApplication.processEvents()

    # Verify column 5 resize mode is ResizeToContents
    col5_mode = dlg.tbl_calc.horizontalHeader().sectionResizeMode(5)
    assert col5_mode == QHeaderView.ResizeMode.ResizeToContents, (
        f"Column 5 resize mode must be ResizeToContents, got {col5_mode}"
    )

    # Verify column 5 header label isolates R-SRC-01
    header_col5 = dlg.tbl_calc.horizontalHeaderItem(5).text()
    expected_isolated_code = isolate_ltr("R-SRC-01")
    assert expected_isolated_code in header_col5, (
        f"Header label must isolate 'R-SRC-01' using isolate_ltr, got {header_col5!r}"
    )

    # Verify cell widget badge text and BiDi isolation
    badge: QLabel = dlg.tbl_calc.cellWidget(0, 5)
    assert badge is not None, "Badge widget in column 5 must exist"
    badge_text = badge.text()

    # Must contain LRI/PDI isolation characters around R-SRC-01
    assert expected_isolated_code in badge_text, (
        f"Badge text must isolate 'R-SRC-01' with isolate_ltr, got {badge_text!r}"
    )
    assert badge_text == f"غير مشمول بقاعدة {isolate_ltr('R-SRC-01')}"
    assert "R-SRC-01" in badge_text

    # Verify column width accommodates badge size hint (no clipping)
    col5_width = dlg.tbl_calc.columnWidth(5)
    assert col5_width >= badge.sizeHint().width(), (
        f"Column 5 width ({col5_width}px) must be >= badge sizeHint ({badge.sizeHint().width()}px)"
    )
    dlg.close()


# ===========================================================================
# 2. Reference Source Word Wrap in NuclideDetailCardDialog
# ===========================================================================

def test_nuclide_detail_card_reference_word_wrap(qtbot):
    """Item 2: 'المصدر المرجعي المعتمد' in NuclideDetailCardDialog has setWordWrap(True)
    so long reference paths and text wrap properly across multiple lines without clipping."""
    long_ref = (
        "docs/tasks.implemented_tasks_summary.md - "
        "Laboratoire National Henri Becquerel (LNHB) Table de Radionucléides / DDEP Reference Data"
    )
    data = {
        "code": "Cs-137",
        "name_ar": "سيزيوم-137",
        "name_en": "Caesium-137",
        "half_life_value": 30.018,
        "half_life_unit": "y",
        "half_life_days": 10964.0745,
        "reference": long_ref,
        "reference_url": "https://www.lnhb.fr/nuclides/Cs-137.pdf",
        "reference_accessed": "2026-01-15",
        "confirmed": 1,
        "is_custom": 0,
    }

    dlg = NuclideDetailCardDialog(data)
    qtbot.addWidget(dlg)
    dlg.show()
    QApplication.processEvents()

    assert hasattr(dlg, "lbl_reference"), "NuclideDetailCardDialog should store lbl_reference"
    lbl_ref = dlg.lbl_reference
    assert isinstance(lbl_ref, QLabel)
    assert lbl_ref.wordWrap() is True, "lbl_reference must have setWordWrap(True)"
    assert long_ref in lbl_ref.text() or "docs/tasks" in lbl_ref.text()
    dlg.close()


# ===========================================================================
# 3. Save & Cancel Buttons Constrained & Centered on Dialog Maximization
# ===========================================================================

def test_save_cancel_buttons_constrained_and_centered_on_maximize(qtbot, db_conn, tech_session):
    """Item 3: In dialogs with Save and Cancel buttons, maximizing the window to 1920px
    constrains the action button container to <= 1000px and centers it horizontally."""
    det_repo = DetectorRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)
    src_repo = SourceRepository(db_conn)
    nuc_repo = NuclideRepository(db_conn)

    dialogs_with_buttons = [
        AddEditDetectorDialog(repo=det_repo, session=tech_session),
        AddEditInstrumentDialog(repo=inst_repo, session=tech_session),
        AddEditSourceDialog(repo=src_repo, session=tech_session),
        AddCustomNuclideDialog(repo=nuc_repo, session=tech_session),
        CreateUserDialog(),
        ResetPasswordAdminDialog("tech"),
    ]

    for dlg in dialogs_with_buttons:
        qtbot.addWidget(dlg)
        dlg.resize(1920, 1080)
        dlg.show()
        QApplication.processEvents()

        # Find the responsive button container
        containers = dlg.findChildren(ResponsiveButtonContainer)
        assert len(containers) >= 1, (
            f"Dialog {type(dlg).__name__} should contain a ResponsiveButtonContainer"
        )
        container = containers[-1]

        # Container width must not exceed 1000px
        assert container.width() <= 1000, (
            f"Button container in {type(dlg).__name__} width ({container.width()}px) must be <= 1000px"
        )

        # Container must be horizontally centered (x > 0 and left/right margins balanced)
        margin_left = container.x()
        margin_right = dlg.width() - (container.x() + container.width())
        assert margin_left > 100, (
            f"Button container in {type(dlg).__name__} must be horizontally centered on 1920px, got x={container.x()}"
        )
        # Margin difference should be small (within 50px due to layout spacing/frames)
        assert abs(margin_left - margin_right) < 80, (
            f"Button container margins should be balanced: left={margin_left}, right={margin_right}"
        )

        # Individual save/cancel buttons must NOT stretch across 1920px
        for btn in container.findChildren(QPushButton):
            assert btn.width() <= 1000, (
                f"Button '{btn.text()}' in {type(dlg).__name__} width ({btn.width()}px) must be <= 1000px"
            )

        dlg.close()


def test_dialogs_without_save_cancel_buttons_unbroken(qtbot):
    """Item 3: Dialogs without Save and Cancel buttons (e.g. detail cards with single close button,
    or dialogs with no buttons) are unbroken and remain fully functional."""
    # Detail dialog with single close button
    dlg_detail = DetectorDetailDialog({"code": "DET-1", "detector_type": "HPGe", "status": "ACTIVE"})
    qtbot.addWidget(dlg_detail)
    dlg_detail.resize(1920, 1080)
    dlg_detail.show()
    QApplication.processEvents()
    assert dlg_detail.isVisible()
    dlg_detail.close()

    # Detail dialog for nuclide
    dlg_nuc = NuclideDetailCardDialog({"code": "Co-60", "name_ar": "كوبالت-60"})
    qtbot.addWidget(dlg_nuc)
    dlg_nuc.resize(1920, 1080)
    dlg_nuc.show()
    QApplication.processEvents()
    assert dlg_nuc.isVisible()
    dlg_nuc.close()

    # Empty dialog without buttons
    dlg_empty = QDialog()
    root = QVBoxLayout(dlg_empty)
    root.addWidget(QLabel("No buttons"))
    adjust_dialog_size_to_screen(dlg_empty, preferred_width=500, preferred_height=400)
    qtbot.addWidget(dlg_empty)
    dlg_empty.show()
    QApplication.processEvents()
    assert dlg_empty.isVisible()
    dlg_empty.close()


def test_constrain_dialog_content_width_idempotent(qtbot, db_conn, tech_session):
    """Item 3: Calling adjust_dialog_size_to_screen / constrain_dialog_content_width multiple times
    is safe and idempotent."""
    det_repo = DetectorRepository(db_conn)
    dlg = AddEditDetectorDialog(repo=det_repo, session=tech_session)
    qtbot.addWidget(dlg)

    # Call multiple times (e.g. triggered on type changes)
    constrain_dialog_content_width(dlg, max_content_width=1000)
    constrain_dialog_content_width(dlg, max_content_width=1000)
    adjust_dialog_size_to_screen(dlg, preferred_width=620, preferred_height=750)

    dlg.resize(1920, 1080)
    dlg.show()
    QApplication.processEvents()

    containers = dlg.findChildren(ResponsiveButtonContainer)
    # Must not nest containers inside containers
    assert len(containers) == 1
    assert containers[0].width() <= 1000
    dlg.close()

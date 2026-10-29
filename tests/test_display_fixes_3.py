"""Tests for TASK-1.15: Fixing dialog content shrinkage on maximize (regression fix from TASK-1.12).

Covers:
1. NuclidesReferenceDialog table width when maximized to 1920x1080 >= 900px (fills up to 1000px and centers).
2. All radionuclides in NuclidesReferenceDialog (including Co-60 and Cs-137) fit completely without clipping or ellipses (...).
3. No dialog shrinks below its natural size when maximized; main content width >= min(1000, dlg.width() - margins).
4. Natural dialog sizes and content widths without maximize remain unaltered across different screen geometries.
"""

import sqlite3
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHeaderView,
    QLabel,
    QScrollArea,
    QTableWidget,
)

from kashif.db.migrations import apply_migrations
from kashif.db.repositories import (
    DetectorRepository,
    InstrumentRepository,
    NuclideRepository,
    SourceRepository,
)
from kashif.gui.main_window import QuickSearchResultsDialog
from kashif.gui.views.detectors_view import AddEditDetectorDialog, DetectorDetailDialog
from kashif.gui.views.instruments_view import AddEditInstrumentDialog, InstrumentDetailDialog
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
from kashif.services.security import create_first_admin
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


def _get_dialog_main_content_widget(dlg: QDialog):
    """Retrieve the main inner content widget for sizing measurements."""
    sa_list = dlg.findChildren(QScrollArea)
    if sa_list and sa_list[0].widget():
        return sa_list[0].widget()

    tbl_list = [w for w in dlg.findChildren(QTableWidget) if w.parent() == dlg]
    if tbl_list:
        return tbl_list[0]

    card_frames = [f for f in dlg.findChildren(QFrame) if f.parent() == dlg and f.property("class") == "card"]
    if card_frames:
        return card_frames[0]

    layout = dlg.layout()
    if layout and layout.count() > 0:
        for i in range(layout.count()):
            item = layout.itemAt(i)
            if item and item.widget():
                return item.widget()

    return dlg


# ===========================================================================
# 1. NuclidesReferenceDialog Table Width >= 900px on 1920x1080 & No Clipping
# ===========================================================================

def test_nuclides_reference_dialog_table_width_and_centering_at_1920(qtbot, db_conn):
    """TASK-1.15 Requirement 4(أ) & 3:
    NuclidesReferenceDialog table width when displayed on 1920x1080 display is >= 900px,
    centered horizontally, and all columns (including Co-60 and Cs-137) fit without '...' truncation.
    """
    nuc_repo = NuclideRepository(db_conn)
    dlg = NuclidesReferenceDialog(nuc_repo)
    qtbot.addWidget(dlg)

    dlg.resize(1920, 1080)
    dlg.show()
    QApplication.processEvents()

    # 1. Table width at 1920 must be >= 900px (capped at 1000px max content width)
    expected_table_min = min(900, dlg.width() - 50)
    assert dlg.table.width() >= expected_table_min, (
        f"NuclidesReferenceDialog table width ({dlg.table.width()}px) must be >= {expected_table_min}px on wide screen"
    )
    assert dlg.table.width() <= 1000, (
        f"NuclidesReferenceDialog table width ({dlg.table.width()}px) must not exceed 1000px"
    )

    # 2. Table must be centered horizontally in the dialog when width allows
    if dlg.width() > 1000 + 40:
        margin_left = dlg.table.x()
        margin_right = dlg.width() - (dlg.table.x() + dlg.table.width())
        assert margin_left > 50, f"Table left margin ({margin_left}px) must be centered, got x={dlg.table.x()}"
        assert abs(margin_left - margin_right) < 80, (
            f"Table horizontal margins should be balanced: left={margin_left}, right={margin_right}"
        )

    # 3. Check column resize modes
    header = dlg.table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents  # الرمز
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.ResizeToContents  # الاسم بالعربية
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.ResizeToContents  # عمر النصف
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.ResizeToContents  # عدم اليقين
    assert header.sectionResizeMode(4) == QHeaderView.ResizeMode.Stretch           # المصدر المرجعي
    assert header.sectionResizeMode(5) == QHeaderView.ResizeMode.ResizeToContents  # حالة التأكيد

    # 4. Check Co-60 and Cs-137 rows are complete with no ellipsis truncation
    found_co60 = False
    found_cs137 = False
    fm = dlg.table.fontMetrics()

    for r in range(dlg.table.rowCount()):
        code_item = dlg.table.item(r, 0)
        if not code_item:
            continue
        code = code_item.text()

        # Check all text cells fit within their column width without truncation
        for c in range(5):
            item = dlg.table.item(r, c)
            if item:
                text = item.text()
                assert "…" not in text, f"Row {r} Col {c} ({text}) must not contain ellipsis"
                assert "..." not in text, f"Row {r} Col {c} ({text}) must not contain dots truncation"
                text_width = fm.horizontalAdvance(text)
                col_width = dlg.table.columnWidth(c)
                assert text_width <= col_width + 10, (
                    f"Text '{text}' width ({text_width}px) exceeds column width ({col_width}px)"
                )

        if code == "Co-60":
            found_co60 = True
            hl_text = dlg.table.item(r, 2).text()
            unc_text = dlg.table.item(r, 3).text()
            ref_text = dlg.table.item(r, 4).text()
            assert "5.2711" in hl_text
            assert "0.0008" in unc_text
            assert "LNHB/DDEP" in ref_text
        elif code == "Cs-137":
            found_cs137 = True
            hl_text = dlg.table.item(r, 2).text()
            unc_text = dlg.table.item(r, 3).text()
            ref_text = dlg.table.item(r, 4).text()
            assert "30.018" in hl_text
            assert "0.022" in unc_text
            assert "LNHB/DDEP" in ref_text

    assert found_co60, "Co-60 must be present in nuclides table"
    assert found_cs137, "Cs-137 must be present in nuclides table"
    dlg.close()


# ===========================================================================
# 2. No Dialog Shrinks Below Natural Size on Maximize
# ===========================================================================

def test_no_dialog_shrinks_below_natural_size_on_maximize(qtbot, db_conn, admin_session):
    """TASK-1.15 Requirement 4(ب) & 2:
    Across all dialogs using adjust_dialog_size_to_screen, when maximized to 1920x1080,
    the main content width does NOT shrink below its natural size, and is >= min(1000, dlg.width() - margins).
    """
    det_repo = DetectorRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)
    src_repo = SourceRepository(db_conn)
    nuc_repo = NuclideRepository(db_conn)

    sample_detector = {
        "id": 1,
        "code": "DET-HPGe-01",
        "name": "كاشف الجرمانيوم",
        "detector_type": "HPGe",
        "crystal_type": "COAXIAL",
        "relative_efficiency": 40.0,
        "resolution_fwhm_kev": 1.85,
        "fwtm_fwhm_ratio": 1.88,
        "operating_voltage": 3000.0,
        "manufacturer": "CANBERRA",
        "model": "GC4018",
        "serial_number": "SN-12345",
        "facility_name": "مختبر القياس",
        "is_in_service": 1,
        "status": "ACTIVE",
    }

    sample_source = {
        "id": 1,
        "source_code": "SRC-CO60-01",
        "source_type": "STANDARD",
        "status": "ACTIVE",
        "facility_name": "مختبر المعايرة",
        "notes": "مصدر معايرة قياسي",
        "isotopes": [
            {
                "nuclide_code": "Co-60",
                "half_life_days": 1925.269275,
                "half_life_value": 5.2711,
                "half_life_unit": "y",
                "cert_activity_bq": 370000.0,
                "cert_date": "2020-01-01",
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

    sample_nuclide = nuc_repo.get_by_code("Co-60")

    dialog_factories = [
        ("AddEditDetectorDialog", lambda: AddEditDetectorDialog(det_repo, session=admin_session)),
        ("DetectorDetailDialog", lambda: DetectorDetailDialog(sample_detector)),
        ("AddEditInstrumentDialog", lambda: AddEditInstrumentDialog(inst_repo, session=admin_session)),
        ("InstrumentDetailDialog", lambda: InstrumentDetailDialog(sample_instrument)),
        ("AddEditSourceDialog", lambda: AddEditSourceDialog(src_repo, session=admin_session)),
        ("SourceDetailDialog", lambda: SourceDetailDialog(sample_source)),
        ("NuclidesReferenceDialog", lambda: NuclidesReferenceDialog(nuc_repo)),
        ("NuclideDetailCardDialog", lambda: NuclideDetailCardDialog(sample_nuclide)),
        ("AddCustomNuclideDialog", lambda: AddCustomNuclideDialog(nuc_repo, admin_session)),
        ("QuickSearchResultsDialog", lambda: QuickSearchResultsDialog([], db_conn)),
        ("CreateUserDialog", lambda: CreateUserDialog()),
        ("ResetPasswordAdminDialog", lambda: ResetPasswordAdminDialog("tech_user")),
        ("UsersManagementDialog", lambda: UsersManagementDialog(db_conn, admin_session)),
    ]

    for name, factory in dialog_factories:
        dlg = factory()
        qtbot.addWidget(dlg)

        # 1. Show at natural / preferred size
        dlg.show()
        QApplication.processEvents()
        w_main_nat = _get_dialog_main_content_widget(dlg)
        nat_content_width = w_main_nat.width()

        # 2. Maximize / resize to 1920x1080
        dlg.resize(1920, 1080)
        QApplication.processEvents()
        w_main_max = _get_dialog_main_content_widget(dlg)
        max_content_width = w_main_max.width()

        # 3. Assert content never shrinks on maximize
        assert max_content_width >= nat_content_width, (
            f"Dialog {name} content shrunk from {nat_content_width}px to {max_content_width}px on maximize!"
        )

        # 4. Assert content width is >= min(1000, dlg.width() - margins)
        expected_min = min(1000, dlg.width() - 50)
        assert max_content_width >= expected_min - 10, (
            f"Dialog {name} content width ({max_content_width}px) is less than expected minimum ({expected_min}px)"
        )
        assert max_content_width <= 1000 or max_content_width == dlg.width(), (
            f"Dialog {name} content width ({max_content_width}px) should be capped at 1000px"
        )

        dlg.close()


# ===========================================================================
# 3. Natural Dialog Size Unaltered Without Maximize
# ===========================================================================

def test_natural_dialog_size_unaltered(qtbot, db_conn, admin_session):
    """TASK-1.15 Requirement 4(ج):
    Natural dialog size and content width without maximize remain unaltered,
    matching their preferred dimensions (screen-bounded) and never collapsing to minimum width.
    """
    nuc_repo = NuclideRepository(db_conn)
    screen = QApplication.primaryScreen()
    avail_w = screen.availableGeometry().width() if screen else 1920
    screen_max_w = int(avail_w * 0.90)

    # 1. NuclidesReferenceDialog preferred size is 850x650
    pref_w_ref = 850
    dlg_ref = NuclidesReferenceDialog(nuc_repo)
    qtbot.addWidget(dlg_ref)
    dlg_ref.show()
    QApplication.processEvents()

    expected_w_ref = max(dlg_ref.minimumWidth(), min(pref_w_ref, screen_max_w))
    assert dlg_ref.width() == expected_w_ref, (
        f"NuclidesReferenceDialog default width should be {expected_w_ref}, got {dlg_ref.width()}"
    )
    # The table width must not collapse and should fill dialog width minus margins:
    expected_table_min = dlg_ref.width() - 50
    assert dlg_ref.table.width() >= expected_table_min, (
        f"Table width at natural dialog size ({dlg_ref.table.width()}px) should be >= {expected_table_min}px, not collapsed!"
    )
    dlg_ref.close()

    # 2. AddEditDetectorDialog preferred size is 620x750
    pref_w_det = 620
    det_repo = DetectorRepository(db_conn)
    dlg_det = AddEditDetectorDialog(det_repo, session=admin_session)
    qtbot.addWidget(dlg_det)
    dlg_det.show()
    QApplication.processEvents()

    expected_w_det = max(dlg_det.minimumWidth(), min(pref_w_det, screen_max_w))
    assert dlg_det.width() == expected_w_det, (
        f"AddEditDetectorDialog default width should be {expected_w_det}, got {dlg_det.width()}"
    )
    sa = dlg_det.findChild(QScrollArea)
    assert sa is not None
    expected_content_min = dlg_det.width() - 60
    assert sa.widget().width() >= expected_content_min, (
        f"Scroll content width ({sa.widget().width()}px) at natural size should be >= {expected_content_min}px"
    )
    dlg_det.close()

    # 3. NuclideDetailCardDialog preferred size is 500x600
    pref_w_card = 500
    sample_nuclide = nuc_repo.get_by_code("Co-60")
    dlg_card = NuclideDetailCardDialog(sample_nuclide)
    qtbot.addWidget(dlg_card)
    dlg_card.show()
    QApplication.processEvents()

    expected_w_card = max(dlg_card.minimumWidth(), min(pref_w_card, screen_max_w))
    assert dlg_card.width() == expected_w_card, (
        f"NuclideDetailCardDialog default width should be {expected_w_card}, got {dlg_card.width()}"
    )
    card = dlg_card.findChild(QFrame, "card") or [
        f for f in dlg_card.findChildren(QFrame) if f.property("class") == "card"
    ][0]
    expected_card_min = dlg_card.width() - 60
    assert card.width() >= expected_card_min, (
        f"Card frame width ({card.width()}px) at natural size should be >= {expected_card_min}px"
    )
    dlg_card.close()

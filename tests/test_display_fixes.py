"""Tests for TASK-1.12: Display corrections (BiDi, Translations, and Layout).

Covers:
1. Activity formatting with multi-isotopes (logical ordering with isolate_ltr and ' + ' outside).
2. Base hint label under source detail card with proper BiDi isolation.
3. Quantity and unit isolation across detector and instrument cards and nuclide cards.
4. Status translation dictionary ("ACTIVE" -> "نشط", "RETIRED" -> "متقاعد", etc.) without modifying DB values.
5. Delete button height and vertical margin in AddEditSourceDialog isotopes table.
6. Maximum content width (<=1000px) and horizontal centering on dialog maximization.
"""

import sqlite3
import pytest
from datetime import date
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtWidgets import QApplication, QLabel, QScrollArea, QPushButton

from kashif.db.migrations import apply_migrations
from kashif.db.repositories import (
    DetectorRepository,
    InstrumentRepository,
    NuclideRepository,
    SourceRepository,
)
from kashif.gui.bidi import (
    LRI,
    PDI,
    STATUS_LABELS_AR,
    format_status_ar,
    isolate_ltr,
    isolate_quantity,
    isolate_rtl,
)
from kashif.gui.dialog_utils import (
    adjust_dialog_size_to_screen,
    constrain_dialog_content_width,
)
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
    AddEditSourceDialog,
    NuclideDetailCardDialog,
    NuclidesReferenceDialog,
    SourceDetailDialog,
    SourcesView,
)
from kashif.services.security import create_first_admin
from kashif.services.session import Session, ROLE_ADMIN, ROLE_TECHNICIAN


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_migrations(conn)
    create_first_admin(conn, "admin", "Admin User", "AdminPass1234")
    yield conn
    conn.close()


@pytest.fixture
def admin_session():
    return Session(user_id=1, username="admin", full_name="Admin User", role=ROLE_ADMIN)


@pytest.fixture
def tech_session():
    return Session(user_id=2, username="tech", full_name="Tech User", role=ROLE_TECHNICIAN)


# ===========================================================================
# 1. BiDi Isolation and Logical Ordering of Activity Cells & Hint Notes
# ===========================================================================

def test_bidi_isolate_quantity_and_rtl_utilities():
    """Verify isolate_quantity formats and wraps value + unit cleanly in LTR isolates."""
    assert isolate_quantity("32.432", "kBq") == f"{LRI}32.432 kBq{PDI}"
    assert isolate_quantity("32.432 kBq") == f"{LRI}32.432 kBq{PDI}"
    assert isolate_quantity(3000, "V") == f"{LRI}3000 V{PDI}"
    assert isolate_quantity("37 kBq ± 50%") == f"{LRI}37 kBq ± 50%{PDI}"
    assert isolate_quantity(None) == "—"
    assert isolate_quantity("") == "—"
    assert isolate_quantity("—") == "—"
    assert isolate_rtl("نص عربي") == f"\u2067نص عربي{PDI}"
    assert isolate_rtl("") == ""
    assert isolate_ltr("") == ""


def test_sources_table_multi_isotope_activity_logical_order(qtbot, db_conn, tech_session):
    """Item 1: Multi-isotope source cell displays activities isolated in LTR with ' + ' outside.
    
    Prevents BiDi scrambling where '32.432 kBq + 82.083 kBq' flipped to 'kBq + 82.083 kBq 32.432'.
    """
    from kashif.core.models import RadioactiveSource, SourceIsotope

    src_repo = SourceRepository(db_conn)
    src = RadioactiveSource(
        source_code="SRC-MULTI-01",
        serial_number="SN-MULTI-01",
        certificate_number="CERT-2025-01",
        facility_name="معمل القياسات الإشعاعية",
        notes="مصدر تدقيق ثنائي النظائر",
        isotopes=[
            SourceIsotope(
                nuclide_code="Co-60",
                cert_activity_bq=32432.0,  # 32.432 kBq
                cert_date=date.today(),
            ),
            SourceIsotope(
                nuclide_code="Cs-137",
                cert_activity_bq=82083.0,  # 82.083 kBq
                cert_date=date.today(),
            ),
        ],
    )
    src_id = src_repo.create(src, session=tech_session)

    view = SourcesView(conn=db_conn, session=tech_session)
    qtbot.addWidget(view)
    view.show()

    assert view.table.rowCount() >= 1
    # Row 0: Certified activity column (Col 4)
    cert_cell_text = view.table.item(0, 4).text()
    expected_cert = f"{LRI}32.432 kBq{PDI} + {LRI}82.083 kBq{PDI}"
    assert cert_cell_text == expected_cert, (
        f"Certified activity text should isolate each quantity with ' + ' between them: got {repr(cert_cell_text)}"
    )

    # Row 0: Current activity column (Col 6)
    curr_cell_text = view.table.item(0, 6).text()
    expected_curr = f"{LRI}32.432 kBq{PDI} + {LRI}82.083 kBq{PDI}"
    assert curr_cell_text == expected_curr, (
        f"Current decay activity text should isolate each quantity with ' + ' between them: got {repr(curr_cell_text)}"
    )


def test_source_detail_hint_note_bidi_isolation(qtbot, db_conn):
    """Item 1: R-SRC-01 hint note under source card correctly isolates '(الفعالية 37 kBq ± 50%)'."""
    dummy_data = {
        "source_code": "SRC-001",
        "serial_number": "SN-001",
        "certificate_number": "CERT-001",
        "status": "ACTIVE",
        "isotopes": [
            {
                "nuclide_code": "Co-60",
                "name_ar": "كوبالت-60",
                "cert_activity_bq": 37000.0,
                "cert_date": date.today().isoformat(),
                "half_life_days": 1925.2,
            }
        ],
    }
    dlg = SourceDetailDialog(dummy_data)
    qtbot.addWidget(dlg)
    dlg.show()

    # Find hint label
    labels = dlg.findChildren(QLabel)
    hint_label = next((lbl for lbl in labels if lbl.property("class") == "hint"), None)
    assert hint_label is not None, "Hint label must exist in SourceDetailDialog"

    hint_text = hint_label.text()
    # Check that '37 kBq ± 50%' is isolated with LRI and PDI
    assert f"{LRI}37 kBq ± 50%{PDI}" in hint_text
    assert f"{LRI}R-SRC-01{PDI}" in hint_text
    assert f"{LRI}Co-60{PDI}" in hint_text
    assert f"{LRI}Cs-137{PDI}" in hint_text


def test_nuclide_detail_card_bidi_quantities(qtbot):
    """Item 1: NuclideDetailCardDialog isolates half-life and uncertainty quantities."""
    nuclide_data = {
        "nuclide_code": "Co-60",
        "name_ar": "كوبالت-60",
        "name_en": "Cobalt-60",
        "half_life_value": 5.2714,
        "half_life_unit": "y",
        "half_life_days": 1925.28,
        "half_life_uncertainty": 0.0005,
        "reference": "LNHB / DDEP",
        "confirmed": 1,
    }
    dlg = NuclideDetailCardDialog(nuclide_data)
    qtbot.addWidget(dlg)
    dlg.show()

    labels = [lbl.text() for lbl in dlg.findChildren(QLabel)]
    # Half-life should contain isolated "5.2714 y"
    assert any(f"{LRI}5.2714 y{PDI}" in txt for txt in labels)
    # Uncertainty should contain isolated "± 0.0005 y"
    assert any(f"{LRI}± 0.0005 y{PDI}" in txt for txt in labels)


def test_detector_detail_dialog_bidi_quantities(qtbot):
    """Item 1: DetectorDetailDialog isolates voltage, ramp rate, FWHM, and efficiency."""
    detector_data = {
        "code": "DET-HPGE-01",
        "detector_type": "HPGE",
        "manufacturer": "Mirion",
        "model": "GC2018",
        "serial_number": "SN-DET-01",
        "nominal_voltage": 3000,
        "voltage_polarity": "POSITIVE",
        "status": "ACTIVE",
        "hv_ramp_max_v_per_s": 15.0,
        "hv_ramp_reference": "Manual Section 4",
        "nominal_fwhm_kev": 1.85,
        "nominal_efficiency_pct": 20.0,
        "beryllium_window_status": "INTACT",
    }
    dlg = DetectorDetailDialog(detector_data)
    qtbot.addWidget(dlg)
    dlg.show()

    labels = [lbl.text() for lbl in dlg.findChildren(QLabel)]
    # Voltage: "3000 V" isolated, polarity in Arabic
    assert any(f"{LRI}3000 V{PDI}" in txt and "موجبة (+)" in txt for txt in labels)
    # HV ramp: "15.0 V/s" isolated
    assert any(f"{LRI}15.0 V/s{PDI}" in txt for txt in labels)
    # FWHM: "1.85 keV" isolated
    assert any(f"{LRI}1.85 keV{PDI}" in txt for txt in labels)
    # Efficiency: "20.0 %" isolated
    assert any(f"{LRI}20.0 %{PDI}" in txt for txt in labels)


def test_instrument_detail_dialog_bidi_quantities(qtbot):
    """Item 1: InstrumentDetailDialog isolates ripple mV, regulation %, and shaping range."""
    inst_data = {
        "instrument_type": "HV_SUPPLY",
        "manufacturer": "Canberra",
        "model": "Model 3106D",
        "serial_number": "SN-HV-01",
        "status": "ACTIVE",
        "ripple_mv": 3.5,
        "regulation_pct": 0.01,
        "channels_count": 8192,
        "shaping_range_min_us": 0.5,
        "shaping_range_max_us": 12.0,
    }
    dlg = InstrumentDetailDialog(inst_data)
    qtbot.addWidget(dlg)
    dlg.show()

    labels = [lbl.text() for lbl in dlg.findChildren(QLabel)]
    assert any(f"{LRI}3.5 mV{PDI}" in txt for txt in labels)
    assert any(f"{LRI}0.01 %{PDI}" in txt for txt in labels)
    assert any(f"{LRI}0.5 – 12.0 µs{PDI}" in txt for txt in labels)


# ===========================================================================
# 2. Status Translations (Single Canonical Dictionary)
# ===========================================================================

def test_status_translation_dictionary():
    """Item 2: Canonical dictionary translates all status codes correctly."""
    assert format_status_ar("ACTIVE") == "نشط"
    assert format_status_ar("active") == "نشط"
    assert format_status_ar("RETIRED") == "متقاعد"
    assert format_status_ar("MAINTENANCE") == "صيانة"
    assert format_status_ar("OPERATIONAL") == "تشغيلي"
    assert format_status_ar("INTACT") == "سليمة"
    assert format_status_ar("SCRATCHED") == "مخدوشة"
    assert format_status_ar("BROKEN") == "مكسورة"
    assert format_status_ar(None) == "—"
    assert format_status_ar("") == "—"
    # Fallback to input string if not in dictionary
    assert format_status_ar("UNKNOWN_STATUS") == "UNKNOWN_STATUS"
    assert format_status_ar("نشط") == "نشط"


def test_source_detail_card_displays_arabic_status(qtbot):
    """Item 2: Source detail card displays 'نشط' instead of English 'ACTIVE'."""
    src_data = {
        "source_code": "SRC-STATUS-01",
        "serial_number": "SN-ST-01",
        "status": "ACTIVE",
        "isotopes": [],
    }
    dlg = SourceDetailDialog(src_data)
    qtbot.addWidget(dlg)
    dlg.show()

    labels = [lbl.text() for lbl in dlg.findChildren(QLabel)]
    assert "نشط" in labels, "Arabic 'نشط' must be displayed in SourceDetailDialog status field"
    assert "ACTIVE" not in labels, "English 'ACTIVE' must NOT be displayed in SourceDetailDialog"


def test_detector_and_instrument_detail_cards_display_arabic_status(qtbot):
    """Item 2: Detector and Instrument detail cards display Arabic status labels."""
    det_data = {
        "code": "DET-01",
        "detector_type": "HPGE",
        "status": "ACTIVE",
        "beryllium_window_status": "INTACT",
    }
    dlg_det = DetectorDetailDialog(det_data)
    qtbot.addWidget(dlg_det)
    dlg_det.show()

    labels_det = [lbl.text() for lbl in dlg_det.findChildren(QLabel)]
    assert "نشط" in labels_det
    assert "ACTIVE" not in labels_det
    assert any("سليمة" in txt for txt in labels_det)

    inst_data = {
        "instrument_type": "AMPLIFIER",
        "model": "AMP-202",
        "status": "ACTIVE",
    }
    dlg_inst = InstrumentDetailDialog(inst_data)
    qtbot.addWidget(dlg_inst)
    dlg_inst.show()

    labels_inst = [lbl.text() for lbl in dlg_inst.findChildren(QLabel)]
    assert "نشط" in labels_inst
    assert "ACTIVE" not in labels_inst


# ===========================================================================
# 3. Source Isotopes Table Delete Button Height & Margins
# ===========================================================================

def test_source_dialog_isotopes_table_delete_buttons_spacing(qtbot, db_conn, tech_session):
    """Item 3: Adjacent delete buttons have proper height and positive gap without colliding."""
    repo = SourceRepository(db_conn)
    dlg = AddEditSourceDialog(repo=repo, session=tech_session)
    qtbot.addWidget(dlg)
    dlg.show()

    # Add second isotope row
    dlg.btn_add_iso.click()
    QApplication.processEvents()
    assert dlg.tbl_isotopes.rowCount() == 2

    # Verify row height
    row0_h = dlg.tbl_isotopes.rowHeight(0)
    row1_h = dlg.tbl_isotopes.rowHeight(1)
    assert row0_h >= 34, f"Row height must be at least 34px, got {row0_h}"
    assert row1_h >= 34, f"Row height must be at least 34px, got {row1_h}"

    btn0: QPushButton = dlg.tbl_isotopes.cellWidget(0, 4)
    btn1: QPushButton = dlg.tbl_isotopes.cellWidget(1, 4)
    assert btn0 is not None and btn1 is not None

    # Button height must be strictly less than row height to allow margins
    assert btn0.height() <= row0_h, f"Button 0 height ({btn0.height()}) must not exceed row height ({row0_h})"
    assert btn1.height() <= row1_h, f"Button 1 height ({btn1.height()}) must not exceed row height ({row1_h})"

    # Vertical gap between buttons must be positive (strictly > 0)
    btn0_bottom = btn0.y() + btn0.height()
    btn1_top = btn1.y()
    gap = btn1_top - btn0_bottom
    assert gap > 0, f"Gap between delete buttons must be positive (got {gap}px), no solid red block"


# ===========================================================================
# 4. Maximum Form Width on Dialog Maximization
# ===========================================================================

def test_dialog_content_max_width_and_centering_on_maximize(qtbot, db_conn, tech_session):
    """Item 4: Content in Detector, Source, and Instrument dialogs has max width <= 1000px and centered."""
    det_repo = DetectorRepository(db_conn)
    src_repo = SourceRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)
    nuc_repo = NuclideRepository(db_conn)

    dialogs = [
        AddEditDetectorDialog(repo=det_repo, session=tech_session),
        DetectorDetailDialog({"code": "D1", "detector_type": "HPGE", "status": "ACTIVE"}),
        AddEditSourceDialog(repo=src_repo, session=tech_session),
        SourceDetailDialog({"source_code": "S1", "status": "ACTIVE", "isotopes": []}),
        AddEditInstrumentDialog(repo=inst_repo, session=tech_session),
        InstrumentDetailDialog({"model": "M1", "status": "ACTIVE"}),
    ]

    for dlg in dialogs:
        qtbot.addWidget(dlg)
        # Simulate maximizing to a 1920x1080 display
        dlg.resize(1920, 1080)
        dlg.show()
        QApplication.processEvents()

        # Check scroll area content widget
        scroll_areas = dlg.findChildren(QScrollArea)
        assert len(scroll_areas) >= 1, f"Dialog {type(dlg).__name__} should contain a QScrollArea"
        for sa in scroll_areas:
            assert sa.alignment() & Qt.AlignmentFlag.AlignHCenter, (
                f"ScrollArea in {type(dlg).__name__} must have AlignHCenter"
            )
            w = sa.widget()
            if w:
                assert w.maximumWidth() <= 1000, (
                    f"Scroll content in {type(dlg).__name__} must be capped at <=1000px, got {w.maximumWidth()}"
                )
                assert w.width() <= 1000, (
                    f"Scroll content width in {type(dlg).__name__} must not exceed 1000px, got {w.width()}"
                )
        dlg.close()

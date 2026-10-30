"""Unit and GUI tests for resuming inspection drafts from detector card (TASK-2.3a1).

Covers:
1. No open drafts: new row created in inspections, wizard opens directly, resume dialog does not appear.
2. Existing draft: resume dialog appears; 'Resume' opens wizard with existing inspection_id, row count does not increase.
3. 'New inspection' creates exactly one row; 'Cancel' creates nothing and opens nothing.
4. Drafts of other technicians, and inspections with status PENDING_APPROVAL, APPROVED, REJECTED are excluded.
5. List order from newest to oldest, and default selection is the newest draft.
6. Start inspection button visibility preserved: HPGe only, users with permission only.
7. Full grab in Light and Dark modes with text contrast >= 4.5:1 across all elements.
8. Exception handling preserves critical QMessageBox on failure.
"""

from datetime import datetime, timezone
import os
import sqlite3
from typing import Any, Dict, List
import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton

from kashif.core.models import CrystalType, HPGeDetector, NaIDetector, SemiconductorType, VoltagePolarity
from kashif.db.migrations import apply_migrations, init_migrations_table
from kashif.db.repositories import DetectorRepository, InspectionRepository
from kashif.gui.bidi import LRI, PDI
from kashif.gui.theme import (
    COLORS,
    calculate_contrast_ratio,
    get_mode,
    get_stylesheet,
    relative_luminance,
    set_mode,
)
from kashif.gui.views.detectors_view import DetectorDetailDialog, InspectionResumeDialog
from kashif.services.inspection_service import InspectionService
from kashif.services.session import (
    ROLE_ADMIN,
    ROLE_LAB_MANAGER,
    ROLE_TECHNICIAN,
    Session,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def ensure_qapp(qapp):
    """Ensure QApplication is initialized for all GUI tests in this module."""
    return qapp


@pytest.fixture
def resume_db():
    """In-memory SQLite database initialized with migrations and baseline seed data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_migrations_table(conn)
    apply_migrations(conn)

    cursor = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    # Users
    cursor.execute(
        """
        INSERT INTO users (id, username, password_hash, full_name, role, is_active, created_at)
        VALUES 
            (1, 'tech1', 'hash1', 'فني الفحص الأول', 'TECHNICIAN', 1, ?),
            (2, 'tech2', 'hash2', 'فني الفحص الثاني', 'TECHNICIAN', 1, ?),
            (3, 'manager1', 'hash3', 'مدير المعمل', 'LAB_MANAGER', 1, ?),
            (4, 'viewer1', 'hash4', 'مستخدم للمشاهدة', 'ADMIN', 1, ?);
        """,
        (now, now, now, now),
    )
    conn.commit()

    # Detectors
    det_repo = DetectorRepository(conn)
    tech1_session = Session(user_id=1, username="tech1", full_name="فني 1", role=ROLE_TECHNICIAN)

    hpge = HPGeDetector(
        code="HPGE-LAB-01",
        manufacturer="ORTEC",
        model="GEM-XX",
        serial_number="SN-HPGE-001",
        crystal_type=CrystalType.COAXIAL,
        semiconductor_type=SemiconductorType.P_TYPE,
        nominal_voltage=3000.0,
        voltage_polarity=VoltagePolarity.POSITIVE,
        facility_name="معمل القياسات الإشعاعية",
    )
    det_repo.create(hpge, tech1_session)

    nai = NaIDetector(
        code="NAI-LAB-01",
        manufacturer="Canberra",
        model="Model-802",
        serial_number="SN-NAI-001",
        diameter_inch=3.0,
        height_inch=3.0,
        nominal_voltage=900.0,
        facility_name="معمل القياسات الإشعاعية",
    )
    det_repo.create(nai, tech1_session)

    return conn


@pytest.fixture
def tech1_session():
    return Session(user_id=1, username="tech1", full_name="فني الفحص 1", role=ROLE_TECHNICIAN)


@pytest.fixture
def tech2_session():
    return Session(user_id=2, username="tech2", full_name="فني الفحص 2", role=ROLE_TECHNICIAN)


@pytest.fixture
def user_no_perm():
    return Session(user_id=99, username="guest", full_name="ضيف", role="GUEST")


@pytest.fixture
def hpge_detector_data(resume_db):
    det_repo = DetectorRepository(resume_db)
    return det_repo.get_by_id(1)


@pytest.fixture
def nai_detector_data(resume_db):
    det_repo = DetectorRepository(resume_db)
    return det_repo.get_by_id(2)


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

def test_start_inspection_button_visibility(resume_db, hpge_detector_data, nai_detector_data, tech1_session, user_no_perm):
    """Button visibility rules: HPGe only, permitted users (conduct_inspection or save_draft) only."""
    # HPGe + Permitted technician -> Button created and enabled
    dlg_hpge = DetectorDetailDialog(data=hpge_detector_data, session=tech1_session, conn=resume_db)
    assert dlg_hpge.btn_start_inspection is not None
    assert dlg_hpge.btn_start_inspection.isEnabled()

    # NaI detector -> Button is None
    dlg_nai = DetectorDetailDialog(data=nai_detector_data, session=tech1_session, conn=resume_db)
    assert dlg_nai.btn_start_inspection is None

    # HPGe + user without inspect/draft permission -> Button is None
    dlg_no_perm = DetectorDetailDialog(data=hpge_detector_data, session=user_no_perm, conn=resume_db)
    assert dlg_no_perm.btn_start_inspection is None


def test_no_drafts_creates_draft_and_opens_wizard_without_dialog(resume_db, hpge_detector_data, tech1_session, monkeypatch):
    """When no draft exists for current detector & technician: create draft directly, no dialog shown."""
    wizard_opened_with = []
    dialog_instantiated = []

    # Track if dialog was ever instantiated
    orig_dialog_init = InspectionResumeDialog.__init__
    def spy_dialog_init(self, *args, **kwargs):
        dialog_instantiated.append(True)
        orig_dialog_init(self, *args, **kwargs)
    monkeypatch.setattr(InspectionResumeDialog, "__init__", spy_dialog_init)

    # Mock wizard execution
    from kashif.gui.wizards import hpge_wizard
    def mock_wizard_exec(self):
        wizard_opened_with.append(self.inspection_id)
        return 0
    monkeypatch.setattr(hpge_wizard.HPGeInspectionWizard, "exec", mock_wizard_exec)

    dlg = DetectorDetailDialog(data=hpge_detector_data, session=tech1_session, conn=resume_db)
    dlg._on_start_inspection()

    # Verify dialog was NOT instantiated
    assert len(dialog_instantiated) == 0

    # Verify a new inspection draft row was created in DB
    cursor = resume_db.cursor()
    cursor.execute("SELECT id, status, technician_id, detector_id FROM inspections;")
    rows = cursor.fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "DRAFT"
    assert rows[0]["technician_id"] == 1
    assert rows[0]["detector_id"] == hpge_detector_data["id"]

    # Verify wizard was opened with that new inspection_id
    assert len(wizard_opened_with) == 1
    assert wizard_opened_with[0] == rows[0]["id"]


def test_existing_draft_resume_opens_wizard_without_new_row(resume_db, hpge_detector_data, tech1_session, monkeypatch):
    """When a draft exists and user clicks 'Resume': wizard opens with same id, row count unchanged."""
    service = InspectionService(resume_db)
    existing_id = service.create_draft(detector_id=hpge_detector_data["id"], session=tech1_session)

    cursor = resume_db.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM inspections;")
    assert cursor.fetchone()["cnt"] == 1

    wizard_opened_with = []
    from kashif.gui.wizards import hpge_wizard
    def mock_wizard_exec(self):
        wizard_opened_with.append(self.inspection_id)
        return 0
    monkeypatch.setattr(hpge_wizard.HPGeInspectionWizard, "exec", mock_wizard_exec)

    # Simulate user clicking 'Resume' in the dialog
    dialog_shown = []
    orig_exec = InspectionResumeDialog.exec
    def mock_dialog_exec(self):
        dialog_shown.append(True)
        # Select first item (default)
        self.selected_inspection_id = self.drafts[0]["id"]
        return InspectionResumeDialog.RESUME
    monkeypatch.setattr(InspectionResumeDialog, "exec", mock_dialog_exec)

    dlg = DetectorDetailDialog(data=hpge_detector_data, session=tech1_session, conn=resume_db)
    dlg._on_start_inspection()

    assert len(dialog_shown) == 1
    assert len(wizard_opened_with) == 1
    assert wizard_opened_with[0] == existing_id

    # Verify no new row was added to inspections table
    cursor.execute("SELECT COUNT(*) as cnt FROM inspections;")
    assert cursor.fetchone()["cnt"] == 1


def test_existing_draft_new_inspection_adds_exactly_one_row(resume_db, hpge_detector_data, tech1_session, monkeypatch):
    """When a draft exists and user chooses 'New inspection': exactly one row added, old draft preserved."""
    service = InspectionService(resume_db)
    draft1_id = service.create_draft(detector_id=hpge_detector_data["id"], session=tech1_session)

    wizard_opened_with = []
    from kashif.gui.wizards import hpge_wizard
    def mock_wizard_exec(self):
        wizard_opened_with.append(self.inspection_id)
        return 0
    monkeypatch.setattr(hpge_wizard.HPGeInspectionWizard, "exec", mock_wizard_exec)

    # Simulate user choosing 'New inspection'
    def mock_dialog_exec(self):
        return InspectionResumeDialog.NEW_INSPECTION
    monkeypatch.setattr(InspectionResumeDialog, "exec", mock_dialog_exec)

    dlg = DetectorDetailDialog(data=hpge_detector_data, session=tech1_session, conn=resume_db)
    dlg._on_start_inspection()

    # Verify exactly two inspections exist now
    cursor = resume_db.cursor()
    cursor.execute("SELECT id, status FROM inspections ORDER BY id ASC;")
    rows = cursor.fetchall()
    assert len(rows) == 2
    assert rows[0]["id"] == draft1_id
    assert rows[0]["status"] == "DRAFT"
    assert rows[1]["status"] == "DRAFT"
    assert rows[1]["id"] != draft1_id

    # Verify wizard opened with the second draft id
    assert len(wizard_opened_with) == 1
    assert wizard_opened_with[0] == rows[1]["id"]


def test_existing_draft_cancel_creates_nothing_and_opens_nothing(resume_db, hpge_detector_data, tech1_session, monkeypatch):
    """When dialog is canceled or closed: no row created, wizard does not open."""
    service = InspectionService(resume_db)
    draft1_id = service.create_draft(detector_id=hpge_detector_data["id"], session=tech1_session)

    wizard_opened_with = []
    from kashif.gui.wizards import hpge_wizard
    def mock_wizard_exec(self):
        wizard_opened_with.append(self.inspection_id)
        return 0
    monkeypatch.setattr(hpge_wizard.HPGeInspectionWizard, "exec", mock_wizard_exec)

    # Case A: Return CANCEL (0)
    def mock_dialog_cancel(self):
        return InspectionResumeDialog.CANCEL
    monkeypatch.setattr(InspectionResumeDialog, "exec", mock_dialog_cancel)

    dlg = DetectorDetailDialog(data=hpge_detector_data, session=tech1_session, conn=resume_db)
    dlg._on_start_inspection()

    assert len(wizard_opened_with) == 0
    cursor = resume_db.cursor()
    cursor.execute("SELECT COUNT(*) as cnt FROM inspections;")
    assert cursor.fetchone()["cnt"] == 1

    # Case B: Close/Reject dialog (returns 0)
    def mock_dialog_reject(self):
        return 0
    monkeypatch.setattr(InspectionResumeDialog, "exec", mock_dialog_reject)

    dlg._on_start_inspection()
    assert len(wizard_opened_with) == 0
    cursor.execute("SELECT COUNT(*) as cnt FROM inspections;")
    assert cursor.fetchone()["cnt"] == 1


def test_filter_excludes_other_tech_drafts_and_non_draft_statuses(resume_db, hpge_detector_data, tech1_session, tech2_session, monkeypatch):
    """Verify that drafts of other technicians, and inspections with status != 'DRAFT' are excluded."""
    service = InspectionService(resume_db)
    det_id = hpge_detector_data["id"]

    # 1. Draft by tech2
    tech2_draft_id = service.create_draft(detector_id=det_id, session=tech2_session)

    # 2. Tech1 inspection with PENDING_APPROVAL
    pending_id = service.create_draft(detector_id=det_id, session=tech1_session)
    cursor = resume_db.cursor()
    cursor.execute("UPDATE inspections SET status = 'PENDING_APPROVAL' WHERE id = ?;", (pending_id,))

    # 3. Tech1 inspection with APPROVED
    approved_id = service.create_draft(detector_id=det_id, session=tech1_session)
    cursor.execute("UPDATE inspections SET status = 'APPROVED' WHERE id = ?;", (approved_id,))

    # 4. Tech1 inspection with REJECTED
    rejected_id = service.create_draft(detector_id=det_id, session=tech1_session)
    cursor.execute("UPDATE inspections SET status = 'REJECTED' WHERE id = ?;", (rejected_id,))
    resume_db.commit()

    # Part A: When tech1 has NO open drafts (only other tech and non-draft statuses exist)
    # Dialog should NOT appear, and a new draft should be created for tech1 directly
    dialog_called = []
    monkeypatch.setattr(InspectionResumeDialog, "exec", lambda self: dialog_called.append(True) or 0)
    from kashif.gui.wizards import hpge_wizard
    monkeypatch.setattr(hpge_wizard.HPGeInspectionWizard, "exec", lambda self: 0)

    dlg = DetectorDetailDialog(data=hpge_detector_data, session=tech1_session, conn=resume_db)
    dlg._on_start_inspection()

    assert len(dialog_called) == 0  # No dialog shown

    # Clean up draft created in Part A to test single open draft in Part B
    cursor.execute("DELETE FROM inspections WHERE technician_id = 1 AND status = 'DRAFT';")
    resume_db.commit()

    # Part B: Now add an open DRAFT for tech1
    tech1_draft_id = service.create_draft(detector_id=det_id, session=tech1_session)

    captured_drafts = []
    orig_init = InspectionResumeDialog.__init__
    def spy_init(self, drafts, parent=None):
        captured_drafts.extend(drafts)
        orig_init(self, drafts, parent)
    monkeypatch.setattr(InspectionResumeDialog, "__init__", spy_init)
    monkeypatch.setattr(InspectionResumeDialog, "exec", lambda self: InspectionResumeDialog.CANCEL)

    dlg._on_start_inspection()

    # Captured drafts should contain ONLY tech1_draft_id
    assert len(captured_drafts) == 1
    assert captured_drafts[0]["id"] == tech1_draft_id
    assert captured_drafts[0]["status"] == "DRAFT"
    assert captured_drafts[0]["technician_id"] == 1

    # Explicitly verify excluded inspection IDs are absent
    captured_ids = {d["id"] for d in captured_drafts}
    assert tech2_draft_id not in captured_ids
    assert pending_id not in captured_ids
    assert approved_id not in captured_ids
    assert rejected_id not in captured_ids


def test_drafts_list_ordering_and_default_selection(resume_db, hpge_detector_data, tech1_session):
    """List order must be newest to oldest, and default selected is the newest."""
    det_id = hpge_detector_data["id"]
    cursor = resume_db.cursor()

    # Insert 3 drafts with distinct timestamps
    cursor.execute(
        """
        INSERT INTO inspections (id, detector_id, technician_id, inspection_date, status, created_at, updated_at)
        VALUES
            (10, ?, 1, '2026-10-28', 'DRAFT', '2026-10-28T08:00:00Z', '2026-10-28T08:00:00Z'),
            (20, ?, 1, '2026-10-29', 'DRAFT', '2026-10-29T10:00:00Z', '2026-10-29T10:00:00Z'),
            (30, ?, 1, '2026-10-30', 'DRAFT', '2026-10-30T12:00:00Z', '2026-10-30T12:00:00Z');
        """,
        (det_id, det_id, det_id),
    )
    resume_db.commit()

    service = InspectionService(resume_db)
    all_drafts = [
        insp for insp in service.list_by_detector(det_id)
        if insp["status"] == "DRAFT" and insp["technician_id"] == 1
    ]

    dialog = InspectionResumeDialog(drafts=all_drafts)

    # 3 items in list
    assert dialog.list_drafts.count() == 3

    # Ordering: newest (30) at row 0, then 20 at row 1, then oldest (10) at row 2
    item0 = dialog.list_drafts.item(0)
    item1 = dialog.list_drafts.item(1)
    item2 = dialog.list_drafts.item(2)

    assert item0.data(Qt.ItemDataRole.UserRole) == 30
    assert item1.data(Qt.ItemDataRole.UserRole) == 20
    assert item2.data(Qt.ItemDataRole.UserRole) == 10

    # Default selected must be row 0 (the newest)
    assert dialog.list_drafts.currentRow() == 0

    # Verify BiDi formatting and contents of item0 text
    text0 = item0.text()
    assert "رقم الفحص:" in text0
    assert "30" in text0
    assert LRI in text0 and PDI in text0
    assert "آخر تحديث:" in text0


def test_dialog_buttons_interaction(resume_db, hpge_detector_data, tech1_session):
    """Verify dialog buttons emit correct return codes and inspection_id."""
    sample_drafts = [
        {"id": 101, "status": "DRAFT", "technician_id": 1, "updated_at": "2026-10-30T12:00:00Z"},
        {"id": 102, "status": "DRAFT", "technician_id": 1, "updated_at": "2026-10-30T13:00:00Z"},
    ]
    dialog = InspectionResumeDialog(drafts=sample_drafts)

    # Primary button: Resume
    dialog.list_drafts.setCurrentRow(0)  # item 102 (newest)
    dialog.btn_resume.click()
    assert dialog.result() == InspectionResumeDialog.RESUME
    assert dialog.selected_inspection_id == 102

    # New inspection button
    dialog2 = InspectionResumeDialog(drafts=sample_drafts)
    dialog2.btn_new.click()
    assert dialog2.result() == InspectionResumeDialog.NEW_INSPECTION
    assert dialog2.selected_inspection_id is None

    # Cancel button
    dialog3 = InspectionResumeDialog(drafts=sample_drafts)
    dialog3.btn_cancel.click()
    assert dialog3.result() == 0  # QDialog.DialogCode.Rejected
    assert dialog3.selected_inspection_id is None


def test_exception_handling_shows_critical_box(resume_db, hpge_detector_data, tech1_session, monkeypatch):
    """Verify that exceptions in _on_start_inspection trigger QMessageBox.critical."""
    msg_boxes = []
    def mock_critical(parent, title, text):
        msg_boxes.append((title, text))
        return QMessageBox.StandardButton.Ok
    monkeypatch.setattr(QMessageBox, "critical", mock_critical)

    # Simulate database error in list_by_detector
    from kashif.services.inspection_service import InspectionService
    def mock_list_by_detector(self, detector_id):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(InspectionService, "list_by_detector", mock_list_by_detector)

    dlg = DetectorDetailDialog(data=hpge_detector_data, session=tech1_session, conn=resume_db)
    dlg._on_start_inspection()

    assert len(msg_boxes) == 1
    assert "خطأ في بدء الفحص" in msg_boxes[0][0]
    assert "تعذر إنشاء مسودة الفحص الفني" in msg_boxes[0][1]


def test_full_grab_and_wcag_contrast_light_and_dark(resume_db, qapp):
    """Full grab of InspectionResumeDialog in Light and Dark modes with WCAG contrast >= 4.5:1."""
    out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "output", "grab_inspection_resume_dialog"))
    os.makedirs(out_dir, exist_ok=True)
    artifact_dir = r"C:\Users\DELL\.gemini\antigravity\brain\b2406b53-3807-4640-9684-8d777722750b\grabs"
    os.makedirs(artifact_dir, exist_ok=True)

    sample_drafts = [
        {"id": 42, "status": "DRAFT", "technician_id": 1, "updated_at": "2026-10-30T14:30:00Z"},
        {"id": 35, "status": "DRAFT", "technician_id": 1, "updated_at": "2026-10-30T09:15:00Z"},
    ]

    for theme in ["light", "dark"]:
        set_mode(theme)
        qapp.setStyleSheet(get_stylesheet(theme))

        dlg = InspectionResumeDialog(drafts=sample_drafts)
        dlg.resize(520, 360)
        dlg.show()
        qapp.processEvents()

        # 1. Full Grab verification
        img = dlg.grab().toImage()
        assert img.width() > 0 and img.height() > 0

        # Save to both output and artifact directory
        filename = f"inspection_resume_dialog_{theme}.png"
        img.save(os.path.join(out_dir, filename))
        img.save(os.path.join(artifact_dir, filename))

        # 2. Text contrast verification (>= 4.5:1)
        # Title contrast against dialog background
        dlg_bg = COLORS["dark_surface"] if theme == "dark" else COLORS["light_surface"]
        title_color = COLORS["dark_teal"] if theme == "dark" else COLORS["navy"]
        cr_title = calculate_contrast_ratio(title_color, dlg_bg)
        assert cr_title >= 4.5, f"Title contrast {cr_title:.2f} < 4.5 in {theme} mode"

        # Subtitle contrast against dialog background
        sub_color = COLORS["dark_slate"] if theme == "dark" else COLORS["light_slate"]
        cr_sub = calculate_contrast_ratio(sub_color, dlg_bg)
        assert cr_sub >= 4.5, f"Subtitle contrast {cr_sub:.2f} < 4.5 in {theme} mode"

        # List items text contrast against card background
        card_bg = COLORS["dark_card"] if theme == "dark" else COLORS["light_card"]
        item_text_color = COLORS["dark_ink"] if theme == "dark" else COLORS["light_ink"]
        cr_item = calculate_contrast_ratio(item_text_color, card_bg)
        assert cr_item >= 4.5, f"List item contrast {cr_item:.2f} < 4.5 in {theme} mode"

        # Selected list item contrast
        sel_bg = COLORS["blue"] if theme == "dark" else COLORS["light_mist"]
        sel_fg = COLORS["dark_ink"] if theme == "dark" else COLORS["navy"]
        cr_sel = calculate_contrast_ratio(sel_fg, sel_bg)
        assert cr_sel >= 4.5, f"Selected item contrast {cr_sel:.2f} < 4.5 in {theme} mode"

        # Primary button text contrast
        btn_bg = COLORS["navy"] if theme == "light" else COLORS["dark_teal"]
        btn_fg = "#FFFFFF" if theme == "light" else COLORS["dark_surface"]
        cr_btn = calculate_contrast_ratio(btn_fg, btn_bg)
        assert cr_btn >= 4.5, f"Primary button contrast {cr_btn:.2f} < 4.5 in {theme} mode"

        dlg.close()

    # Reset mode back to light
    set_mode("light")
    qapp.setStyleSheet(get_stylesheet("light"))

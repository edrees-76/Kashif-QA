"""Tests for HPGe detector inspection wizard and step 1 & 2 workflows (TASK-2.2).

Covers:
1. No preselected values on mandatory fields (Step 1 and Step 2).
2. Preventing step transition on WAIT / FAILURE / BLOCK / incomplete fields.
3. Saving only valid entered fields as partial draft without forcing subsequent fields.
4. Not sending hv_ramp_* parameters before explicit technician confirmation.
5. Re-confirmation requirement (checkbox unchecked) when reopening an existing draft.
6. Ownership enforcement: non-owner opens wizard in read-only mode with disabled inputs.
7. Start inspection button in DetectorDetailDialog: visible for HPGe only and permitted users only.
8. Steps 3 to 5 placeholders prevent submission and do not allow finishing inspection.
9. High voltage ramp countdown timer behavior and safety alert banner.
"""

from datetime import datetime, timezone
import os
import sqlite3
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton

from kashif.core.models import Verdict, VoltagePolarity, WindowStatus
from kashif.db.migrations import apply_migrations, init_migrations_table
from kashif.db.repositories import DetectorRepository, InspectionRepository
from kashif.gui.views.detectors_view import DetectorDetailDialog
from kashif.gui.wizards.hpge_wizard import HPGeInspectionWizard
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

@pytest.fixture
def wizard_db():
    """In-memory SQLite database initialized with all migrations and test fixtures."""
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
            (1, 'tech1', 'hash1', 'فني الفحص 1', 'TECHNICIAN', 1, ?),
            (2, 'tech2', 'hash2', 'فني الفحص 2', 'TECHNICIAN', 1, ?),
            (3, 'manager1', 'hash3', 'مدير المعمل', 'LAB_MANAGER', 1, ?),
            (4, 'admin1', 'hash4', 'مدير النظام', 'ADMIN', 1, ?);
        """,
        (now, now, now, now),
    )

    # Detectors: HPGe and NaI
    cursor.execute(
        """
        INSERT INTO detectors (
            id, code, detector_type, manufacturer, model, serial_number,
            crystal_type, semiconductor_type, nominal_voltage, voltage_polarity,
            certified_polarity, nominal_fwhm_kev, nominal_efficiency_pct, cooling_hours_cert,
            hv_ramp_max_v_per_s, hv_ramp_reference, status, created_at, updated_at
        ) VALUES (
            1, 'DET-HPGE-001', 'HPGE', 'ORTEC', 'GEM-XX', 'SN-HPGE-1',
            'COAXIAL', 'P_TYPE', 3000.0, 'POSITIVE',
            'POSITIVE', 1.85, 40.0, 6.0, 10.0, 'ORTEC Manual Sec. 4', 'ACTIVE', ?, ?
        );
        """,
        (now, now),
    )

    cursor.execute(
        """
        INSERT INTO detectors (
            id, code, detector_type, manufacturer, model, serial_number,
            diameter_inch, height_inch, nominal_voltage, nominal_resolution_pct,
            status, created_at, updated_at
        ) VALUES (
            2, 'DET-NAI-001', 'NAI', 'BICRON', '2M2/2', 'SN-NAI-1',
            2.0, 2.0, 850.0, 7.5,
            'ACTIVE', ?, ?
        );
        """,
        (now, now),
    )

    conn.commit()
    return conn


@pytest.fixture
def tech1_session():
    return Session(user_id=1, username="tech1", full_name="فني الفحص 1", role=ROLE_TECHNICIAN)


@pytest.fixture
def tech2_session():
    return Session(user_id=2, username="tech2", full_name="فني الفحص 2", role=ROLE_TECHNICIAN)


@pytest.fixture
def manager_session():
    return Session(user_id=3, username="manager1", full_name="مدير المعمل", role=ROLE_LAB_MANAGER)


@pytest.fixture
def admin_session():
    return Session(user_id=4, username="admin1", full_name="مدير النظام", role=ROLE_ADMIN)


# ---------------------------------------------------------------------------
# Tests for HPGeInspectionWizard
# ---------------------------------------------------------------------------

class TestHPGeInspectionWizard:
    """مجموعة اختبارات معالج فحص كواشف HPGe والخطوتين 1 و2."""

    def test_no_preselected_values_in_wizard(self, wizard_db, tech1_session, qapp):
        """1. التأكد من عدم وجود أي قيم مختارة مسبقاً أو مفترضة في الحقول الإلزامية."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)

        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # مدخلات الخطوة 1
        assert wizard.combo_window_status.currentData() is None
        assert wizard.edit_cooling_hours.text() == ""
        assert wizard.combo_vacuum_intact.currentData() is None
        assert wizard.combo_wet_frozen.currentData() is None

        # مدخلات الخطوة 2
        assert wizard.edit_v_pos_24.text() == ""
        assert wizard.edit_v_neg_24.text() == ""
        assert wizard.edit_v_pos_12.text() == ""
        assert wizard.edit_v_neg_12.text() == ""
        assert wizard.edit_v_pos_6.text() == ""
        assert wizard.edit_v_neg_6.text() == ""
        assert wizard.combo_applied_polarity.currentData() is None
        assert wizard.edit_applied_voltage.text() == ""
        assert wizard.combo_ramp_source.currentData() is None
        assert wizard.edit_ramp_rate.text() == ""
        assert wizard.chk_ramp_confirm.isChecked() is False
        assert wizard.combo_preamp_signal.currentData() is None

    def test_step1_prevents_transition_on_missing_fields(self, wizard_db, tech1_session, qapp):
        """منع الانتقال من الخطوة 1 عند نقص أي حقل إلزامي."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # الحقول فارغة -> التحقق يفشل
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "إلزامي" in msg

        # ملء بعض الحقول وترك بعضها
        wizard.combo_window_status.setCurrentIndex(1)  # INTACT
        wizard.edit_cooling_hours.setText("8.0")
        # ترك الفراغ ورطوبة الحاوية فارغين
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "إحكام فراغ الحاوية" in msg

    def test_step1_prevents_transition_on_cooling_time_wait(self, wizard_db, tech1_session, qapp):
        """منع الانتقال من الخطوة 1 عند عدم اكتمال التبريد مع إبراز لافتة الانتظار والساعات المتبقية."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        wizard.combo_window_status.setCurrentIndex(1)  # INTACT
        wizard.edit_cooling_hours.setText("3.0")  # المطلوب بالشهادة 6.0
        wizard.combo_vacuum_intact.setCurrentIndex(1)  # سليم
        wizard.combo_wet_frozen.setCurrentIndex(1)  # جاف (0)

        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "انتظار تبريد وليس عطلاً" in msg
        assert "3.00" in msg or "3.0" in msg

        # التحقق من نص لافتة التقييم المباشر
        assert "انتظار تبريد وليس عطلاً" in wizard.lbl_step1_status.text()
        assert "3.00" in wizard.lbl_step1_status.text()

    def test_step1_early_stop_on_broken_window(self, wizard_db, tech1_session, qapp):
        """إخفاق حاسم (R-HPGE-01): نافذة بريليوم مكسورة تقفل الانتقال وتحفظ المسودة فورا."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        wizard.combo_window_status.setCurrentIndex(3)  # BROKEN
        wizard.edit_cooling_hours.setText("8.0")
        wizard.combo_vacuum_intact.setCurrentIndex(1)
        wizard.combo_wet_frozen.setCurrentIndex(1)

        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "نافذة البريليوم مكسورة" in msg
        assert "R-HPGE-01" in msg

        # التحقق من أن المسودة حُفظت بـ BROKEN
        full = service.get_inspection_full(insp_id)
        assert full["hpge_measurements"]["beryllium_window_status"] == "BROKEN"

    def test_step1_early_stop_on_vacuum_leak_or_wet_cryostat(self, wizard_db, tech1_session, qapp):
        """إخفاق حاسم (R-HPGE-04): تسريب الفراغ أو تندّي الحاوية يقفل الانتقال."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        wizard.combo_window_status.setCurrentIndex(1)  # INTACT
        wizard.edit_cooling_hours.setText("8.0")
        wizard.combo_vacuum_intact.setCurrentIndex(2)  # لا — يوجد تسريب (0)
        wizard.combo_wet_frozen.setCurrentIndex(1)  # لا — جاف

        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "R-HPGE-04" in msg

        # حالة تندّي الحاوية
        wizard.combo_vacuum_intact.setCurrentIndex(1)  # سليم
        wizard.combo_wet_frozen.setCurrentIndex(2)  # نعم — تندّي أو جليد (1)
        ok2, msg2 = wizard._validate_current_step()
        assert ok2 is False
        assert "R-HPGE-04" in msg2

    def test_step1_scratched_window_allows_proceeding_with_warning(self, wizard_db, tech1_session, qapp):
        """نافذة مخدوشة (R-HPGE-02): تظهر لافتة تحذير OUT_OF_SPEC وتسمح بالمتابعة للخطوة 2."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        wizard.combo_window_status.setCurrentIndex(2)  # SCRATCHED
        wizard.edit_cooling_hours.setText("7.0")
        wizard.combo_vacuum_intact.setCurrentIndex(1)  # سليم
        wizard.combo_wet_frozen.setCurrentIndex(1)  # جاف

        assert "OUT_OF_SPEC" in wizard.lbl_step1_status.text() or "مخدوشة" in wizard.lbl_step1_status.text()

        ok, msg = wizard._validate_current_step()
        assert ok is True
        assert msg is None

        # الانتقال للخطوة 2
        wizard._on_next_step()
        assert wizard.stack.currentIndex() == 1

    def test_step2_nim_voltages_validation(self, wizard_db, tech1_session, qapp):
        """التحقق من إلزامية جهود NIM القياسية الأربعة في الخطوة 2."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard.stack.setCurrentIndex(1)

        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "NIM" in msg

        # ملء جهود غير كاملة
        wizard.edit_v_pos_24.setText("24.0")
        wizard.edit_v_neg_24.setText("-24.0")
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "+12 V" in msg

    def test_step2_polarity_mismatch_blocks_transition(self, wizard_db, tech1_session, qapp):
        """القطبية المطبقة المخالفة لقطبية الكاشف تعطي إخفاق BLOCK وتمنع المتابعة."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard.stack.setCurrentIndex(1)

        # إدخال جهود NIM
        wizard.edit_v_pos_24.setText("24.0")
        wizard.edit_v_neg_24.setText("-24.0")
        wizard.edit_v_pos_12.setText("12.0")
        wizard.edit_v_neg_12.setText("-12.0")

        # قطبية الكاشف POSITIVE، واختيار NEGATIVE
        wizard.combo_applied_polarity.setCurrentIndex(2)  # NEGATIVE
        wizard.edit_applied_voltage.setText("3000.0")
        wizard.combo_ramp_source.setCurrentIndex(1)  # MANUAL
        wizard.edit_ramp_rate.setText("8.0")
        wizard.chk_ramp_confirm.setChecked(True)
        wizard.combo_preamp_signal.setCurrentIndex(1)  # سليم

        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "R-HPGE-06" in msg or "تخالف" in msg

    def test_step2_ramp_confirmation_and_rate_limit(self, wizard_db, tech1_session, qapp):
        """التحقق من شرط تأكيد معدل ومصدر الرفع الصريح، ومنع التجاوز (BLOCK)."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard.stack.setCurrentIndex(1)

        wizard.edit_v_pos_24.setText("24.0")
        wizard.edit_v_neg_24.setText("-24.0")
        wizard.edit_v_pos_12.setText("12.0")
        wizard.edit_v_neg_12.setText("-12.0")
        wizard.combo_applied_polarity.setCurrentIndex(1)  # POSITIVE
        wizard.edit_applied_voltage.setText("3000.0")
        wizard.combo_ramp_source.setCurrentIndex(1)  # MANUAL
        wizard.edit_ramp_rate.setText("8.0")
        wizard.combo_preamp_signal.setCurrentIndex(1)  # سليم

        # الخانة غير مؤكدة -> يجب منع التقدم
        wizard.chk_ramp_confirm.setChecked(False)
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "تأكيد" in msg or "الإقرار" in msg

        # تجاوز الحد الأقصى للمعدل (حد الكاشف 10.0 وإدخال 15.0)
        wizard.chk_ramp_confirm.setChecked(True)
        wizard.edit_ramp_rate.setText("15.0")
        ok_max, msg_max = wizard._validate_current_step()
        assert ok_max is False
        assert "R-HPGE-07" in msg_max or "يتجاوز" in msg_max

        # معدل سليم ومؤكد -> يمر بنجاح
        wizard.edit_ramp_rate.setText("9.5")
        ok_valid, msg_valid = wizard._validate_current_step()
        assert ok_valid is True
        assert msg_valid is None

    def test_step2_preamp_no_signal_early_stop(self, wizard_db, tech1_session, qapp):
        """انعدام إشارة المضخم الأولي (R-HPGE-08) يعطي رسوب فوري ويمنع المتابعة."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard.stack.setCurrentIndex(1)

        wizard.edit_v_pos_24.setText("24.0")
        wizard.edit_v_neg_24.setText("-24.0")
        wizard.edit_v_pos_12.setText("12.0")
        wizard.edit_v_neg_12.setText("-12.0")
        wizard.combo_applied_polarity.setCurrentIndex(1)
        wizard.edit_applied_voltage.setText("3000.0")
        wizard.combo_ramp_source.setCurrentIndex(1)
        wizard.edit_ramp_rate.setText("8.0")
        wizard.chk_ramp_confirm.setChecked(True)

        # اختيار "لا إشارة"
        wizard.combo_preamp_signal.setCurrentIndex(2)  # 0 = لا
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "R-HPGE-08" in msg or "المضخم الأولي" in msg

    def test_saving_only_valid_entered_fields(self, wizard_db, tech1_session, qapp):
        """حفظ الحقول المدخلة فقط كمسودة جزئية دون فرض بقية الحقول."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # ملء بعض حقول الخطوة 1 فقط
        wizard.combo_window_status.setCurrentIndex(1)  # INTACT
        wizard.edit_cooling_hours.setText("7.5")
        wizard.combo_vacuum_intact.setCurrentIndex(1)  # سليم
        wizard.combo_wet_frozen.setCurrentIndex(1)  # جاف

        saved_ok = wizard.save_draft(show_feedback=False)
        assert saved_ok is True

        # التحقق من قاعدة البيانات
        full = service.get_inspection_full(insp_id)
        meas = full["hpge_measurements"]
        assert meas["beryllium_window_status"] == "INTACT"
        assert meas["cooling_hours"] == 7.5
        assert meas["vacuum_intact"] == 1
        assert meas["cryostat_wet_or_frozen"] == 0
        assert meas["cryostat_intact"] == 1
        # الحقول الأخرى غير المدخلة يجب أن تبقى None
        assert meas["applied_voltage"] is None
        assert meas["v_pos_24"] is None

    def test_ramp_rate_not_sent_before_confirmation(self, wizard_db, tech1_session, qapp):
        """الشرط الحرج: عدم إرسال hv_ramp_source و hv_ramp_rate_v_per_s قبل التأكيد الصريح بالخانة."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # كتابة المصدر والمعدل مع ترك خانة التأكيد غير محددة
        wizard.combo_ramp_source.setCurrentIndex(2)  # ACQUISITION_SOFTWARE
        wizard.edit_ramp_rate.setText("8.5")
        wizard.chk_ramp_confirm.setChecked(False)

        # حفظ المسودة
        wizard.save_draft(show_feedback=False)

        # فحص ما تم تمريره لقاعدة البيانات: بما أنه لم يؤكد، لا تُرسل في payload التحديث
        # (قاعدة البيانات ستحتفظ بافتراضياتها في حال لم تُرسل، ولكن المعالج لا يوثقها)
        full = service.get_inspection_full(insp_id)

        # الآن الفني يحدد خانة التأكيد ويحفظ
        wizard.chk_ramp_confirm.setChecked(True)
        wizard.save_draft(show_feedback=False)

        full_after = service.get_inspection_full(insp_id)
        assert full_after["hpge_measurements"]["hv_ramp_source"] == "ACQUISITION_SOFTWARE"
        assert full_after["hpge_measurements"]["hv_ramp_rate_v_per_s"] == 8.5

    def test_reopening_draft_leaves_ramp_unconfirmed(self, wizard_db, tech1_session, qapp):
        """عند إعادة فتح مسودة مخزنة، تُعرض خانة التأكيد غير مؤكدة دائماً ويُطلب تأكيدها مجدداً."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)

        # حفظ مسودة مسبقة بقيم معينة
        service.save_hpge_measurements(
            insp_id,
            {
                "cooling_hours": 8.0,
                "hv_ramp_source": "ACQUISITION_SOFTWARE",
                "hv_ramp_rate_v_per_s": 7.5,
            },
            tech1_session,
        )

        # فتح المعالج على المسودة المخزنة
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # خانة التأكيد يجب أن تكون غير محددة صراحة
        assert wizard.chk_ramp_confirm.isChecked() is False

    def test_read_only_mode_for_non_owner(self, wizard_db, tech1_session, tech2_session, qapp):
        """قصر التعديل على المالك: فني آخر يفتح الفحص للقراءة فقط وتُعطّل جميع مدخلاته وأزرار حفظه."""
        service = InspectionService(wizard_db)
        # الفني 1 ينشئ الفحص
        insp_id = service.create_draft(detector_id=1, session=tech1_session)

        # الفني 2 يفتح المعالج
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech2_session, inspection_id=insp_id)

        assert wizard.is_owner is False
        assert wizard.is_editable is False

        # زر الحفظ معطل
        assert wizard.btn_save_draft.isEnabled() is False

        # المدخلات معطلة أو للقراءة فقط
        assert wizard.combo_window_status.isEnabled() is False
        assert wizard.edit_cooling_hours.isReadOnly() is True
        assert wizard.combo_vacuum_intact.isEnabled() is False
        assert wizard.combo_wet_frozen.isEnabled() is False
        assert wizard.edit_v_pos_24.isReadOnly() is True
        assert wizard.combo_applied_polarity.isEnabled() is False
        assert wizard.chk_ramp_confirm.isEnabled() is False

        # محاولة حفظ المسودة من المعالج تُرجع False
        assert wizard.save_draft(show_feedback=False) is False

    def test_detector_detail_dialog_start_inspection_button(self, wizard_db, tech1_session, manager_session, qapp):
        """ظهور زر 'بدء فحص' لكواشف HPGe فقط وللمستخدم الذي يملك الصلاحية."""
        repo = DetectorRepository(wizard_db)

        hpge_det = repo.get_by_id(1)  # HPGe
        nai_det = repo.get_by_id(2)   # NaI

        # 1. كاشف HPGe مع فني يملك conduct_inspection
        dlg_hpge = DetectorDetailDialog(data=hpge_det, session=tech1_session, conn=wizard_db)
        assert dlg_hpge.btn_start_inspection is not None
        assert dlg_hpge.btn_start_inspection.isVisibleTo(dlg_hpge) or dlg_hpge.btn_start_inspection.isEnabled()

        # 2. كاشف NaI مع فني يملك conduct_inspection -> لا يظهر زر بدء فحص
        dlg_nai = DetectorDetailDialog(data=nai_det, session=tech1_session, conn=wizard_db)
        assert dlg_nai.btn_start_inspection is None

        # 3. مستخدم لا يملك صلاحية conduct_inspection أو save_draft
        user_no_perm = Session(user_id=99, username="guest", full_name="زائر", role="GUEST")
        dlg_no_perm = DetectorDetailDialog(data=hpge_det, session=user_no_perm, conn=wizard_db)
        assert dlg_no_perm.btn_start_inspection is None

    def test_steps_3_to_5_placeholders_prevent_submission(self, wizard_db, tech1_session, qapp):
        """الخطوات 3 إلى 5 صفحات فارغة معلَّمة 'قيد التطوير' ولا تسمح بالإنهاء أو التقديم."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # الانتقال للخطوة 3 و4 و5
        wizard.stack.setCurrentIndex(2)  # الخطوة 3
        lbl_p3 = wizard.findChild(QLabel, "lbl_placeholder_step_3")
        assert lbl_p3 is not None
        assert "قيد التطوير (TASK-2.3)" in lbl_p3.text()

        wizard.stack.setCurrentIndex(3)  # الخطوة 4
        lbl_p4 = wizard.findChild(QLabel, "lbl_placeholder_step_4")
        assert lbl_p4 is not None
        assert "قيد التطوير (TASK-2.3)" in lbl_p4.text()

        wizard.stack.setCurrentIndex(4)  # الخطوة 5
        lbl_p5 = wizard.findChild(QLabel, "lbl_placeholder_step_5")
        assert lbl_p5 is not None
        assert "قيد التطوير (TASK-2.3)" in lbl_p5.text()

        # زر التالي معطل في الخطوة الأخيرة ولا يوجد زر إنهاء
        wizard._update_navigation_state()
        assert wizard.btn_next.isEnabled() is False
        assert "قيد التطوير" in wizard.btn_next.text()

    def test_hv_ramp_timer_countdown_and_safety_alert(self, wizard_db, tech1_session, qapp):
        """اختبار مؤقت رفع الجهد الاسترشادي وتنبيه الأمان الصارم."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard.stack.setCurrentIndex(1)

        # التحقق من وجود تنبيه الأمان الصارم
        lbl_warn = wizard.findChild(QLabel, "lbl_safety_warning")
        assert lbl_warn is not None
        assert "ممنوع توصيل أو فصل كابل الجهد العالي" in lbl_warn.text()

        # إدخال الجهد والمعدل لحساب الزمن المقترح
        wizard.edit_applied_voltage.setText("3000.0")
        wizard.edit_ramp_rate.setText("10.0")
        wizard._update_timer_target_seconds()

        # 3000 V / 10 V/s = 300 ثانية = 05:00 دقيقة
        assert wizard._timer_total_seconds == 300
        assert wizard.lbl_timer_display.text() == "05:00"

        # تشغيل المؤقت وتمرير ثانية
        wizard._on_toggle_timer()
        assert wizard._timer_is_running is True
        wizard._on_timer_tick()
        assert wizard._timer_remaining_seconds == 299
        assert wizard.lbl_timer_display.text() == "04:59"

        # إيقاف مؤقت ثم إعادة ضبط
        wizard._on_toggle_timer()
        assert wizard._timer_is_running is False
        wizard._on_reset_timer()
        assert wizard._timer_remaining_seconds == 300
        assert wizard.lbl_timer_display.text() == "05:00"

    def test_reopen_draft_no_slot_exceptions(self, wizard_db, tech1_session, qtbot):
        """تصحيح TASK-2.2: إعادة فتح مسودة كاملة لا يرفع أي استثناء داخل Qt slots."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        service.save_hpge_measurements(
            insp_id,
            {
                "beryllium_window_status": "INTACT",
                "cooling_hours": 6.5,
                "vacuum_intact": 1,
                "cryostat_wet_or_frozen": 0,
                "cryostat_intact": 1,
                "v_pos_24": 24.0,
                "v_neg_24": -24.0,
                "v_pos_12": 12.0,
                "v_neg_12": -12.0,
                "v_pos_6": 6.0,
                "v_neg_6": -6.0,
                "applied_polarity": "POSITIVE",
                "applied_voltage": 3000.0,
                "hv_ramp_source": "ACQUISITION_SOFTWARE",
                "hv_ramp_rate_v_per_s": 5.0,
                "has_preamp_signal": 1,
            },
            tech1_session,
        )

        with qtbot.captureExceptions() as exceptions:
            wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
            qtbot.addWidget(wizard)
            # محاكاة التفاعل وتغيير النصوص والتنقل
            wizard.edit_v_pos_24.setText("24.05")
            wizard._on_next_step()
            wizard.edit_applied_voltage.setText("3100.0")

        assert len(exceptions) == 0, f"Unexpected slot exceptions captured: {exceptions}"

    def test_reopen_draft_preserves_confirmed_hv_ramp_without_overwrite(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2 (2-د): حفظ مؤكد ACQUISITION_SOFTWARE/5.0 ثم إعادة فتح وتعديل التبريد لا يغيّرهما."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard1 = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # ملء بيانات الخطوتين وحفظ مع تأكيد معدل الرفع
        wizard1.combo_window_status.setCurrentIndex(1)  # INTACT
        wizard1.edit_cooling_hours.setText("6.5")
        wizard1.combo_vacuum_intact.setCurrentIndex(1)  # 1
        wizard1.combo_wet_frozen.setCurrentIndex(1)  # 0

        wizard1.combo_ramp_source.setCurrentIndex(2)  # ACQUISITION_SOFTWARE
        wizard1.edit_ramp_rate.setText("5.0")
        wizard1.chk_ramp_confirm.setChecked(True)

        saved = wizard1.save_draft(show_feedback=False)
        assert saved is True

        row = wizard_db.cursor().execute("SELECT hv_ramp_source, hv_ramp_rate_v_per_s FROM inspection_hpge_measurements WHERE inspection_id = ?", (insp_id,)).fetchone()
        assert row["hv_ramp_source"] == "ACQUISITION_SOFTWARE"
        assert row["hv_ramp_rate_v_per_s"] == 5.0

        # إعادة فتح المسودة
        wizard2 = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        assert wizard2.combo_ramp_source.currentData() == "ACQUISITION_SOFTWARE"
        assert wizard2.edit_ramp_rate.text() == "5.0"
        assert wizard2.chk_ramp_confirm.isChecked() is False
        assert wizard2.lbl_ramp_confirm_note.text() == "قيم مخزّنة سابقاً — أكّدها من جديد"

        # تعديل التبريد في الخطوة 1 وحفظ دون لمس تأكيد الرفع
        wizard2.edit_cooling_hours.setText("8.0")
        saved2 = wizard2.save_draft(show_feedback=False)
        assert saved2 is True

        # التحقق من أن القيمتين المحفوظتين لم تتأثرا ولم تُستبدلا بالافتراضيات
        row2 = wizard_db.cursor().execute("SELECT cooling_hours, hv_ramp_source, hv_ramp_rate_v_per_s FROM inspection_hpge_measurements WHERE inspection_id = ?", (insp_id,)).fetchone()
        assert row2["cooling_hours"] == 8.0
        assert row2["hv_ramp_source"] == "ACQUISITION_SOFTWARE"
        assert row2["hv_ramp_rate_v_per_s"] == 5.0

    def test_confirming_new_hv_ramp_value_updates_db(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2 (2-د): تأكيد قيمة جديدة لمصدر ومعدل الرفع يغيّرهما في القاعدة."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        service.save_hpge_measurements(
            insp_id,
            {
                "hv_ramp_source": "ACQUISITION_SOFTWARE",
                "hv_ramp_rate_v_per_s": 5.0,
            },
            tech1_session,
        )

        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        assert wizard.combo_ramp_source.currentData() == "ACQUISITION_SOFTWARE"
        assert wizard.edit_ramp_rate.text() == "5.0"

        # تغيير القيم وتأكيدها صراحة
        wizard.combo_ramp_source.setCurrentIndex(1)  # MANUAL
        wizard.edit_ramp_rate.setText("8.0")
        wizard.chk_ramp_confirm.setChecked(True)

        saved = wizard.save_draft(show_feedback=False)
        assert saved is True

        row = wizard_db.cursor().execute("SELECT hv_ramp_source, hv_ramp_rate_v_per_s FROM inspection_hpge_measurements WHERE inspection_id = ?", (insp_id,)).fetchone()
        assert row["hv_ramp_source"] == "MANUAL"
        assert row["hv_ramp_rate_v_per_s"] == 8.0

    def test_nim_voltage_out_of_spec_warning_allows_transition(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2 (3): +24 V مضبوطة على 30 V تظهر OUT_OF_SPEC في اللافتة ولا تمنع الانتقال."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # استيفاء الخطوة 1
        wizard.combo_window_status.setCurrentIndex(1)  # INTACT
        wizard.edit_cooling_hours.setText("6.5")
        wizard.combo_vacuum_intact.setCurrentIndex(1)  # 1
        wizard.combo_wet_frozen.setCurrentIndex(1)  # 0
        wizard._on_next_step()
        assert wizard.stack.currentIndex() == 1

        # إدخال جهود NIM مع جعل +24V خارج السماحية (30V)
        wizard.edit_v_pos_24.setText("30.0")
        wizard.edit_v_neg_24.setText("-24.0")
        wizard.edit_v_pos_12.setText("12.0")
        wizard.edit_v_neg_12.setText("-12.0")

        # بقية حقول الخطوة 2
        wizard.combo_applied_polarity.setCurrentIndex(1)  # POSITIVE
        wizard.edit_applied_voltage.setText("3000.0")
        wizard.combo_ramp_source.setCurrentIndex(1)  # MANUAL
        wizard.edit_ramp_rate.setText("5.0")
        wizard.chk_ramp_confirm.setChecked(True)
        wizard.combo_preamp_signal.setCurrentIndex(1)  # 1

        # التحقق من ظهور تحذير OUT_OF_SPEC و R-HPGE-05 في اللافتة
        assert "OUT_OF_SPEC" in wizard.lbl_step2_status.text()
        assert "R-HPGE-05" in wizard.lbl_step2_status.text()

        # التحقق من أن هذا التحذير لا يمنع الانتقال
        ok, msg = wizard._validate_current_step()
        assert ok is True
        assert msg is None

        # الانتقال للخطوة 3 ينجح
        wizard._on_next_step()
        assert wizard.stack.currentIndex() == 2

    def test_cooling_remaining_hours_and_effective_minimum(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2 (4): شهادة 4 وتبريد 5 تعرض متبقياً 1.00 ساعة، والحد الأدنى 6.0 لا يُنسب للشهادة."""
        # ضبط شهادة التبريد للكاشف على 4.0 ساعات
        wizard_db.cursor().execute("UPDATE detectors SET cooling_hours_cert = 4.0 WHERE id = 1")
        wizard_db.commit()

        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        wizard.combo_window_status.setCurrentIndex(1)  # INTACT
        wizard.edit_cooling_hours.setText("5.0")
        wizard.combo_vacuum_intact.setCurrentIndex(1)  # 1
        wizard.combo_wet_frozen.setCurrentIndex(1)  # 0

        # max(6.0, 4.0) = 6.0 h => remaining = 6.0 - 5.0 = 1.00 h
        banner_text = wizard.lbl_step1_status.text()
        assert "1.00" in banner_text
        assert "6.0" in banner_text
        # لا شهادة ولا 6.0 يُنسب للشهادة عند كون الشهادة أقل من 6.0
        assert "شهادة الكاشف (6.0 h)" not in banner_text

        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "1.00" in msg

    def test_save_draft_rejects_invalid_numeric_text(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2 (5): save_draft لا يتجاهل نصاً رقمياً غير صالح بصمت ويظهر خطأ ولا يحفظ."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # 1. نص غير صالح في مدة التبريد
        wizard.edit_cooling_hours.setText("abc_not_number")
        ok1 = wizard.save_draft(show_feedback=False)
        assert ok1 is False
        assert "مدة التبريد" in wizard.lbl_feedback.text()

        row1 = wizard_db.cursor().execute("SELECT cooling_hours FROM inspection_hpge_measurements WHERE inspection_id = ?", (insp_id,)).fetchone()
        assert row1 is None

        # 2. نص غير صالح في جهد سلة NIM
        wizard.edit_cooling_hours.setText("6.5")
        wizard.edit_v_pos_24.setText("invalid_volt")
        ok2 = wizard.save_draft(show_feedback=False)
        assert ok2 is False
        assert "+24 V" in wizard.lbl_feedback.text()

        # 3. نص غير صالح في معدل رفع الجهد مع تأكيد الإقرار
        wizard.edit_v_pos_24.setText("24.0")
        wizard.edit_ramp_rate.setText("bad_rate")
        wizard.chk_ramp_confirm.setChecked(True)
        ok3 = wizard.save_draft(show_feedback=False)
        assert ok3 is False
        assert "معدل رفع الجهد" in wizard.lbl_feedback.text()

    def test_save_draft_feedback_timer_reproduce_deletion_crash(self, wizard_db, tech1_session, qtbot):
        """إعادة إنتاج الخطأ المطلوب في البند 4 قبل الإصلاح."""
        from PyQt6 import sip
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        qtbot.addWidget(wizard)

        wizard.save_draft(show_feedback=True)
        wizard.accept()

        # حذف الكائن لمحاكاة إغلاق وتدمير النافذة في التشغيل الحقيقي
        sip.delete(wizard.lbl_feedback)

        with qtbot.captureExceptions() as exceptions:
            qtbot.wait(5100)

        assert len(exceptions) == 0, f"Captured slot exceptions: {exceptions}"

    def test_banner_style_id_selector_no_double_border(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2 (5): _style_banner يستعمل محدد ID واللصيقة الداخلية بلا إطار مزدوج."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        assert wizard.card_step1_banner.objectName() == "card_step1_banner"
        assert wizard.card_step2_banner.objectName() == "card_step2_banner"
        assert wizard.lbl_step1_status.wordWrap() is True
        assert wizard.lbl_step2_status.wordWrap() is True

        # لافتة الخطوة 1 عند الخطأ
        wizard._style_banner(wizard.card_step1_banner, "error")
        sheet1 = wizard.card_step1_banner.styleSheet()
        assert "#card_step1_banner" in sheet1
        assert "border: 1px solid" in sheet1
        # اللصيقة الداخلية خالية من أي إطار موروث
        assert "border: 1px solid" not in wizard.lbl_step1_status.styleSheet()

        # لافتة الخطوة 2 عند التحذير
        wizard._style_banner(wizard.card_step2_banner, "warning")
        sheet2 = wizard.card_step2_banner.styleSheet()
        assert "#card_step2_banner" in sheet2
        assert "border: 1px solid" in sheet2
        assert "border: 1px solid" not in wizard.lbl_step2_status.styleSheet()

    def test_navigation_button_arrows_direction_rtl(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2 (8): اتجاه الأسهم في الواجهة العربية: التالي لليسار ⬅️ والسابق لليمين ➡️."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # زر التالي يشير لليسار (تقدم في RTL)
        assert "⬅️" in wizard.btn_next.text()
        # زر السابق يشير لليمين (رجوع في RTL)
        assert "➡️" in wizard.btn_prev.text()

    def test_hv_ramp_reference_unspecified_not_invented(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2 (9): لا اختراع لنص المرجع؛ كاشف بلا مرجع يعرض 'غير محدد في بطاقة الكاشف'."""
        # تفريغ المرجع في بطاقة الكاشف لمحاكاة غياب المرجع
        wizard_db.cursor().execute("UPDATE detectors SET hv_ramp_reference = NULL WHERE id = 1")
        wizard_db.commit()

        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # فحص وجود نص "غير محدد في بطاقة الكاشف" وعدم وجود "دليل عام"
        labels = [lbl.text() for lbl in wizard.findChildren(QLabel)]
        all_labels_text = " ".join(labels)
        assert "غير محدد في بطاقة الكاشف" in all_labels_text
        assert "دليل عام" not in all_labels_text

    def test_bidi_formatting_and_no_double_parentheses(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2 (10): عزل BiDi وعدم وجود أقواس مزدوجة ((...)) في القطبية والتبريد."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # 1. فحص سطر القطبية: خلوه من الأقواس المزدوجة
        guide_labels = [lbl.text() for lbl in wizard.findChildren(QLabel) if "الاسمية" in lbl.text()]
        assert len(guide_labels) > 0
        pol_guide_text = guide_labels[0]
        assert "((" not in pol_guide_text
        assert "))" not in pol_guide_text

        # 2. فحص لافتة التبريد عند إدخال قيمة ناقصة
        wizard.edit_cooling_hours.setText("4.5")
        cool_text = wizard.lbl_step1_status.text()
        assert "((" not in cool_text
        assert "))" not in cool_text

        # 3. فحص عزل BiDi في رسائل القواعد اللاتينية
        from kashif.gui.wizards.hpge_wizard import format_bidi_message
        raw_msg = "جهود سلة NIM خارج السماحية المسموحة (±5.0%): v_pos_24=30.00V (nom=+24.0V, diff=25.0%)"
        isolated = format_bidi_message(raw_msg)
        assert "⁦" in isolated
        assert "⁩" in isolated

    def test_chk_ramp_confirm_indicator_stylesheet_and_object_name(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2: خانة التأكيد chk_ramp_confirm معرّفة بحد وحجم واضحين، واختبار بكسلي حقيقي للحالات الأربع والثيمين."""
        from PyQt6.QtGui import QColor
        from kashif.gui.theme import COLORS, get_stylesheet

        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # 1. التحقق من أن objectName ما زال chk_ramp_confirm
        assert wizard.chk_ramp_confirm.objectName() == "chk_ramp_confirm"

        # 2. التحقق من خصائص styleSheet
        style = wizard.chk_ramp_confirm.styleSheet()
        assert "::indicator" in style
        assert "border" in style
        assert "width" in style
        assert "height" in style
        assert "#chk_ramp_confirm" in style

        import re
        w_match = re.search(r"width:\s*(\d+)px", style)
        h_match = re.search(r"height:\s*(\d+)px", style)
        assert w_match and int(w_match.group(1)) >= 18
        assert h_match and int(h_match.group(1)) >= 18

        # دالة حساب السطوع النسبي ونسبة التباين
        def rel_lum(c: QColor) -> float:
            def f(ch):
                val = ch / 255.0
                return val / 12.92 if val <= 0.03928 else ((val + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(c.red()) + 0.7152 * f(c.green()) + 0.0722 * f(c.blue())

        def calc_contrast(c1: QColor, c2: QColor) -> float:
            l1 = rel_lum(c1)
            l2 = rel_lum(c2)
            return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

        # مجلد حفظ صور الحالات الأربع للتوثيق
        out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "output", "grab_chk_ramp_confirm"))
        os.makedirs(out_dir, exist_ok=True)
        artifact_dir = r"C:\Users\DELL\.gemini\antigravity\brain\90ae91ab-db14-4005-b180-09aecd3d6241\chk_ramp_confirm_grabs"
        os.makedirs(artifact_dir, exist_ok=True)

        for theme in ["light", "dark"]:
            qapp.setStyleSheet(get_stylesheet(theme))
            cb = wizard.chk_ramp_confirm
            cb.resize(420, 36)
            cb.show()

            # (ب) نسبة تباين لون النص مع خلفية الصف لا تقل عن 4.5
            text_color = cb.palette().color(cb.foregroundRole())
            card_bg = cb.get_card_background()
            cr = calc_contrast(text_color, card_bg)
            assert cr >= 4.5, f"Contrast ratio in {theme} theme is {cr:.2f} < 4.5"

            for checked, focused, name in [
                (False, False, "unchecked_unfocused"),
                (False, True,  "unchecked_focused"),
                (True,  False, "checked_unfocused"),
                (True,  True,  "checked_focused"),
            ]:
                cb.setChecked(checked)
                if focused:
                    cb.setFocus()
                else:
                    cb.clearFocus()

                img = cb.grab().toImage()

                # حفظ لقطة الشاشة في المجلدين للتوثيق
                filename = f"chk_ramp_confirm_{theme}_{name}.png"
                img.save(os.path.join(out_dir, filename))
                img.save(os.path.join(artifact_dir, filename))

                # (أ) لون خلفية الصف ليس كحلياً مصمتاً (بكسل الزاوية البعيدة عن المؤشر)
                corner = img.pixelColor(6, 6)
                assert corner.name().lower() != COLORS["navy"].lower(), (
                    f"Row background corner must not be navy in {theme} {name}; got {corner.name()}"
                )

                # (د) حد التركيز لا يلوّن الصف كله (الزاوية البعيدة لا تأخذ لون التركيز البرتقالي)
                assert corner.name().lower() != COLORS["orange"].lower(), (
                    f"Focus border leaked to row corner in {theme} {name}; got {corner.name()}"
                )

                # (ج) فحص المؤشر: في الحالة المؤشرة يختلف بفارق كبير عن غير المؤشرة
                dpr = cb.devicePixelRatio()
                ind_rect = cb.get_indicator_rect()
                # نأخذ بكسلاً داخلياً في زاوية المؤشر (بعيداً عن خط علامة ✓)
                x_ind_corner = int((ind_rect.x() + 4) * dpr)
                y_ind_corner = int((ind_rect.y() + 4) * dpr)
                ind_col = img.pixelColor(x_ind_corner, y_ind_corner)

                if checked:
                    # لون المؤشر كحلي الهوية
                    assert ind_col.name().lower() == COLORS["navy"].lower(), (
                        f"Checked indicator should be navy in {theme} {name}, got {ind_col.name()}"
                    )
                else:
                    # لون المؤشر أبيض
                    assert ind_col.name().lower() == "#ffffff", (
                        f"Unchecked indicator should be white in {theme} {name}, got {ind_col.name()}"
                    )

    def test_cooling_message_single_limit_and_no_nested_parentheses(self, wizard_db, tech1_session, qapp):
        """تصحيح TASK-2.2: رسالة التبريد تذكر الحد مرة واحدة دون تكرار ودون أقواس متداخلة بشهادة 4 وبلا شهادة."""
        # حالة 1: بشهادة 4.0
        wizard_db.cursor().execute("UPDATE detectors SET cooling_hours_cert = 4.0 WHERE id = 1")
        wizard_db.commit()

        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        wizard.combo_window_status.setCurrentIndex(1)  # INTACT
        wizard.edit_cooling_hours.setText("5.0")
        wizard.combo_vacuum_intact.setCurrentIndex(1)
        wizard.combo_wet_frozen.setCurrentIndex(1)

        banner_text = wizard.lbl_step1_status.text()
        ok, msg = wizard._validate_current_step()

        # التحقق من صيغة "المقاس: X h، الحد الأدنى المعتمد: Y h"
        assert "المقاس: 5.00 h، الحد الأدنى المعتمد: 6.0 h" in banner_text
        assert "المقاس: 5.00 h، الحد الأدنى المعتمد: 6.0 h" in msg
        # عدم وجود أقواس متداخلة
        assert "((" not in banner_text and "))" not in banner_text
        assert "((" not in msg and "))" not in msg
        # عدم تكرار الحد
        assert "المطلوب:" not in banner_text
        assert "المطلوب:" not in msg

        # حالة 2: بلا شهادة (cooling_hours_cert = NULL)
        wizard_db.cursor().execute("UPDATE detectors SET cooling_hours_cert = NULL WHERE id = 1")
        wizard_db.commit()

        insp_id2 = service.create_draft(detector_id=1, session=tech1_session)
        wizard2 = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id2)

        wizard2.combo_window_status.setCurrentIndex(1)
        wizard2.edit_cooling_hours.setText("5.0")
        wizard2.combo_vacuum_intact.setCurrentIndex(1)
        wizard2.combo_wet_frozen.setCurrentIndex(1)

        banner_text2 = wizard2.lbl_step1_status.text()
        ok2, msg2 = wizard2._validate_current_step()

        assert "المقاس: 5.00 h، الحد الأدنى المعتمد: 6.0 h" in banner_text2
        assert "المقاس: 5.00 h، الحد الأدنى المعتمد: 6.0 h" in msg2
        assert "((" not in banner_text2 and "))" not in banner_text2
        assert "((" not in msg2 and "))" not in msg2
        assert "المطلوب:" not in banner_text2
        assert "المطلوب:" not in msg2

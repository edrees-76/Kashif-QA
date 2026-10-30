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

# ---------------------------------------------------------------------------
# Helpers for Steps 1-4 Filling
# ---------------------------------------------------------------------------

def _fill_step_1_valid(wizard: HPGeInspectionWizard) -> None:
    wizard.combo_window_status.setCurrentIndex(1)  # INTACT
    wizard.edit_cooling_hours.setText("7.0")
    wizard.combo_vacuum_intact.setCurrentIndex(1)  # سليم 1
    wizard.combo_wet_frozen.setCurrentIndex(2)  # لا يوجد رطوبة 0

def _fill_step_2_valid(wizard: HPGeInspectionWizard) -> None:
    wizard.edit_v_pos_24.setText("24.0")
    wizard.edit_v_neg_24.setText("-24.0")
    wizard.edit_v_pos_12.setText("12.0")
    wizard.edit_v_neg_12.setText("-12.0")
    wizard.combo_applied_polarity.setCurrentIndex(1)  # POSITIVE
    wizard.edit_applied_voltage.setText("3000.0")
    wizard.combo_ramp_source.setCurrentIndex(1)  # MANUAL
    wizard.edit_ramp_rate.setText("8.0")
    wizard.chk_ramp_confirm.setChecked(True)
    wizard.combo_preamp_signal.setCurrentIndex(1)  # سليم 1

def _fill_step_3_valid(wizard: HPGeInspectionWizard) -> None:
    wizard.edit_shaping_time.setText("4.0")
    wizard.edit_coarse_gain.setText("50.0")
    wizard.edit_fine_gain.setText("1.0")
    wizard.edit_baseline_restorer.setText("AUTO")
    wizard.edit_pole_zero_setting.setText("OPTIMIZED")
    wizard.edit_amp_output_amplitude.setText("5.0")
    wizard.combo_pz_distortion.setCurrentIndex(1)  # لا يوجد تشوه (0)

def _fill_step_4_valid(wizard: HPGeInspectionWizard) -> None:
    wizard.edit_total_count_rate_cps.setText("1200.0")
    wizard.edit_dead_time_pct.setText("3.0")
    wizard.edit_live_time_sec.setText("300.0")
    wizard.edit_p1_centroid.setText("2346.4")
    wizard.edit_p1_fwhm_ch.setText("3.60")
    wizard.edit_p1_fwtm_ch.setText("6.65")
    wizard.edit_p1_net_area.setText("15000.0")
    wizard.edit_p2_centroid.setText("2665.0")
    wizard.edit_p2_fwhm_ch.setText("3.70")
    wizard.edit_p2_fwtm_ch.setText("6.85")
    wizard.edit_p2_net_area.setText("12500.0")

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
        # وتبقى في قاعدة البيانات NULL بلا افتراضات محقونة (TASK-2.2b)
        full = service.get_inspection_full(insp_id)
        assert full["hpge_measurements"] is None or (
            full["hpge_measurements"]["hv_ramp_source"] is None
            and full["hpge_measurements"]["hv_ramp_rate_v_per_s"] is None
        )

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

    def test_step_5_placeholder_prevents_submission(self, wizard_db, tech1_session, qapp):
        """الخطوة 5 صفحة فارغة معلَّمة 'قيد التطوير' ولا تسمح بالإنهاء أو التقديم في هذه المهمة."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # التحقق من أن الخطوة 5 صفحة قيد التطوير
        wizard.stack.setCurrentIndex(4)  # الخطوة 5
        lbl_p5 = wizard.findChild(QLabel, "lbl_placeholder_step_5")
        assert lbl_p5 is not None
        assert "قيد التطوير" in lbl_p5.text()

        # زر التالي معطل في الخطوة 5 ولا يوجد زر إنهاء
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
        assert wizard2.lbl_ramp_confirm_note.text() == "مؤكَّدة في جلسة سابقة"

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

    def test_save_draft_rejects_invalid_numeric_text(self, wizard_db, tech1_session, monkeypatch, qapp):
        """تصحيح TASK-2.2 (5) وتصحيح TASK-2.3a (1): save_draft لا يتجاهل نصاً غير صالح، يظهر QMessageBox.warning بعنوان تنبيه قبل المتابعة ولا يمرر أخطاء عبر lbl_feedback."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard.show()
        qapp.processEvents()

        warnings_called = []
        def fake_warning(parent, title, text, *args, **kwargs):
            warnings_called.append((title, text))
            return QMessageBox.StandardButton.Ok
        monkeypatch.setattr(QMessageBox, "warning", fake_warning)

        # 1. نص غير صالح في مدة التبريد مع show_feedback=True
        wizard.edit_cooling_hours.setText("abc_not_number")
        ok1 = wizard.save_draft(show_feedback=True)
        assert ok1 is False
        assert len(warnings_called) == 1
        assert warnings_called[0][0] == "تنبيه قبل المتابعة"
        assert "مدة التبريد" in warnings_called[0][1]
        assert wizard.edit_cooling_hours.hasFocus()
        assert wizard.lbl_feedback.text() == ""

        row1 = wizard_db.cursor().execute("SELECT cooling_hours FROM inspection_hpge_measurements WHERE inspection_id = ?", (insp_id,)).fetchone()
        assert row1 is None

        # 2. نص غير صالح في جهد سلة NIM مع show_feedback=True
        warnings_called.clear()
        wizard.edit_cooling_hours.setText("6.5")
        wizard.edit_v_pos_24.setText("invalid_volt")
        ok2 = wizard.save_draft(show_feedback=True)
        assert ok2 is False
        assert len(warnings_called) == 1
        assert warnings_called[0][0] == "تنبيه قبل المتابعة"
        assert "+24 V" in warnings_called[0][1]
        assert wizard.edit_v_pos_24.hasFocus()
        assert wizard.lbl_feedback.text() == ""

        # 3. نص غير صالح في معدل رفع الجهد مع تأكيد الإقرار مع show_feedback=True
        warnings_called.clear()
        wizard.edit_v_pos_24.setText("24.0")
        wizard.edit_ramp_rate.setText("bad_rate")
        wizard.chk_ramp_confirm.setChecked(True)
        ok3 = wizard.save_draft(show_feedback=True)
        assert ok3 is False
        assert len(warnings_called) == 1
        assert warnings_called[0][0] == "تنبيه قبل المتابعة"
        assert "معدل رفع الجهد" in warnings_called[0][1]
        assert wizard.edit_ramp_rate.hasFocus()
        assert wizard.lbl_feedback.text() == ""

        # 4. الحفظ التلقائي عند الانتقال (show_feedback=False): لا يكرر نافذة التنبيه ولا يمرر أخطاء لـ lbl_feedback
        warnings_called.clear()
        ok4 = wizard.save_draft(show_feedback=False)
        assert ok4 is False
        assert len(warnings_called) == 0
        assert wizard.lbl_feedback.text() == ""

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


    def test_wizard_hv_ramp_lifecycle_draft_null_and_reopen_preservation(self, wizard_db, tech1_session, qapp):
        """(TASK-2.2b البند 4) مسودة جديدة تحفظ الخطوة 1 فقط لا تكتب شيئاً في العمودين (NULL)؛
        بعد التأكيد تُكتب القيمتان؛ إعادة الفتح والحفظ دون تأكيد تحفظ القيم كما هي؛
        وعندما تكون القيم NULL يُترك الحقلان فارغين بلا أي نص 'مخزّنة سابقاً'."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)

        # 1. فتح المعالج على المسودة الجديدة وتعبئة وحفظ الخطوة 1 فقط
        wizard1 = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard1.combo_window_status.setCurrentIndex(1)  # INTACT
        wizard1.edit_cooling_hours.setText("7.0")
        wizard1.combo_vacuum_intact.setCurrentIndex(1)  # 1
        wizard1.combo_wet_frozen.setCurrentIndex(1)  # 0
        assert wizard1.save_draft(show_feedback=False) is True

        # التحقق من أن العمودين في القاعدة ما زالا NULL تماماً
        row1 = wizard_db.cursor().execute(
            "SELECT hv_ramp_source, hv_ramp_rate_v_per_s FROM inspection_hpge_measurements WHERE inspection_id = ?",
            (insp_id,),
        ).fetchone()
        assert row1["hv_ramp_source"] is None
        assert row1["hv_ramp_rate_v_per_s"] is None

        # 2. إعادة فتح المسودة: الحقلان فارغان ولا يظهر نص 'مخزّنة سابقاً' أو 'مؤكدة'
        wizard2 = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        assert wizard2.combo_ramp_source.currentIndex() == 0
        assert wizard2.combo_ramp_source.currentData() is None
        assert wizard2.edit_ramp_rate.text() == ""
        assert wizard2.chk_ramp_confirm.isChecked() is False
        assert wizard2.lbl_ramp_confirm_note.text() == ""
        assert wizard2.lbl_ramp_confirm_note.isVisible() is False

        # حفظ مجدداً دون تأكيد لا يكتب شيئاً في العمودين
        assert wizard2.save_draft(show_feedback=False) is True
        row2 = wizard_db.cursor().execute(
            "SELECT hv_ramp_source, hv_ramp_rate_v_per_s FROM inspection_hpge_measurements WHERE inspection_id = ?",
            (insp_id,),
        ).fetchone()
        assert row2["hv_ramp_source"] is None
        assert row2["hv_ramp_rate_v_per_s"] is None

        # 3. إدخال قيم الرفع وتأكيدها صراحة ثم الحفظ
        wizard2.combo_ramp_source.setCurrentIndex(2)  # ACQUISITION_SOFTWARE
        wizard2.edit_ramp_rate.setText("8.0")
        wizard2.chk_ramp_confirm.setChecked(True)
        assert wizard2.save_draft(show_feedback=False) is True

        row3 = wizard_db.cursor().execute(
            "SELECT hv_ramp_source, hv_ramp_rate_v_per_s FROM inspection_hpge_measurements WHERE inspection_id = ?",
            (insp_id,),
        ).fetchone()
        assert row3["hv_ramp_source"] == "ACQUISITION_SOFTWARE"
        assert row3["hv_ramp_rate_v_per_s"] == 8.0

        # 4. إعادة الفتح الآن: القيمتان تظهران مع نص 'مؤكَّدة في جلسة سابقة' وخانة التأكيد غير مؤشرة
        wizard3 = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard3.show()
        wizard3.stack.setCurrentIndex(1)  # الانتقال للخطوة 2 حيث توجد حقول الرفع وملاحظة التأكيد
        assert wizard3.combo_ramp_source.currentData() == "ACQUISITION_SOFTWARE"
        assert wizard3.edit_ramp_rate.text() == "8.0"
        assert wizard3.chk_ramp_confirm.isChecked() is False
        assert wizard3.lbl_ramp_confirm_note.text() == "مؤكَّدة في جلسة سابقة"
        assert wizard3.lbl_ramp_confirm_note.isVisible() is True

        # حفظ دون تأكيد يحافظ على القيم المخزنة كما هي
        assert wizard3.save_draft(show_feedback=False) is True
        row4 = wizard_db.cursor().execute(
            "SELECT hv_ramp_source, hv_ramp_rate_v_per_s FROM inspection_hpge_measurements WHERE inspection_id = ?",
            (insp_id,),
        ).fetchone()
        assert row4["hv_ramp_source"] == "ACQUISITION_SOFTWARE"
        assert row4["hv_ramp_rate_v_per_s"] == 8.0

    def test_step2_full_grab_contrast_light_and_dark(self, wizard_db, tech1_session, qapp):
        """(TASK-2.2b البند 5) تصوير (grab) الصفحة 2 كاملة في الثيمين الداكن والفاتح
        والتأكد من أن نسبة التباين لنص عنوان المؤقت وعناوين الحقول >= 4.5 مقابل خلفيتها."""
        from kashif.gui.theme import set_mode, get_stylesheet, COLORS
        from PyQt6.QtGui import QColor
        from PyQt6.QtWidgets import QFrame, QFormLayout
        import os

        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)

        def rel_lum(c: QColor) -> float:
            def f(ch):
                val = ch / 255.0
                return val / 12.92 if val <= 0.03928 else ((val + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(c.red()) + 0.7152 * f(c.green()) + 0.0722 * f(c.blue())

        def calc_contrast(c1: QColor, c2: QColor) -> float:
            l1 = rel_lum(c1)
            l2 = rel_lum(c2)
            return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

        # مجلدات حفظ لقطات الصفحة 2 للتوثيق
        out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "output", "grab_step2"))
        os.makedirs(out_dir, exist_ok=True)
        artifact_dir = r"C:\Users\DELL\.gemini\antigravity\brain\5c0a7800-2b5d-45f4-a075-cc02dc05861d\step2_grabs"
        os.makedirs(artifact_dir, exist_ok=True)

        for theme in ["light", "dark"]:
            set_mode(theme)
            qapp.setStyleSheet(get_stylesheet(theme))

            wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
            wizard.setStyleSheet(get_stylesheet(theme))
            wizard.resize(900, 700)
            wizard.show()

            # الانتقال للصفحة 2
            wizard.stack.setCurrentIndex(1)
            page2 = wizard.stack.widget(1)
            assert page2 is not None

            # تصوير الصفحة 2 كاملة (Full Grab)
            img = page2.grab().toImage()
            assert img.width() > 0 and img.height() > 0

            # حفظ اللقطة للتوثيق في المجلدين
            fname = f"hpge_wizard_step2_{theme}.png"
            img.save(os.path.join(out_dir, fname))
            img.save(os.path.join(artifact_dir, fname))

            # خلفية البطاقة المعتمدة لكل ثيم
            card_bg = QColor(COLORS["dark_card"] if theme == "dark" else COLORS["light_card"])

            # 1. فحص نسبة التباين لعنوان المؤقت
            timer_hdr = page2.findChild(QLabel, "lbl_timer_header")
            assert timer_hdr is not None
            th_color = timer_hdr.palette().color(timer_hdr.foregroundRole())
            cr_hdr = calc_contrast(th_color, card_bg)
            assert cr_hdr >= 4.5, f"Timer header contrast ratio in {theme} theme is {cr_hdr:.2f} < 4.5"

            # 2. فحص نسبة التباين لعنوان قسم NIM
            lbl_nim = page2.findChild(QLabel, "lbl_nim_section")
            assert lbl_nim is not None
            nim_color = lbl_nim.palette().color(lbl_nim.foregroundRole())
            cr_nim = calc_contrast(nim_color, card_bg)
            assert cr_nim >= 4.5, f"NIM section header contrast ratio in {theme} theme is {cr_nim:.2f} < 4.5"

            # 3. فحص عناوين الحقول في استمارة الخطوة 2
            form_cards = [w for w in page2.findChildren(QFrame) if w.property("class") == "card"]
            assert len(form_cards) >= 1
            for fc in form_cards:
                fl = fc.findChild(QFormLayout)
                if fl:
                    for row_i in range(fl.rowCount()):
                        item = fl.itemAt(row_i, QFormLayout.ItemRole.LabelRole)
                        if item and item.widget() and isinstance(item.widget(), QLabel):
                            w_lbl = item.widget()
                            if not w_lbl.text().strip():
                                continue
                            lbl_color = w_lbl.palette().color(w_lbl.foregroundRole())
                            cr_f = calc_contrast(lbl_color, card_bg)
                            assert cr_f >= 4.5, f"Field label '{w_lbl.text()}' contrast in {theme} is {cr_f:.2f} < 4.5"

            wizard.close()

    def test_step3_and_step4_no_preselected_values(self, wizard_db, tech1_session, qapp):
        """التحقق من عدم وجود أي خيارات أو قيم مسبقة في الحقول الإلزامية للخطوتين 3 و 4."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # الخطوة 3
        assert wizard.combo_pz_distortion.currentData() is None
        assert wizard.edit_shaping_time.text() == ""
        assert wizard.edit_coarse_gain.text() == ""
        assert wizard.edit_fine_gain.text() == ""
        assert wizard.edit_baseline_restorer.text() == ""
        assert wizard.edit_pole_zero_setting.text() == ""
        assert wizard.edit_amp_output_amplitude.text() == ""

        # الخطوة 4
        assert wizard.edit_total_count_rate_cps.text() == ""
        assert wizard.edit_dead_time_pct.text() == ""
        assert wizard.edit_live_time_sec.text() == ""
        assert wizard.edit_p1_centroid.text() == ""
        assert wizard.edit_p1_fwhm_ch.text() == ""
        assert wizard.edit_p1_fwtm_ch.text() == ""
        assert wizard.edit_p1_net_area.text() == ""
        assert wizard.edit_p2_centroid.text() == ""
        assert wizard.edit_p2_fwhm_ch.text() == ""
        assert wizard.edit_p2_fwtm_ch.text() == ""
        assert wizard.edit_p2_net_area.text() == ""
        assert wizard.edit_slope_m.text() == ""
        assert wizard.edit_intercept_b.text() == ""
        assert wizard.edit_measured_fwhm_kev.text() == ""
        assert wizard.edit_measured_fwtm_kev.text() == ""
        assert wizard.edit_form_factor.text() == ""
        assert wizard.edit_net_counts_1332.text() == ""

    def test_step3_and_step4_missing_mandatory_fields_prevent_transition(self, wizard_db, tech1_session, qapp):
        """منع العبور عند نقص أي حقل إلزامي في الخطوتين 3 و 4 مع رسالة تسمّي الحقل الناقص."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        _fill_step_1_valid(wizard)
        _fill_step_2_valid(wizard)

        # اختبار الخطوة 3: نقص حقل has_pz_distortion
        wizard.stack.setCurrentIndex(2)
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "P/Z" in msg

        # تعبئة الخطوة 3 والانتقال للخطوة 4
        _fill_step_3_valid(wizard)
        ok, _ = wizard._validate_current_step()
        assert ok is True

        # اختبار الخطوة 4: نقص حقول تدريجياً
        wizard.stack.setCurrentIndex(3)
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "معدل النبضات" in msg

        wizard.edit_total_count_rate_cps.setText("1200.0")
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "زمن الموت" in msg

        wizard.edit_dead_time_pct.setText("3.0")
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "زمن القياس الحي" in msg

        wizard.edit_live_time_sec.setText("300.0")
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "1173.2" in msg and "القناة" in msg

    def test_step4_wait_on_r_hpge_11_blocks_step5(self, wizard_db, tech1_session, qapp):
        """قاعدة R-HPGE-11 تعيد WAIT عند نقص مساحة قمة 1332.5 عن 10,000 نبضة وتمنع الانتقال للخطوة 5."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        _fill_step_1_valid(wizard)
        _fill_step_2_valid(wizard)
        _fill_step_3_valid(wizard)
        _fill_step_4_valid(wizard)

        wizard.stack.setCurrentIndex(3)

        # جعل مساحة قمة 1332.5 = 7500 نبضة (< 10,000)
        wizard.edit_p2_net_area.setText("7500.0")

        # التحقق من أن التقييم الحي يعرض لافتة WAIT
        assert "R-HPGE-11" in wizard.lbl_step4_status.text()
        assert ("10000" in wizard.lbl_step4_status.text() or "10,000" in wizard.lbl_step4_status.text())

        # التحقق من أن _validate_current_step تمنع المتابعة
        ok, msg = wizard._validate_current_step()
        assert ok is False
        assert "R-HPGE-11" in msg
        assert ("10000" in msg or "10,000" in msg)

        # محاولة النقر على التالي لا تنتقل للخطوة 5
        wizard._on_next_step()
        assert wizard.stack.currentIndex() == 3

    def test_step3_and_step4_out_of_spec_and_failure_allow_transition(self, wizard_db, tech1_session, qapp):
        """حالات OUT_OF_SPEC و FAILURE في الخطوتين 3 و 4 تظهر بلافتة واضحة وتسمح بالعبور دون توقف مبكر."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        _fill_step_1_valid(wizard)
        _fill_step_2_valid(wizard)

        # 1. الخطوة 3: تشوه نبضة P/Z يعطي OUT_OF_SPEC ويسمح بالعبور
        wizard.stack.setCurrentIndex(2)
        _fill_step_3_valid(wizard)
        wizard.combo_pz_distortion.setCurrentIndex(2)  # نعم (يوجد تشوه)
        assert "R-HPGE-09" in wizard.lbl_step3_status.text()
        ok, _ = wizard._validate_current_step()
        assert ok is True

        wizard._on_next_step()
        assert wizard.stack.currentIndex() == 3

        # 2. الخطوة 4: معدل العد > 2000 (OUT_OF_SPEC) و FWHM > 1.25 * nominal (FAILURE)
        _fill_step_4_valid(wizard)
        wizard.edit_total_count_rate_cps.setText("2800.0")  # > 2000 -> R-HPGE-10 OUT_OF_SPEC
        wizard.edit_p2_fwhm_ch.setText("6.5")  # يعطي FWHM عالي جداً -> FAILURE
        wizard.edit_p2_fwtm_ch.setText("14.0")  # FWTM > FWHM
        wizard.edit_p2_net_area.setText("15000.0")  # >= 10000 لتجاوز WAIT

        # اللافتة تحوي التنبيهات
        assert "R-HPGE-10" in wizard.lbl_step4_status.text()
        assert "FAILURE" in wizard.lbl_step4_status.text() or "عطل" in wizard.lbl_step4_status.text()

        # التحقق يمر بنجاح لأن OUT_OF_SPEC و FAILURE لا يمنعان الخطوة 4
        ok, msg = wizard._validate_current_step()
        assert ok is True
        assert msg is None

        # الانتقال إلى الخطوة 5
        wizard._on_next_step()
        assert wizard.stack.currentIndex() == 4

    def test_step4_calculations_match_core_functions(self, wizard_db, tech1_session, qapp):
        """التأكد من أن حسابات m و b و FWHM و Form Factor في الواجهة تطابق دوال core على قيم معروفة."""
        from kashif.core.spectrum import calibrate_co60
        from kashif.core.physics import fwhm_kev, fwtm_kev, form_factor

        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        ch1, ch2 = 2346.4, 2665.0
        w2, t2 = 3.70, 6.85
        net_counts = 12500.0

        wizard.edit_p1_centroid.setText(str(ch1))
        wizard.edit_p1_fwhm_ch.setText("3.60")
        wizard.edit_p1_fwtm_ch.setText("6.65")
        wizard.edit_p1_net_area.setText("15000.0")

        wizard.edit_p2_centroid.setText(str(ch2))
        wizard.edit_p2_fwhm_ch.setText(str(w2))
        wizard.edit_p2_fwtm_ch.setText(str(t2))
        wizard.edit_p2_net_area.setText(str(net_counts))

        # حسابات core المرجعية
        exp_m, exp_b = calibrate_co60(ch1, ch2)
        exp_fwhm = fwhm_kev(w2, exp_m)
        exp_fwtm = fwtm_kev(t2, exp_m)
        exp_ff = form_factor(t2, w2)

        # مقارنة ما تعرضه الحقول المحسوبة
        assert abs(float(wizard.edit_slope_m.text()) - exp_m) < 1e-5
        assert abs(float(wizard.edit_intercept_b.text()) - exp_b) < 1e-4
        assert abs(float(wizard.edit_measured_fwhm_kev.text()) - exp_fwhm) < 1e-3
        assert abs(float(wizard.edit_measured_fwtm_kev.text()) - exp_fwtm) < 1e-3
        assert abs(float(wizard.edit_form_factor.text()) - exp_ff) < 1e-3
        assert float(wizard.edit_net_counts_1332.text()) == net_counts

        # التأكد من أن الحقول للقراءة فقط
        assert wizard.edit_slope_m.isReadOnly() is True
        assert wizard.edit_intercept_b.isReadOnly() is True
        assert wizard.edit_measured_fwhm_kev.isReadOnly() is True
        assert wizard.edit_form_factor.isReadOnly() is True

    def test_step4_save_peaks_sends_full_list_preserves_first_peak(self, wizard_db, tech1_session, qapp):
        """التحقق من أن حفظ القمم يرسل القائمة الكاملة دائماً ولا يحذف القمة الأولى عند إدخال الثانية."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        _fill_step_1_valid(wizard)
        _fill_step_2_valid(wizard)
        _fill_step_3_valid(wizard)

        # إدخال القمة الأولى فقط مع زمن القياس
        wizard.edit_live_time_sec.setText("300.0")
        wizard.edit_p1_centroid.setText("2346.4")
        wizard.edit_p1_fwhm_ch.setText("3.60")
        wizard.edit_p1_fwtm_ch.setText("6.65")
        wizard.edit_p1_net_area.setText("15000.0")

        # حفظ المسودة
        assert wizard.save_draft(show_feedback=False) is True

        # التحقق في قاعدة البيانات من وجود قمة واحدة
        full_1 = service.get_inspection_full(insp_id)
        assert len(full_1["peaks"]) == 1
        assert "1173" in full_1["peaks"][0]["peak_name"]
        assert full_1["peaks"][0]["centroid_channel"] == 2346.4

        # إدخال القمة الثانية في نفس الحوار دون مسح الأولى
        wizard.edit_p2_centroid.setText("2665.0")
        wizard.edit_p2_fwhm_ch.setText("3.70")
        wizard.edit_p2_fwtm_ch.setText("6.85")
        wizard.edit_p2_net_area.setText("12500.0")

        # حفظ المسودة من جديد
        assert wizard.save_draft(show_feedback=False) is True

        # التحقق في قاعدة البيانات من حفظ القمتين معاً دون ضياع الأولى
        full_2 = service.get_inspection_full(insp_id)
        assert len(full_2["peaks"]) == 2
        p_names = [p["peak_name"] for p in full_2["peaks"]]
        assert any("1173" in n for n in p_names)
        assert any("1332" in n for n in p_names)

    def test_step3_and_step4_draft_lifecycle_save_and_reopen(self, wizard_db, tech1_session, qapp):
        """دورة حياة المسودة للخطوتين 3 و 4: حفظ البيانات ثم إعادة فتحها وإعادة احتساب الحقول المحسوبة."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)

        # الجلسة الأولى: إدخال وحفظ
        wizard1 = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        _fill_step_1_valid(wizard1)
        _fill_step_2_valid(wizard1)
        _fill_step_3_valid(wizard1)
        _fill_step_4_valid(wizard1)

        assert wizard1.save_draft(show_feedback=False) is True
        wizard1.close()

        # الجلسة الثانية: إعادة فتح نفس الفحص
        wizard2 = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)

        # استرجاع الخطوة 3
        assert wizard2.edit_shaping_time.text() == "4.0"
        assert wizard2.edit_coarse_gain.text() == "50.0"
        assert wizard2.edit_fine_gain.text() == "1.0"
        assert wizard2.edit_baseline_restorer.text() == "AUTO"
        assert wizard2.edit_pole_zero_setting.text() == "OPTIMIZED"
        assert wizard2.edit_amp_output_amplitude.text() == "5.0"
        assert wizard2.combo_pz_distortion.currentData() == 0

        # استرجاع الخطوة 4
        assert wizard2.edit_total_count_rate_cps.text() == "1200.0"
        assert wizard2.edit_dead_time_pct.text() == "3.0"
        assert wizard2.edit_live_time_sec.text() == "300.0"

        assert wizard2.edit_p1_centroid.text() == "2346.4"
        assert float(wizard2.edit_p1_fwhm_ch.text()) == 3.60
        assert wizard2.edit_p2_centroid.text() == "2665.0"

        # الحقول المحسوبة أُعيد احتسابها من المدخلات تلقائياً
        assert wizard2.edit_slope_m.text() != ""
        assert abs(float(wizard2.edit_slope_m.text()) - 0.5) < 1e-4
        assert abs(float(wizard2.edit_measured_fwhm_kev.text()) - 1.85) < 1e-2

        wizard2.close()

    def test_step3_and_step4_read_only_mode_for_non_owner(self, wizard_db, tech1_session, tech2_session, qapp):
        """فتح مسودة من قبل فني غير المالك يعرض الخطوتين 3 و 4 بمدخلات معطلة للقراءة فقط."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)

        # حفظ بيانات من المالك
        w_owner = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        _fill_step_1_valid(w_owner)
        _fill_step_2_valid(w_owner)
        _fill_step_3_valid(w_owner)
        _fill_step_4_valid(w_owner)
        assert w_owner.save_draft(show_feedback=False) is True
        w_owner.close()

        # فتح الفحص بواسطة tech2 (غير المالك)
        w_viewer = HPGeInspectionWizard(conn=wizard_db, session=tech2_session, inspection_id=insp_id)

        # الخطوة 3 معطلة للقراءة فقط
        assert w_viewer.edit_shaping_time.isReadOnly() is True
        assert w_viewer.edit_coarse_gain.isReadOnly() is True
        assert w_viewer.edit_fine_gain.isReadOnly() is True
        assert w_viewer.edit_baseline_restorer.isReadOnly() is True
        assert w_viewer.edit_pole_zero_setting.isReadOnly() is True
        assert w_viewer.edit_amp_output_amplitude.isReadOnly() is True
        assert w_viewer.combo_pz_distortion.isEnabled() is False

        # الخطوة 4 معطلة للقراءة فقط
        assert w_viewer.edit_total_count_rate_cps.isReadOnly() is True
        assert w_viewer.edit_dead_time_pct.isReadOnly() is True
        assert w_viewer.edit_live_time_sec.isReadOnly() is True
        assert w_viewer.edit_p1_centroid.isReadOnly() is True
        assert w_viewer.edit_p1_fwhm_ch.isReadOnly() is True
        assert w_viewer.edit_p2_centroid.isReadOnly() is True
        assert w_viewer.edit_p2_fwhm_ch.isReadOnly() is True

        assert w_viewer.btn_save_draft.isEnabled() is False
        w_viewer.close()

    def test_step3_and_step4_full_grab_contrast_light_and_dark(self, wizard_db, tech1_session, qapp):
        """تصوير كامل (Full Grab) لصفحتي 3 و 4 في الوضعين الفاتح والداكن، والتحقق من نسبة التباين >= 4.5."""
        from kashif.gui.theme import set_mode, get_stylesheet, COLORS
        from PyQt6.QtGui import QColor
        from PyQt6.QtWidgets import QFrame, QFormLayout
        import os

        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)

        def rel_lum(c: QColor) -> float:
            def f(ch):
                val = ch / 255.0
                return val / 12.92 if val <= 0.03928 else ((val + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(c.red()) + 0.7152 * f(c.green()) + 0.0722 * f(c.blue())

        def calc_contrast(c1: QColor, c2: QColor) -> float:
            l1 = rel_lum(c1)
            l2 = rel_lum(c2)
            return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

        out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "output", "grab_step3_step4"))
        os.makedirs(out_dir, exist_ok=True)
        artifact_dir = r"C:\Users\DELL\.gemini\antigravity\brain\5c0a7800-2b5d-45f4-a075-cc02dc05861d\step3_step4_grabs"
        os.makedirs(artifact_dir, exist_ok=True)

        for theme in ["light", "dark"]:
            set_mode(theme)
            qapp.setStyleSheet(get_stylesheet(theme))

            wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
            wizard.setStyleSheet(get_stylesheet(theme))
            wizard.resize(920, 760)
            wizard.show()

            card_bg = QColor(COLORS["dark_card"] if theme == "dark" else COLORS["light_card"])

            # --- فحص الصفحة 3 ---
            wizard.stack.setCurrentIndex(2)
            page3 = wizard.stack.widget(2)
            assert page3 is not None

            img3 = page3.grab().toImage()
            assert img3.width() > 0 and img3.height() > 0
            fname3 = f"hpge_wizard_step3_{theme}.png"
            img3.save(os.path.join(out_dir, fname3))
            img3.save(os.path.join(artifact_dir, fname3))

            cards_3 = [w for w in page3.findChildren(QFrame) if w.property("class") == "card"]
            assert len(cards_3) >= 1
            for fc in cards_3:
                fl = fc.findChild(QFormLayout)
                if fl:
                    for row_i in range(fl.rowCount()):
                        item = fl.itemAt(row_i, QFormLayout.ItemRole.LabelRole)
                        if item and item.widget() and isinstance(item.widget(), QLabel):
                            w_lbl = item.widget()
                            if not w_lbl.text().strip():
                                continue
                            lbl_color = w_lbl.palette().color(w_lbl.foregroundRole())
                            cr = calc_contrast(lbl_color, card_bg)
                            assert cr >= 4.5, f"Step 3 label '{w_lbl.text()}' contrast in {theme} is {cr:.2f} < 4.5"

            # --- فحص الصفحة 4 ---
            wizard.stack.setCurrentIndex(3)
            page4 = wizard.stack.widget(3)
            assert page4 is not None

            img4 = page4.grab().toImage()
            assert img4.width() > 0 and img4.height() > 0
            fname4 = f"hpge_wizard_step4_{theme}.png"
            img4.save(os.path.join(out_dir, fname4))
            img4.save(os.path.join(artifact_dir, fname4))

            cards_4 = [w for w in page4.findChildren(QFrame) if w.property("class") == "card"]
            assert len(cards_4) >= 1
            for fc in cards_4:
                fl = fc.findChild(QFormLayout)
                if fl:
                    for row_i in range(fl.rowCount()):
                        item = fl.itemAt(row_i, QFormLayout.ItemRole.LabelRole)
                        if item and item.widget() and isinstance(item.widget(), QLabel):
                            w_lbl = item.widget()
                            if not w_lbl.text().strip():
                                continue
                            lbl_color = w_lbl.palette().color(w_lbl.foregroundRole())
                            cr = calc_contrast(lbl_color, card_bg)
                            assert cr >= 4.5, f"Step 4 label '{w_lbl.text()}' contrast in {theme} is {cr:.2f} < 4.5"

            wizard.close()


    def test_step3_and_step4_banner_fixed_outside_scroll_area(self, wizard_db, tech1_session, qapp):
        """اللافتة في الخطوتين 3 و 4 ثابتة فوق منطقة التمرير، وليست من أبناء QScrollArea، وتظل ظاهرة عند التمرير للأسفل."""
        from PyQt6.QtWidgets import QScrollArea
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard.show()
        qapp.processEvents()

        for step_idx, banner, scroll in [
            (2, wizard.card_step3_banner, wizard.scroll_step_3),
            (3, wizard.card_step4_banner, wizard.scroll_step_4),
        ]:
            wizard.stack.setCurrentIndex(step_idx)
            qapp.processEvents()

            # 1. التحقق من سلسلة parent: اللافتة ليست ابناً أو حفيدة لأي QScrollArea
            p = banner.parent()
            is_child_of_scroll = False
            while p is not None:
                if isinstance(p, QScrollArea):
                    is_child_of_scroll = True
                    break
                p = p.parent()
            assert is_child_of_scroll is False, f"Banner for step {step_idx + 1} must not be inside any QScrollArea"

            # 2. تمرير منطقة التمرير إلى أقصى الأسفل
            v_bar = scroll.verticalScrollBar()
            v_bar.setValue(v_bar.maximum())
            qapp.processEvents()

            # 3. التحقق من أن اللافتة ظاهرة ومرسومة داخل حدود الحوار
            assert banner.isVisible() is True
            top_left = banner.mapTo(wizard, banner.rect().topLeft())
            bottom_right = banner.mapTo(wizard, banner.rect().bottomRight())
            wizard_rect = wizard.rect()
            assert wizard_rect.contains(top_left)
            assert wizard_rect.contains(bottom_right)

        wizard.close()

    def test_next_step_failure_shows_warning_dialog_and_focuses_first_missing_field(self, wizard_db, tech1_session, monkeypatch, qapp):
        """فشل زر 'التالي' بحقل ناقص يستدعي QMessageBox.warning بعنوان 'تنبيه قبل المتابعة' ويذكر اسم الحقل ويركز عليه."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard.show()
        qapp.processEvents()

        warnings_called = []
        def fake_warning(parent, title, text, *args, **kwargs):
            warnings_called.append((title, text))
            return QMessageBox.StandardButton.Ok
        monkeypatch.setattr(QMessageBox, "warning", fake_warning)

        # 1. الخطوة 1: أول حقل ناقص هو نافذة البريليوم
        wizard.stack.setCurrentIndex(0)
        wizard.btn_next.click()
        qapp.processEvents()

        assert len(warnings_called) == 1
        assert warnings_called[0][0] == "تنبيه قبل المتابعة"
        assert "نافذة البريليوم" in warnings_called[0][1]
        assert wizard.combo_window_status.hasFocus()

        # 2. تعبئة نافذة البريليوم والنقر على التالي -> التركيز على مدة التبريد
        warnings_called.clear()
        wizard.combo_window_status.setCurrentIndex(1)
        wizard.btn_next.click()
        qapp.processEvents()

        assert len(warnings_called) == 1
        assert warnings_called[0][0] == "تنبيه قبل المتابعة"
        assert "التبريد" in warnings_called[0][1]
        assert wizard.edit_cooling_hours.hasFocus()

        # 3. الخطوة 3: حقل تشوه نبضة P/Z ناقص
        warnings_called.clear()
        wizard.stack.setCurrentIndex(2)
        wizard.btn_next.click()
        qapp.processEvents()

        assert len(warnings_called) == 1
        assert warnings_called[0][0] == "تنبيه قبل المتابعة"
        assert "P/Z" in warnings_called[0][1]
        assert wizard.combo_pz_distortion.hasFocus()

        # 4. الخطوة 4: حقل إجمالي معدل النبضات MCA ناقص
        warnings_called.clear()
        wizard.stack.setCurrentIndex(3)
        wizard.btn_next.click()
        qapp.processEvents()

        assert len(warnings_called) == 1
        assert warnings_called[0][0] == "تنبيه قبل المتابعة"
        assert "معدل النبضات" in warnings_called[0][1]
        assert wizard.edit_total_count_rate_cps.hasFocus()

        wizard.close()

    def test_save_draft_invalid_fwtm_fwhm_shows_warning_and_does_not_save(self, wizard_db, tech1_session, monkeypatch, qapp):
        """حفظ كمسودة بإدخال غير صالح (FWTM <= FWHM) يعرض نافذة تنبيه ولا يتم الحفظ."""
        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)
        wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
        wizard.show()
        qapp.processEvents()

        warnings_called = []
        def fake_warning(parent, title, text, *args, **kwargs):
            warnings_called.append((title, text))
            return QMessageBox.StandardButton.Ok
        monkeypatch.setattr(QMessageBox, "warning", fake_warning)

        _fill_step_1_valid(wizard)
        _fill_step_2_valid(wizard)
        _fill_step_3_valid(wizard)
        _fill_step_4_valid(wizard)

        # جعل FWTM <= FWHM في قمة 1332.5 (خطأ فيزيائي)
        wizard.edit_p2_fwhm_ch.setText("4.00")
        wizard.edit_p2_fwtm_ch.setText("3.50")

        # النقر على زر حفظ كمسودة
        wizard.btn_save_draft.click()
        qapp.processEvents()

        assert len(warnings_called) == 1
        title, msg = warnings_called[0]
        assert title == "تنبيه قبل المتابعة"
        assert "FWTM" in msg or "عُشر الارتفاع" in msg
        assert wizard.edit_p2_fwtm_ch.hasFocus()

        # التأكد من عدم حفظ القمم بقيم غير صالحة
        full = service.get_inspection_full(insp_id)
        assert len(full.get("peaks", [])) == 0

        # التأكد من أن lbl_feedback لم تمرر أي خطأ
        assert wizard.lbl_feedback.text() == ""

        wizard.close()

    def test_steps_1_to_4_full_grab_contrast_light_and_dark(self, wizard_db, tech1_session, qapp):
        """تصوير كامل (Full Grab) للحوار للخطوات 1 إلى 4 في الوضعين الفاتح والداكن والتحقق من نسبة التباين >= 4.5:1."""
        from kashif.gui.theme import set_mode, get_stylesheet, COLORS
        from PyQt6.QtGui import QColor
        from PyQt6.QtWidgets import QFrame, QFormLayout
        import os

        service = InspectionService(wizard_db)
        insp_id = service.create_draft(detector_id=1, session=tech1_session)

        def rel_lum(c: QColor) -> float:
            def f(ch):
                val = ch / 255.0
                return val / 12.92 if val <= 0.03928 else ((val + 0.055) / 1.055) ** 2.4
            return 0.2126 * f(c.red()) + 0.7152 * f(c.green()) + 0.0722 * f(c.blue())

        def calc_contrast(c1: QColor, c2: QColor) -> float:
            l1 = rel_lum(c1)
            l2 = rel_lum(c2)
            return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

        out_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "output", "grab_steps_1_to_4"))
        os.makedirs(out_dir, exist_ok=True)
        artifact_dir = "C:/Users/DELL/.gemini/antigravity/brain/5c0a7800-2b5d-45f4-a075-cc02dc05861d/step_grabs_all"
        os.makedirs(artifact_dir, exist_ok=True)

        for theme in ["light", "dark"]:
            set_mode(theme)
            qapp.setStyleSheet(get_stylesheet(theme))

            wizard = HPGeInspectionWizard(conn=wizard_db, session=tech1_session, inspection_id=insp_id)
            wizard.setStyleSheet(get_stylesheet(theme))
            wizard.resize(920, 760)
            wizard.show()
            qapp.processEvents()

            card_bg = QColor(COLORS["dark_card"] if theme == "dark" else COLORS["light_card"])

            for step_idx in range(4):
                wizard.stack.setCurrentIndex(step_idx)
                qapp.processEvents()

                # التقاط صورة كاملة للحوار في الخطوة المحددة
                img = wizard.grab().toImage()
                assert img.width() > 0 and img.height() > 0
                fname = f"hpge_wizard_step{step_idx + 1}_{theme}.png"
                img.save(os.path.join(out_dir, fname))
                img.save(os.path.join(artifact_dir, fname))

                # فحص تباين النصوص داخل الصفحة
                page = wizard.stack.widget(step_idx)
                assert page is not None
                cards = [w for w in page.findChildren(QFrame) if w.property("class") == "card"]
                for fc in cards:
                    fl = fc.findChild(QFormLayout)
                    if fl:
                        for row_i in range(fl.rowCount()):
                            item = fl.itemAt(row_i, QFormLayout.ItemRole.LabelRole)
                            if item and item.widget() and isinstance(item.widget(), QLabel):
                                w_lbl = item.widget()
                                if not w_lbl.text().strip():
                                    continue
                                lbl_color = w_lbl.palette().color(w_lbl.foregroundRole())
                                cr = calc_contrast(lbl_color, card_bg)
                                assert cr >= 4.5, f"Step {step_idx + 1} label '{w_lbl.text()}' contrast in {theme} is {cr:.2f} < 4.5"

            wizard.close()

def test_hpge_wizard_class_docstring():
    """التحقق من وجود docstring صريح لصنف HPGeInspectionWizard وأنه يحوي كلمة 'معالج'."""
    assert HPGeInspectionWizard.__doc__ is not None
    assert "معالج" in HPGeInspectionWizard.__doc__


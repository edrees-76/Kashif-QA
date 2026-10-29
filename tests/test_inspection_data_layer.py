"""Comprehensive tests for HPGe detector inspection data layer and governance logic (TASK-2.1).

Covers:
1. Migration 009 schema, constraints, idempotence, foreign keys.
2. InspectionRepository CRUD, links, attachments (SHA-256), retries, audit chain.
3. InspectionService lifecycle, separation of duties (SEC-01, SEC-02, SEC-03),
   verdict aggregation (R-HPGE), canonical snapshot, report numbering.
"""

from datetime import date, datetime, timezone
import hashlib
import json
import os
import sqlite3
import tempfile
import pytest

from kashif.core.models import (
    HPGeMeasurement,
    InspectionStatus,
    PreampType,
    Verdict,
    VoltagePolarity,
    WindowStatus,
)
from kashif.core.rules import (
    RuleResult,
    aggregate_verdicts,
    eval_rule_sec_01_separation_of_duties,
    eval_rule_sec_02_admin_approval_prohibition,
    eval_rule_sec_03_immutable_approved_record,
)
from kashif.db.connection import create_connection
from kashif.db.migrations import apply_migrations, init_migrations_table, migration_009_inspections_and_hpge_measurements
from kashif.db.repositories import (
    DetectorRepository,
    DuplicateEntityError,
    EntityNotFoundError,
    InspectionRepository,
    RepositoryError,
)
from kashif.services.audit_service import verify_audit_chain
from kashif.services.inspection_service import (
    MANDATORY_HPGE_FIELDS,
    InspectionService,
    InspectionWorkflowError,
    InvalidStateTransitionError,
    SecurityViolationError,
    build_inspection_snapshot,
    check_early_stage_failure,
    evaluate_hpge_rules_for_inspection,
    generate_report_number,
    missing_mandatory_fields,
)
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
def insp_db():
    """In-memory SQLite database initialized with all migrations up to 009."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_migrations_table(conn)
    applied = apply_migrations(conn)
    assert 9 in applied

    # Insert test users
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute(
        """
        INSERT INTO users (id, username, password_hash, full_name, role, is_active, created_at)
        VALUES 
            (1, 'tech1', 'hash1', 'فني الفحص 1', 'TECHNICIAN', 1, ?),
            (2, 'tech2', 'hash2', 'فني الفحص 2', 'TECHNICIAN', 1, ?),
            (3, 'manager1', 'hash3', 'مدير المعمل 1', 'LAB_MANAGER', 1, ?),
            (4, 'admin1', 'hash4', 'مدير النظام 1', 'ADMIN', 1, ?);
        """,
        (now, now, now, now),
    )

    # Insert test detector
    c.execute(
        """
        INSERT INTO detectors (
            id, code, detector_type, manufacturer, model, serial_number,
            crystal_type, semiconductor_type, nominal_voltage, voltage_polarity,
            certified_polarity, nominal_fwhm_kev, nominal_efficiency_pct, cooling_hours_cert,
            hv_ramp_max_v_per_s, hv_ramp_reference, status, created_at, updated_at
        ) VALUES (
            1, 'DET-HPGE-001', 'HPGE', 'ORTEC', 'GEM-XX', 'SN-12345',
            'COAXIAL', 'P_TYPE', 3000.0, 'POSITIVE',
            'POSITIVE', 1.85, 40.0, 6.0, 10.0, 'Manual Section 4', 'ACTIVE', ?, ?
        );
        """,
        (now, now),
    )

    # Insert test instruments
    c.execute(
        """
        INSERT INTO instruments (
            id, instrument_type, manufacturer, model, serial_number, status, created_at, updated_at
        ) VALUES 
            (1, 'NIM_BIN', 'Canberra', '2000', 'BIN-001', 'ACTIVE', ?, ?),
            (2, 'HV_SUPPLY', 'ORTEC', '659', 'HV-002', 'ACTIVE', ?, ?),
            (3, 'AMPLIFIER', 'Canberra', '2026', 'AMP-003', 'ACTIVE', ?, ?),
            (4, 'MCA', 'ORTEC', '927', 'MCA-004', 'ACTIVE', ?, ?);
        """,
        (now, now, now, now, now, now, now, now),
    )

    # Insert test source
    c.execute(
        """
        INSERT INTO sources (
            id, source_code, serial_number, status, created_at, updated_at
        ) VALUES (
            1, 'SRC-CO60-01', 'CO-9988', 'ACTIVE', ?, ?
        );
        """,
        (now, now),
    )

    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def tech_session():
    return Session(user_id=1, username="tech1", full_name="فني الفحص 1", role=ROLE_TECHNICIAN)


@pytest.fixture
def tech2_session():
    return Session(user_id=2, username="tech2", full_name="فني الفحص 2", role=ROLE_TECHNICIAN)


@pytest.fixture
def manager_session():
    return Session(user_id=3, username="manager1", full_name="مدير المعمل 1", role=ROLE_LAB_MANAGER)


@pytest.fixture
def admin_session():
    return Session(user_id=4, username="admin1", full_name="مدير النظام 1", role=ROLE_ADMIN)


@pytest.fixture
def base_measurements():
    return {
        "beryllium_window_status": "INTACT",
        "cooling_hours": 8.0,
        "vacuum_intact": True,
        "cryostat_wet_or_frozen": False,
        "v_pos_24": 24.0,
        "v_neg_24": -24.0,
        "v_pos_12": 12.0,
        "v_neg_12": -12.0,
        "applied_voltage": 3000.0,
        "applied_polarity": "POSITIVE",
        "hv_ramp_source": "MANUAL",
        "hv_ramp_rate_v_per_s": 10.0,
        "has_preamp_signal": True,
        "has_pz_distortion": False,
        "total_count_rate_cps": 1500.0,
        "dead_time_pct": 5.0,
        "net_counts_1332": 15000.0,
        "measured_fwhm_kev": 1.82,
        "form_factor": 1.88,
        "relative_efficiency_pct": 40.5,
    }


# ---------------------------------------------------------------------------
# 1. Migration 009 Tests
# ---------------------------------------------------------------------------

class TestMigration009:
    """اختبارات مخطط قاعدة البيانات والقيود وقابلية التكرار للهجرة 009."""

    def test_migration_009_tables_exist(self, insp_db):
        cursor = insp_db.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}

        expected_tables = {
            "inspections",
            "inspection_hpge_measurements",
            "inspection_peaks",
            "inspection_instruments",
            "inspection_sources",
            "attachments",
            "measurement_retries",
        }
        assert expected_tables.issubset(tables)

    def test_migration_009_is_idempotent(self, insp_db):
        """إعادة تشغيل الهجرة 009 لا يجب أن تُحدث أي أخطاء."""
        migration_009_inspections_and_hpge_measurements(insp_db)
        migration_009_inspections_and_hpge_measurements(insp_db)

    def test_inspections_status_check_constraint(self, insp_db):
        """التحقق من رفض أي حالة غير معرفة في قيد CHECK(status IN (...))."""
        cursor = insp_db.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO inspections (detector_id, technician_id, inspection_date, status, created_at, updated_at)
                VALUES (1, 1, '2026-10-29', 'INVALID_STATUS', '2026-10-29', '2026-10-29');
                """
            )

    def test_inspections_verdict_check_constraint(self, insp_db):
        """التحقق من رفض أي حكم غير صالح."""
        cursor = insp_db.cursor()
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO inspections (detector_id, technician_id, inspection_date, status, verdict, created_at, updated_at)
                VALUES (1, 1, '2026-10-29', 'DRAFT', 'UNKNOWN_VERDICT', '2026-10-29', '2026-10-29');
                """
            )

    def test_inspections_report_number_uniqueness(self, insp_db):
        """التحقق من قيد تفرد رقم تقرير الفحص report_number."""
        cursor = insp_db.cursor()
        cursor.execute(
            """
            INSERT INTO inspections (report_number, detector_id, technician_id, inspection_date, status, created_at, updated_at)
            VALUES ('KASHIF-HPGE-2026-0001', 1, 1, '2026-10-29', 'APPROVED', '2026-10-29', '2026-10-29');
            """
        )
        insp_db.commit()

        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO inspections (report_number, detector_id, technician_id, inspection_date, status, created_at, updated_at)
                VALUES ('KASHIF-HPGE-2026-0001', 1, 1, '2026-10-29', 'APPROVED', '2026-10-29', '2026-10-29');
                """
            )

    def test_hpge_measurements_ramp_source_and_defaults(self, insp_db):
        """التحقق من حقل مصدر رفع الجهد والقيمة الافتراضية 10.0."""
        cursor = insp_db.cursor()
        cursor.execute(
            """
            INSERT INTO inspections (id, detector_id, technician_id, inspection_date, status, created_at, updated_at)
            VALUES (100, 1, 1, '2026-10-29', 'DRAFT', '2026-10-29', '2026-10-29');
            """
        )
        cursor.execute(
            """
            INSERT INTO inspection_hpge_measurements (inspection_id, cooling_hours, created_at, updated_at)
            VALUES (100, 8.0, '2026-10-29', '2026-10-29');
            """
        )
        cursor.execute("SELECT hv_ramp_source, hv_ramp_rate_v_per_s FROM inspection_hpge_measurements WHERE inspection_id = 100;")
        row = cursor.fetchone()
        assert row["hv_ramp_source"] == "MANUAL"
        assert row["hv_ramp_rate_v_per_s"] == 10.0

        # رفض قيمة غير صالحة لـ hv_ramp_source
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO inspection_hpge_measurements (inspection_id, hv_ramp_source, created_at, updated_at)
                VALUES (101, 'INVALID_RAMP_SOURCE', '2026-10-29', '2026-10-29');
                """
            )

    def test_foreign_key_cascade_deletion(self, insp_db):
        """حذف الفحص يحذف بالتعاقب (CASCADE) القياسات والقمم والملحقات والإعادات."""
        cursor = insp_db.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        cursor.execute(
            """
            INSERT INTO inspections (id, detector_id, technician_id, inspection_date, status, created_at, updated_at)
            VALUES (200, 1, 1, '2026-10-29', 'DRAFT', '2026-10-29', '2026-10-29');
            """
        )
        cursor.execute(
            """
            INSERT INTO inspection_hpge_measurements (inspection_id, cooling_hours, created_at, updated_at)
            VALUES (200, 8.0, '2026-10-29', '2026-10-29');
            """
        )
        cursor.execute(
            """
            INSERT INTO inspection_peaks (inspection_id, peak_name, energy_reference_kev, created_at)
            VALUES (200, 'Co-60 Peak 1', 1173.2, '2026-10-29');
            """
        )
        cursor.execute("DELETE FROM inspections WHERE id = 200;")
        insp_db.commit()

        cursor.execute("SELECT COUNT(*) FROM inspection_hpge_measurements WHERE inspection_id = 200;")
        assert cursor.fetchone()[0] == 0
        cursor.execute("SELECT COUNT(*) FROM inspection_peaks WHERE inspection_id = 200;")
        assert cursor.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 2. InspectionRepository Tests
# ---------------------------------------------------------------------------

class TestInspectionRepository:
    """اختبارات مستودع بيانات الفحوصات والعمليات الذرية والربط والتدقيق."""

    def test_create_draft_success(self, insp_db, tech_session):
        repo = InspectionRepository(insp_db)
        insp_id = repo.create_draft(detector_id=1, session=tech_session, inspection_date="2026-10-29")
        assert insp_id > 0

        row = repo.get_by_id(insp_id)
        assert row is not None
        assert row["status"] == "DRAFT"
        assert row["detector_id"] == 1
        assert row["technician_id"] == tech_session.user_id
        assert row["inspection_date"] == "2026-10-29"
        assert row["report_number"] is None

        # التحقق من سلامة سلسلة التدقيق
        is_valid, broken = verify_audit_chain(insp_db)
        assert is_valid is True
        assert broken == []

    def test_create_draft_invalid_detector(self, insp_db, tech_session):
        repo = InspectionRepository(insp_db)
        with pytest.raises(EntityNotFoundError):
            repo.create_draft(detector_id=999, session=tech_session)

    def test_create_draft_permission_denied(self, insp_db, admin_session):
        repo = InspectionRepository(insp_db)
        with pytest.raises(PermissionError):
            repo.create_draft(detector_id=1, session=admin_session)

    def test_save_hpge_measurements_create_and_update(self, insp_db, tech_session):
        repo = InspectionRepository(insp_db)
        insp_id = repo.create_draft(detector_id=1, session=tech_session)

        meas_dict = {
            "cooling_hours": 8.0,
            "cryostat_intact": True,
            "beryllium_window_status": "INTACT",
            "vacuum_intact": True,
            "cryostat_wet_or_frozen": False,
            "v_pos_24": 24.0,
            "v_neg_24": -24.0,
            "v_pos_12": 12.0,
            "v_neg_12": -12.0,
            "applied_voltage": 3000.0,
            "applied_polarity": "POSITIVE",
            "hv_ramp_source": "MANUAL",
            "hv_ramp_rate_v_per_s": 10.0,
            "has_preamp_signal": True,
            "shaping_time_us": 4.0,
            "has_pz_distortion": False,
            "total_count_rate_cps": 1500.0,
            "dead_time_pct": 5.0,
            "net_counts_1332": 15000.0,
            "measured_fwhm_kev": 1.82,
            "measured_fwtm_kev": 3.45,
            "form_factor": 1.89,
            "relative_efficiency_pct": 41.5,
        }
        repo.save_hpge_measurements(insp_id, meas_dict, tech_session)

        full = repo.get_inspection_full(insp_id)
        assert full["hpge_measurements"] is not None
        assert full["hpge_measurements"]["cooling_hours"] == 8.0
        assert full["hpge_measurements"]["measured_fwhm_kev"] == 1.82
        assert full["hpge_measurements"]["relative_efficiency_pct"] == 41.5

        # تحديث القياسات
        meas_dict["measured_fwhm_kev"] = 1.80
        repo.save_hpge_measurements(insp_id, meas_dict, tech_session)
        full2 = repo.get_inspection_full(insp_id)
        assert full2["hpge_measurements"]["measured_fwhm_kev"] == 1.80

        is_valid, broken = verify_audit_chain(insp_db)
        assert is_valid is True
        assert broken == []

    def test_save_peaks(self, insp_db, tech_session):
        repo = InspectionRepository(insp_db)
        insp_id = repo.create_draft(detector_id=1, session=tech_session)

        peaks = [
            {
                "peak_name": "Co-60 Peak 1",
                "energy_reference_kev": 1173.2,
                "centroid_channel": 2346.0,
                "fwhm_kev": 1.78,
                "net_area_counts": 25000.0,
            },
            {
                "peak_name": "Co-60 Peak 2",
                "energy_reference_kev": 1332.5,
                "centroid_channel": 2665.0,
                "fwhm_kev": 1.82,
                "net_area_counts": 22000.0,
            },
        ]
        repo.save_peaks(insp_id, peaks, tech_session)

        full = repo.get_inspection_full(insp_id)
        assert len(full["peaks"]) == 2
        assert full["peaks"][0]["peak_name"] == "Co-60 Peak 1"
        assert full["peaks"][1]["energy_reference_kev"] == 1332.5

    def test_link_instruments_and_sources(self, insp_db, tech_session):
        repo = InspectionRepository(insp_db)
        insp_id = repo.create_draft(detector_id=1, session=tech_session)

        instruments = [
            {"instrument_id": 1, "instrument_role": "NIM_POWER", "notes": "Main crate"},
            {"instrument_id": 2, "instrument_role": "HV_BIAS", "notes": "+3000V bias"},
        ]
        repo.link_instruments(insp_id, instruments, tech_session)

        sources = [
            {"source_id": 1, "source_role": "CALIBRATION", "distance_cm": 25.0, "decay_corrected_activity_bq": 37000.0},
        ]
        repo.link_sources(insp_id, sources, tech_session)

        full = repo.get_inspection_full(insp_id)
        assert len(full["instruments"]) == 2
        assert len(full["sources"]) == 1
        assert full["instruments"][0]["instrument_type"] == "NIM_BIN"
        assert full["sources"][0]["source_code"] == "SRC-CO60-01"

    def test_add_attachment_computes_sha256(self, insp_db, tech_session):
        repo = InspectionRepository(insp_db)
        insp_id = repo.create_draft(detector_id=1, session=tech_session)

        # Create temporary test file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tf:
            tf.write(b"SAMPLE_SPECTRUM_IMAGE_DATA_123456789")
            temp_path = tf.name

        try:
            expected_hash = hashlib.sha256(b"SAMPLE_SPECTRUM_IMAGE_DATA_123456789").hexdigest()
            att_id = repo.add_attachment(
                inspection_id=insp_id,
                file_path=temp_path,
                file_type="SPECTRUM_PLOT",
                caption="مخطط طيف Co-60 التوضيحي",
                session=tech_session,
            )
            assert att_id > 0

            full = repo.get_inspection_full(insp_id)
            assert len(full["attachments"]) == 1
            att = full["attachments"][0]
            assert att["file_hash_sha256"] == expected_hash
            assert att["file_type"] == "SPECTRUM_PLOT"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_add_attachment_missing_file_raises_error(self, insp_db, tech_session):
        repo = InspectionRepository(insp_db)
        insp_id = repo.create_draft(detector_id=1, session=tech_session)
        with pytest.raises(FileNotFoundError):
            repo.add_attachment(insp_id, "non_existent_file_path.dat", "OTHER", None, tech_session)

    def test_record_retry_increments_attempt(self, insp_db, tech_session):
        repo = InspectionRepository(insp_db)
        insp_id = repo.create_draft(detector_id=1, session=tech_session)

        r1 = repo.record_retry(
            inspection_id=insp_id,
            stage_number=3,
            reason_for_retry="P/Z distortion present",
            corrective_action_taken="Adjusted screwdriver trim pot on amplifier",
            previous_values={"has_pz_distortion": True},
            new_values={"has_pz_distortion": False},
            session=tech_session,
        )
        assert r1 > 0

        r2 = repo.record_retry(
            inspection_id=insp_id,
            stage_number=3,
            reason_for_retry="Centroid shifted by 2 channels",
            corrective_action_taken="Readjusted fine gain",
            previous_values={"centroid": 2667.0},
            new_values={"centroid": 2665.0},
            session=tech_session,
        )
        assert r2 > 0

        full = repo.get_inspection_full(insp_id)
        assert len(full["retries"]) == 2
        assert full["retries"][0]["attempt_number"] == 1
        assert full["retries"][1]["attempt_number"] == 2
        assert json.loads(full["retries"][0]["previous_values_json"]) == {"has_pz_distortion": True}

    def test_list_by_detector(self, insp_db, tech_session):
        repo = InspectionRepository(insp_db)
        id1 = repo.create_draft(detector_id=1, session=tech_session, inspection_date="2026-10-25")
        id2 = repo.create_draft(detector_id=1, session=tech_session, inspection_date="2026-10-29")

        rows = repo.list_by_detector(1)
        assert len(rows) == 2
        assert rows[0]["id"] == id2  # أحدث تاريخ أولاً
        assert rows[1]["id"] == id1

    def test_repository_transaction_and_exception_branches(self, insp_db, tech_session):
        repo = InspectionRepository(insp_db)

        # 1. اختبار المعاملات وبداية المعاملة المسبقة لتغطية تكرار BEGIN
        insp_db.execute("BEGIN IMMEDIATE;")
        insp_id = repo.create_draft(1, tech_session)

        insp_db.execute("BEGIN IMMEDIATE;")
        repo.save_hpge_measurements(insp_id, {
            "cooling_hours": 6.0,
            "beryllium_window_status": WindowStatus.INTACT,
            "applied_voltage": 3000.0,
            "applied_polarity": VoltagePolarity.POSITIVE,
            "preamp_type": PreampType.RESISTIVE,
            "total_cps": 1000.0,
            "fwhm_measured_kev": 1.85,
            "fwtm_measured_kev": 3.5,
            "form_factor_measured": 1.89,
            "eff_measured_pct": 40.0,
        }, tech_session)

        insp_db.execute("BEGIN IMMEDIATE;")
        repo.save_peaks(insp_id, [{"peak_name": "P1", "energy_reference_kev": 100.0}], tech_session)

        insp_db.execute("BEGIN IMMEDIATE;")
        repo.link_instruments(insp_id, [{"instrument_id": 1, "instrument_role": "R1"}], tech_session)

        insp_db.execute("BEGIN IMMEDIATE;")
        repo.link_sources(insp_id, [{"source_id": 1, "source_role": "S1"}], tech_session)

        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"DATA")
            tf_name = tf.name
        try:
            insp_db.execute("BEGIN IMMEDIATE;")
            repo.add_attachment(insp_id, tf_name, "OTHER", None, tech_session)
        finally:
            if os.path.exists(tf_name):
                os.remove(tf_name)

        insp_db.execute("BEGIN IMMEDIATE;")
        repo.record_retry(insp_id, 1, "reason", "action", None, None, tech_session)

        insp_db.execute("BEGIN IMMEDIATE;")
        repo.update_inspection_record(insp_id, {"manager_notes": "notes"}, tech_session)

        # 2. محاكاة فشل استثنائي لتغطية كتل التراجع العامة (generic rollback handlers)
        class FailingConn:
            def __init__(self, real_conn):
                self.real = real_conn
                self.in_transaction = False
            def cursor(self):
                raise RuntimeError("Simulated connection failure")
            def rollback(self):
                pass
            def execute(self, *a, **k):
                raise RuntimeError("Simulated failure")

        fail_repo = InspectionRepository(FailingConn(insp_db))
        with pytest.raises(RuntimeError):
            fail_repo.create_draft(1, tech_session)
        with pytest.raises(RuntimeError):
            fail_repo.save_hpge_measurements(insp_id, {"cooling_hours": 6.0}, tech_session)
        with pytest.raises(RuntimeError):
            fail_repo.save_peaks(insp_id, [], tech_session)
        with pytest.raises(RuntimeError):
            fail_repo.link_instruments(insp_id, [], tech_session)
        with pytest.raises(RuntimeError):
            fail_repo.link_sources(insp_id, [], tech_session)
        with tempfile.NamedTemporaryFile(delete=False) as tf2:
            tf2.write(b"D")
            tf2_name = tf2.name
        try:
            with pytest.raises(RuntimeError):
                fail_repo.add_attachment(insp_id, tf2_name, "OTHER", None, tech_session)
        finally:
            if os.path.exists(tf2_name):
                os.remove(tf2_name)
        with pytest.raises(RuntimeError):
            fail_repo.record_retry(insp_id, 1, "r", "c", None, None, tech_session)
        with pytest.raises(RuntimeError):
            fail_repo.update_inspection_record(insp_id, {"manager_notes": "n"}, tech_session)


# ---------------------------------------------------------------------------
# 3. InspectionService & Governance Rules Tests
# ---------------------------------------------------------------------------

class TestInspectionServiceAndGovernance:
    """اختبارات دورة حياة الفحص وقواعد فصل المهام وتجميد السجلات والتوليد التسلسلي."""

    def test_lifecycle_happy_path(self, insp_db, tech_session, manager_session):
        """مسار الفحص الناجح: مسودة -> قيد المراجعة -> معتمد برقم تقرير ولقطة مجمدة."""
        service = InspectionService(insp_db)

        # 1. إنشاء مسودة
        insp_id = service.create_draft(detector_id=1, session=tech_session)

        # 2. إدخال قياسات ناجحة مطابقة
        meas = {
            "cooling_hours": 8.0,
            "cryostat_intact": True,
            "beryllium_window_status": "INTACT",
            "vacuum_intact": True,
            "cryostat_wet_or_frozen": False,
            "v_pos_24": 24.0,
            "v_neg_24": -24.0,
            "v_pos_12": 12.0,
            "v_neg_12": -12.0,
            "applied_voltage": 3000.0,
            "applied_polarity": "POSITIVE",
            "hv_ramp_rate_v_per_s": 10.0,
            "has_preamp_signal": True,
            "has_pz_distortion": False,
            "total_count_rate_cps": 1500.0,
            "dead_time_pct": 5.0,
            "net_counts_1332": 15000.0,
            "measured_fwhm_kev": 1.82,
            "form_factor": 1.88,
            "relative_efficiency_pct": 40.5,
        }
        service.save_hpge_measurements(insp_id, meas, tech_session)

        # 3. تقديم الفحص للمراجعة
        service.submit_for_approval(insp_id, tech_session, technician_signature="SIG-TECH-001")
        insp_pending = service.get_inspection_full(insp_id)["inspection"]
        assert insp_pending["status"] == "PENDING_APPROVAL"
        assert insp_pending["technician_signature_token"] == "SIG-TECH-001"

        # 4. اعتماد الفحص بواسطة مدير المعمل
        approved_full = service.approve_inspection(
            inspection_id=insp_id,
            session=manager_session,
            manager_notes="فحص مطابق لكافة المعايير المعتمدة",
            manager_signature="SIG-MGR-001",
        )
        insp_approved = approved_full["inspection"]
        assert insp_approved["status"] == "APPROVED"
        assert insp_approved["verdict"] == "PASS"
        assert insp_approved["report_number"] == "KASHIF-HPGE-2026-0001"
        assert insp_approved["approver_id"] == manager_session.user_id
        assert insp_approved["snapshot_json"] is not None

        # التحقق من محتوى اللقطة المجمدة
        snapshot = json.loads(insp_approved["snapshot_json"])
        assert snapshot["report_number"] == "KASHIF-HPGE-2026-0001"
        assert snapshot["verdict"] == "PASS"
        assert snapshot["approver"]["user_id"] == manager_session.user_id

        # 5. التحقق من التجميد التام (SEC-03)
        with pytest.raises(RepositoryError):
            service.save_hpge_measurements(insp_id, meas, tech_session)

        with pytest.raises(SecurityViolationError):
            service.submit_for_approval(insp_id, tech_session)

        with pytest.raises(SecurityViolationError):
            service.approve_inspection(insp_id, manager_session)

        with pytest.raises(SecurityViolationError):
            service.reject_inspection(insp_id, "سبب غير صالح", manager_session)

    def test_rejection_path_returns_to_draft_with_reason(self, insp_db, tech_session, manager_session, base_measurements):
        """مسار الرفض: مسودة -> تقديم -> رفض -> عودة لمسودة مع بقاء سبب الرفض -> إعادة تقديم -> اعتماد."""
        service = InspectionService(insp_db)

        insp_id = service.create_draft(detector_id=1, session=tech_session)
        meas_short = dict(base_measurements, cooling_hours=3.0)
        service.save_hpge_measurements(insp_id, meas_short, tech_session)
        service.submit_for_approval(insp_id, tech_session)

        # رفض الفحص مع توثيق السبب
        rejected_full = service.reject_inspection(
            inspection_id=insp_id,
            rejection_reason="مدة التبريد غير كافية (3 ساعات فقط بينما المطلوب 6 ساعات)",
            session=manager_session,
            manager_notes="يرجى إكمال التبريد وإعادة القياس",
            return_to_draft=True,
        )
        insp_row = rejected_full["inspection"]
        assert insp_row["status"] == "DRAFT"
        assert "مدة التبريد غير كافية" in insp_row["rejection_reason"]
        assert "إكمال التبريد" in insp_row["manager_notes"]

        # الفني يصحح التبريد ويعيد التقديم
        meas_corrected = dict(base_measurements, cooling_hours=6.5)
        service.save_hpge_measurements(insp_id, meas_corrected, tech_session)
        service.submit_for_approval(insp_id, tech_session)

        # المدير يعتمد بعد التصحيح
        approved = service.approve_inspection(insp_id, manager_session)
        assert approved["inspection"]["status"] == "APPROVED"

    def test_sec_01_separation_of_duties_prevents_self_approval(self, insp_db, tech_session, base_measurements):
        """قاعدة RULE-SEC-01: الفني لا يمكنه اعتماد فحصه بنفسه."""
        service = InspectionService(insp_db)
        insp_id = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp_id, base_measurements, tech_session)
        service.submit_for_approval(insp_id, tech_session)

        # محاولة اعتماد الفحص بنفس معرف الفني
        with pytest.raises(SecurityViolationError) as exc_info:
            service.approve_inspection(insp_id, session=tech_session)

        assert "حظر الاعتماد الذاتي" in str(exc_info.value) or "فصل المهام" in str(exc_info.value)

    def test_sec_02_admin_approval_prohibition(self, insp_db, tech_session, admin_session, base_measurements):
        """قاعدة RULE-SEC-02: مدير النظام ADMIN محظور من اعتماد أو رفض الفحوصات الفنية."""
        service = InspectionService(insp_db)
        insp_id = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp_id, base_measurements, tech_session)
        service.submit_for_approval(insp_id, tech_session)

        # محاولة الاعتماد بواسطة ADMIN
        with pytest.raises(SecurityViolationError) as exc_info:
            service.approve_inspection(insp_id, session=admin_session)
        assert "حظر اعتماد مدير النظام" in str(exc_info.value)

        # محاولة الرفض بواسطة ADMIN
        with pytest.raises(SecurityViolationError) as exc_info2:
            service.reject_inspection(insp_id, "سبب الرفض", session=admin_session)
        assert "حظر اعتماد مدير النظام" in str(exc_info2.value)

    def test_cannot_approve_draft_directly_without_submission(self, insp_db, tech_session, manager_session, base_measurements):
        """منع الانتقال غير النظامي من DRAFT إلى APPROVED مباشرة."""
        service = InspectionService(insp_db)
        insp_id = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp_id, base_measurements, tech_session)

        with pytest.raises(InvalidStateTransitionError):
            service.approve_inspection(insp_id, manager_session)

    def test_cannot_submit_without_measurements(self, insp_db, tech_session):
        """منع تقديم الفحص للمراجعة إذا لم تكن هناك قياسات مسجلة."""
        service = InspectionService(insp_db)
        insp_id = service.create_draft(detector_id=1, session=tech_session)

        with pytest.raises(InspectionWorkflowError):
            service.submit_for_approval(insp_id, tech_session)

    def test_rejection_requires_non_empty_reason(self, insp_db, tech_session, manager_session, base_measurements):
        """سبب الرفض إلزامي ولا يمكن تمرير نص فارغ."""
        service = InspectionService(insp_db)
        insp_id = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp_id, base_measurements, tech_session)
        service.submit_for_approval(insp_id, tech_session)

        with pytest.raises(ValueError):
            service.reject_inspection(insp_id, "   ", manager_session)

    def test_verdict_aggregation_with_hpge_rules(self, insp_db, tech_session, manager_session, base_measurements):
        """اختبار تجميع الأحكام: خروج عن المواصفات (OUT_OF_SPEC) ورسوب (FAILURE)."""
        service = InspectionService(insp_db)

        # 1. حالة OUT_OF_SPEC (FWHM أعلى من 1.10x nominal ولكن دون 1.25x)
        # nominal_fwhm = 1.85 -> 1.10 * 1.85 = 2.035 -> 2.10 is OUT_OF_SPEC
        insp1 = service.create_draft(detector_id=1, session=tech_session)
        meas_out_of_spec = dict(base_measurements, measured_fwhm_kev=2.10)
        service.save_hpge_measurements(insp1, meas_out_of_spec, tech_session)
        service.submit_for_approval(insp1, tech_session)
        approved1 = service.approve_inspection(insp1, manager_session)
        assert approved1["inspection"]["verdict"] == "OUT_OF_SPEC"

        # 2. حالة FAILURE (فراغ تالف أو كسر نافذة)
        insp2 = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(
            insp2,
            {
                "cooling_hours": 8.0,
                "cryostat_intact": False,
                "beryllium_window_status": "BROKEN",
                "applied_voltage": 3000.0,
            },
            tech_session,
        )
        service.submit_for_approval(insp2, tech_session)
        approved2 = service.approve_inspection(insp2, manager_session)
        assert approved2["inspection"]["verdict"] == "FAILURE"

    def test_sequential_report_number_generation(self, insp_db, tech_session, manager_session, base_measurements):
        """توليد أرقام التقارير المتتابعة KASHIF-HPGE-YYYY-0001 ثم 0002."""
        service = InspectionService(insp_db)

        insp1 = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp1, base_measurements, tech_session)
        service.submit_for_approval(insp1, tech_session)
        app1 = service.approve_inspection(insp1, manager_session)

        insp2 = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp2, base_measurements, tech_session)
        service.submit_for_approval(insp2, tech_session)
        app2 = service.approve_inspection(insp2, manager_session)

        year = datetime.now(timezone.utc).year
        assert app1["inspection"]["report_number"] == f"KASHIF-HPGE-{year}-0001"
        assert app2["inspection"]["report_number"] == f"KASHIF-HPGE-{year}-0002"

    def test_service_delegates_and_pydantic_model(self, insp_db, tech_session):
        """اختبار تمرير كائن Pydantic HPGeMeasurement ودوال التفويض في InspectionService."""
        service = InspectionService(insp_db)
        insp_id = service.create_draft(detector_id=1, session=tech_session)

        # تمرير كائن Pydantic
        meas_model = HPGeMeasurement(
            cooling_hours=7.5,
            applied_voltage=3000.0,
            applied_polarity=VoltagePolarity.POSITIVE,
            shaping_time_us=4.0,
            has_preamp_signal=True,
            has_pz_distortion=False,
            total_cps=1200.0,
            dead_time_pct=3.5,
            net_counts_1332=12000.0,
            fwhm_measured_kev=1.83,
            fwtm_measured_kev=3.46,
            form_factor_measured=1.89,
            eff_measured_pct=40.2,
        )
        service.save_hpge_measurements(insp_id, meas_model, tech_session)

        # تفويض save_peaks
        service.save_peaks(
            insp_id,
            [{"peak_name": "Peak A", "energy_reference_kev": 661.7}],
            tech_session,
        )

        # تفويض link_instruments
        service.link_instruments(
            insp_id,
            [{"instrument_id": 1, "instrument_role": "NIM_MAIN"}],
            tech_session,
        )

        # تفويض link_sources
        service.link_sources(
            insp_id,
            [{"source_id": 1, "source_role": "CHECK"}],
            tech_session,
        )

        # تفويض record_retry
        retry_id = service.record_retry(
            insp_id,
            stage_number=1,
            reason_for_retry="Initial adjustment",
            corrective_action_taken="Realigned",
            previous_values=None,
            new_values=None,
            session=tech_session,
        )
        assert retry_id > 0

        # تفويض list_by_detector
        by_det = service.list_by_detector(1)
        assert len(by_det) >= 1

        full = service.get_inspection_full(insp_id)
        assert full["hpge_measurements"]["cooling_hours"] == 7.5
        assert len(full["peaks"]) == 1
        assert len(full["instruments"]) == 1
        assert len(full["sources"]) == 1

    def test_return_to_draft_workflow(self, insp_db, tech_session, manager_session, base_measurements):
        """اختبار دالة return_to_draft وحالات الخطأ الخاصة بها."""
        service = InspectionService(insp_db)
        insp_id = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp_id, base_measurements, tech_session)
        service.submit_for_approval(insp_id, tech_session)

        # الرفض مع البقاء بحالة REJECTED (return_to_draft=False)
        service.reject_inspection(
            insp_id,
            rejection_reason="تحتاج إعادة ضبط",
            session=manager_session,
            return_to_draft=False,
        )
        rejected = service.get_inspection_full(insp_id)["inspection"]
        assert rejected["status"] == "REJECTED"

        # إعادة الفحص إلى مسودة
        res = service.return_to_draft(insp_id, tech_session)
        assert res["inspection"]["status"] == "DRAFT"

        # استدعاء return_to_draft وهو مسودة بالفعل (idempotent)
        res2 = service.return_to_draft(insp_id, tech_session)
        assert res2["inspection"]["status"] == "DRAFT"

        # محاولة استدعاء return_to_draft وهو PENDING_APPROVAL
        service.submit_for_approval(insp_id, tech_session)
        with pytest.raises(InvalidStateTransitionError):
            service.return_to_draft(insp_id, tech_session)

    def test_workflow_error_cases_and_permissions(self, insp_db, tech_session, manager_session, admin_session, base_measurements):
        """اختبار كافة مسارات الاستثناءات والأخطاء الصريحة."""
        service = InspectionService(insp_db)
        repo = InspectionRepository(insp_db)

        # 1. التقديم والاعتماد والرفض لفحص غير موجود
        with pytest.raises(EntityNotFoundError):
            service.submit_for_approval(9999, tech_session)
        with pytest.raises(EntityNotFoundError):
            service.approve_inspection(9999, manager_session)
        with pytest.raises(EntityNotFoundError):
            service.reject_inspection(9999, "سبب", manager_session)
        with pytest.raises(EntityNotFoundError):
            service.return_to_draft(9999, tech_session)
        with pytest.raises(EntityNotFoundError):
            repo.get_inspection_full(9999)

        # 2. إنشاء فحص وتقديم مزدوج
        insp_id = service.create_draft(1, tech_session)
        service.save_hpge_measurements(insp_id, base_measurements, tech_session)
        service.submit_for_approval(insp_id, tech_session)
        with pytest.raises(InvalidStateTransitionError):
            service.submit_for_approval(insp_id, tech_session)

        # 3. محاولة الاعتماد دون صلاحية approve_inspection (فني يحاول اعتماد فحص غيره)
        tech2 = Session(user_id=2, username="tech2", full_name="فني 2", role=ROLE_TECHNICIAN)
        with pytest.raises(PermissionError):
            service.approve_inspection(insp_id, session=tech2)

        # 4. محاولة الرفض دون صلاحية reject_inspection
        with pytest.raises(PermissionError):
            service.reject_inspection(insp_id, "سبب الرفض", session=tech2)

        # 5. محاولة التعديل المباشر برفض/تحديث فارغ
        repo.update_inspection_record(insp_id, {}, tech_session)  # no-op

        # 6. صلاحيات المستودع
        with pytest.raises(PermissionError):
            repo.save_hpge_measurements(insp_id, base_measurements, admin_session)
        with pytest.raises(PermissionError):
            repo.save_peaks(insp_id, [], admin_session)
        with pytest.raises(PermissionError):
            repo.link_instruments(insp_id, [], admin_session)
        with pytest.raises(PermissionError):
            repo.link_sources(insp_id, [], admin_session)
        with pytest.raises(PermissionError):
            repo.record_retry(insp_id, 1, "r", "c", None, None, admin_session)
        with pytest.raises(PermissionError):
            repo.add_attachment(insp_id, "test", "OTHER", None, admin_session)

        # 7. رفض ثم اختبار صلاحية return_to_draft
        service.reject_inspection(insp_id, "سبب تجريبي", manager_session, return_to_draft=False)
        with pytest.raises(PermissionError):
            service.return_to_draft(insp_id, admin_session)

        # 8. فروع إضافية للتغطية الشاملة
        # أ) توليد رقم تقرير بوجود رقم سابق غير قياسي
        insp_db.execute(
            "INSERT INTO inspections (report_number, detector_id, technician_id, inspection_date, status, created_at, updated_at) "
            "VALUES ('KASHIF-HPGE-2026-MALFORMED', 1, 1, '2026-10-29', 'APPROVED', '2026-10-29', '2026-10-29');"
        )
        new_num = generate_report_number(insp_db, "HPGE", 2026)
        assert new_num == "KASHIF-HPGE-2026-0001"

        # ب) تقييم قواعد فحص فارغ
        assert evaluate_hpge_rules_for_inspection({}) == []

        # ج) تقييم مع قيم غير قياسية لقيم التعداد (Enums fallback)
        res_fallback = evaluate_hpge_rules_for_inspection({
            "hpge_measurements": {
                "beryllium_window_status": "UNKNOWN_VAL",
                "applied_polarity": "INVALID_POL",
                "hv_ramp_rate_v_per_s": None,
            },
            "detector": {
                "voltage_polarity": "INVALID_NOM_POL",
                "certified_polarity": "INVALID_CERT_POL",
            },
        })
        assert len(res_fallback) == 0

        # د) تفويض إضافة مرفق عبر InspectionService
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tf:
            tf.write(b"DELEGATE_ATTACHMENT")
            tf_path = tf.name
        try:
            insp_for_att = service.create_draft(1, tech_session)
            att_res = service.add_attachment(insp_for_att, tf_path, "PREAMP_WAVEFORM", "Waveform", tech_session)
            assert att_res > 0
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

        # هـ) محاولة تقديم دون صلاحية conduct_inspection أو save_draft
        insp_no_perm = service.create_draft(1, tech_session)
        service.save_hpge_measurements(insp_no_perm, base_measurements, tech_session)
        with pytest.raises(PermissionError):
            service.submit_for_approval(insp_no_perm, admin_session)

        # و) محاولة رفض فحص في حالة DRAFT
        with pytest.raises(InvalidStateTransitionError):
            service.reject_inspection(insp_no_perm, "سبب", manager_session)

        # ز) محاولة إعادة فحص معتمد إلى مسودة (RULE-SEC-03)
        service.submit_for_approval(insp_no_perm, tech_session)
        service.approve_inspection(insp_no_perm, manager_session)
        with pytest.raises(SecurityViolationError):
            service.return_to_draft(insp_no_perm, tech_session)

        # ح) قيود السلامة والأخطاء في InspectionRepository مباشرة على فحص معتمد
        with pytest.raises(RepositoryError):
            repo.save_peaks(insp_no_perm, [], tech_session)
        with pytest.raises(RepositoryError):
            repo.link_instruments(insp_no_perm, [], tech_session)
        with pytest.raises(RepositoryError):
            repo.link_sources(insp_no_perm, [], tech_session)
        with pytest.raises(RepositoryError):
            repo.record_retry(insp_no_perm, 1, "r", "c", None, None, tech_session)
        with pytest.raises(RepositoryError):
            repo.update_inspection_record(insp_no_perm, {"status": "DRAFT"}, tech_session, action="UPDATE")

        # ط) انتهاك قيود التكرار (IntegrityError mappings)
        insp_for_err = service.create_draft(1, tech_session)
        with pytest.raises(RepositoryError):
            repo.save_hpge_measurements(insp_for_err, {"hv_ramp_source": "CORRUPT"}, tech_session)

        with pytest.raises(DuplicateEntityError):
            repo.link_instruments(
                insp_for_err,
                [
                    {"instrument_id": 1, "instrument_role": "DUPLICATE_ROLE"},
                    {"instrument_id": 1, "instrument_role": "DUPLICATE_ROLE"},
                ],
                tech_session,
            )

        with pytest.raises(DuplicateEntityError):
            repo.link_sources(
                insp_for_err,
                [
                    {"source_id": 1, "source_role": "DUPLICATE_ROLE"},
                    {"source_id": 1, "source_role": "DUPLICATE_ROLE"},
                ],
                tech_session,
            )


# ---------------------------------------------------------------------------
# 4. Supplementary Coverage for PDF & Security
# ---------------------------------------------------------------------------

class TestPdfUtilsAndSecurityCoverage:
    """تغطية وظائف PDF والأمان التكميلية لضمان استيفاء شرط التغطية الشاملة >= 91%."""

    def test_pdf_arabic_utils(self):
        from kashif.reports.pdf_utils import (
            get_fonts_dir,
            register_arabic_fonts,
            prepare_arabic_line,
            prepare_arabic_paragraph,
        )
        fonts_dir = get_fonts_dir()
        assert fonts_dir is not None
        register_arabic_fonts()
        register_arabic_fonts()  # idempotent

        assert prepare_arabic_line("") == ""
        line = prepare_arabic_line("تقرير فحص كاشف الجرمانيوم")
        assert len(line) > 0

        assert prepare_arabic_paragraph("") == ""
        para = prepare_arabic_paragraph(
            "السطر الأول من التقرير\n\nالسطر الثاني ويحتوي على نص طويل جداً للالتفاف",
            max_chars_per_line=20,
        )
        assert "<br/>" in para

    def test_security_hash_fallbacks(self):
        from kashif.services.security import (
            hash_password,
            hash_password_pbkdf2,
            verify_password,
        )
        assert verify_password("", "") is False
        assert verify_password("secret", "") is False
        assert verify_password("", "hash") is False

        h_pbkdf2 = hash_password_pbkdf2("ValidPassword123!")
        assert verify_password("ValidPassword123!", h_pbkdf2) is True
        assert verify_password("WrongPassword!", h_pbkdf2) is False

        h_default = hash_password("ValidPassword123!", method="pbkdf2")
        assert verify_password("ValidPassword123!", h_default) is True


# ---------------------------------------------------------------------------
# 5. Quality Security Fixes (TASK-2.1 Quality Assurance Correction)
# ---------------------------------------------------------------------------

class TestQualitySecurityFixes:
    """اختبارات تصحيح عيوب أمان الجودة ومنع اعتماد الفحص الناقص وحصر الصلاحيات على المالك."""

    def test_cooling_hours_only_rejected_on_submit_and_approve(self, insp_db, tech_session, manager_session):
        """(a) فحص يحتوي cooling_hours فقط يُرفض عند submit ويُرفض عند approve."""
        service = InspectionService(insp_db)
        insp_id = service.create_draft(detector_id=1, session=tech_session)

        # حفظ cooling_hours=8.0 فقط دون أي قياس آخر
        service.save_hpge_measurements(insp_id, {"cooling_hours": 8.0}, tech_session)

        # 1. التقديم للمراجعة والاعتماد يجب أن يُرفض صراحة
        with pytest.raises(InspectionWorkflowError) as exc_submit:
            service.submit_for_approval(insp_id, tech_session)
        assert "حقول إلزامية غير مسجلة" in str(exc_submit.value)
        assert "beryllium_window_status" in str(exc_submit.value)
        assert "v_pos_24" in str(exc_submit.value)

        # 2. محاولة الاعتماد المباشر حتى لو تم التلاعب بالحالة إلى PENDING_APPROVAL
        insp_db.execute("UPDATE inspections SET status = 'PENDING_APPROVAL' WHERE id = ?;", (insp_id,))
        with pytest.raises(InspectionWorkflowError) as exc_approve:
            service.approve_inspection(insp_id, manager_session)
        assert "حقول إلزامية غير مسجلة" in str(exc_approve.value)

    def test_each_mandatory_field_missing_prevents_approval(self, insp_db, tech_session, manager_session, base_measurements):
        """(b) كل حقل إلزامي من قائمة الحقول الـ 19 ينقص وحده يمنع الاعتماد."""
        service = InspectionService(insp_db)

        for field in MANDATORY_HPGE_FIELDS:
            # 1. اختبار دالة missing_mandatory_fields على قاموس ينقصه هذا الحقل
            meas = dict(base_measurements)
            meas[field] = None
            full_mock = {"hpge_measurements": meas, "detector": {}}
            missing = missing_mandatory_fields(full_mock)
            assert field in missing, f"Expected {field} to be reported in missing fields"

            # 2. اختبار عبر خدمة الفحص وقاعدة البيانات
            # الحقول التي لا تفرض قيد NOT NULL في مخطط SQLite يتم حفظها بقيمة NULL والتحقق من رفض التقديم والاعتماد
            if field not in ("hv_ramp_source", "hv_ramp_rate_v_per_s"):
                insp_id = service.create_draft(detector_id=1, session=tech_session)
                service.save_hpge_measurements(insp_id, meas, tech_session)

                with pytest.raises(InspectionWorkflowError) as exc_sub:
                    service.submit_for_approval(insp_id, tech_session)
                assert field in str(exc_sub.value)

                insp_db.execute("UPDATE inspections SET status = 'PENDING_APPROVAL' WHERE id = ?;", (insp_id,))
                with pytest.raises(InspectionWorkflowError) as exc_app:
                    service.approve_inspection(insp_id, manager_session)
                assert field in str(exc_app.value)

    def test_early_stop_failure_conditions_approved_as_failure(self, insp_db, tech_session, manager_session):
        """(c) استثناء الإيقاف المبكر: إخفاقات المرحلتين 1 و 2 تُعتمد كـ FAILURE دون اشتراط بقية القياسات."""
        service = InspectionService(insp_db)

        # الحالة 1: نافذة بريليوم مكسورة (R-HPGE-01)
        insp1 = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp1, {"beryllium_window_status": "BROKEN"}, tech_session)
        service.submit_for_approval(insp1, tech_session)
        app1 = service.approve_inspection(insp1, manager_session)
        assert app1["inspection"]["status"] == "APPROVED"
        assert app1["inspection"]["verdict"] == "FAILURE"
        assert app1["inspection"]["report_number"].startswith("KASHIF-HPGE-")

        # الحالة 2: رطوبة وتجمد على الحاوية (R-HPGE-04)
        insp2 = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp2, {"cryostat_wet_or_frozen": True}, tech_session)
        service.submit_for_approval(insp2, tech_session)
        app2 = service.approve_inspection(insp2, manager_session)
        assert app2["inspection"]["status"] == "APPROVED"
        assert app2["inspection"]["verdict"] == "FAILURE"

        # الحالة 3: تسريب فراغ الحاوية (R-HPGE-04)
        insp3 = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp3, {"vacuum_intact": False}, tech_session)
        service.submit_for_approval(insp3, tech_session)
        app3 = service.approve_inspection(insp3, manager_session)
        assert app3["inspection"]["status"] == "APPROVED"
        assert app3["inspection"]["verdict"] == "FAILURE"

        # الحالة 4: قطبية جهد مخالفة لقطبية الكاشف (R-HPGE-06)
        # الكاشف مثبت في التجهيز بقطبية POSITIVE
        insp4 = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp4, {"applied_polarity": "NEGATIVE"}, tech_session)
        service.submit_for_approval(insp4, tech_session)
        app4 = service.approve_inspection(insp4, manager_session)
        assert app4["inspection"]["status"] == "APPROVED"
        assert app4["inspection"]["verdict"] == "FAILURE"

        # الحالة 5: انعدام إشارة المضخم الأولي (R-HPGE-08)
        insp5 = service.create_draft(detector_id=1, session=tech_session)
        service.save_hpge_measurements(insp5, {"has_preamp_signal": False}, tech_session)
        service.submit_for_approval(insp5, tech_session)
        app5 = service.approve_inspection(insp5, manager_session)
        assert app5["inspection"]["status"] == "APPROVED"
        assert app5["inspection"]["verdict"] == "FAILURE"

    def test_unrecorded_polarity_not_considered_matching(self, insp_db, tech_session, manager_session, base_measurements):
        """(d) قطبية غير مسجلة لا تُعد مطابقة ولا يجوز افتراض قطبية الكاشف كقيمة آمنة."""
        service = InspectionService(insp_db)
        insp_id = service.create_draft(detector_id=1, session=tech_session)

        meas = dict(base_measurements)
        meas["applied_polarity"] = None
        service.save_hpge_measurements(insp_id, meas, tech_session)

        # 1. التقديم والاعتماد يُرفضان لأن القطبية غير مسجلة
        with pytest.raises(InspectionWorkflowError) as exc_sub:
            service.submit_for_approval(insp_id, tech_session)
        assert "applied_polarity" in str(exc_sub.value)

        # 2. فحص تقييم القواعد المباشر للتأكد من عدم توليد R-HPGE-06 بقطبية مفترضة
        full = service.get_inspection_full(insp_id)
        rules = evaluate_hpge_rules_for_inspection(full)
        rule_ids = [r.rule_id for r in rules]
        assert "R-HPGE-06" not in rule_ids

    def test_technician_ownership_enforcement(self, insp_db, tech_session, tech2_session, manager_session, base_measurements):
        """(e) قصر التعديل والإرسال على المالك: فني آخر لا يملك حق تعديل أو تقديم فحص زميله."""
        service = InspectionService(insp_db)
        repo = InspectionRepository(insp_db)

        # الفني 1 ينشئ مسودة الفحص
        insp_id = service.create_draft(detector_id=1, session=tech_session)

        # الفني 2 يحاول تعديل بيانات مسودة الفني 1
        with pytest.raises(PermissionError):
            repo.save_hpge_measurements(insp_id, base_measurements, tech2_session)
        with pytest.raises(PermissionError):
            repo.save_peaks(insp_id, [], tech2_session)
        with pytest.raises(PermissionError):
            repo.link_instruments(insp_id, [], tech2_session)
        with pytest.raises(PermissionError):
            repo.link_sources(insp_id, [], tech2_session)
        with pytest.raises(PermissionError):
            repo.record_retry(insp_id, 1, "سبب", "إجراء", None, None, tech2_session)

        # الفني 1 يسجل القياسات بنجاح
        service.save_hpge_measurements(insp_id, base_measurements, tech_session)

        # الفني 2 يحاول تقديم فحص الفني 1
        with pytest.raises(PermissionError) as exc_sub:
            service.submit_for_approval(insp_id, tech2_session)
        assert "الفني صاحب الفحص" in str(exc_sub.value)

        # الفني 1 يقدّم الفحص بنجاح
        service.submit_for_approval(insp_id, tech_session)

        # المدير يرفض الفحص دون إعادته للمسودة تلقائياً (REJECTED)
        service.reject_inspection(insp_id, "ملاحظة فنية تتطلب المراجعة", manager_session, return_to_draft=False)

        # الفني 2 يحاول إعادة الفحص إلى مسودة
        with pytest.raises(PermissionError) as exc_ret:
            service.return_to_draft(insp_id, tech2_session)
        assert "الفني صاحب الفحص" in str(exc_ret.value)

        # الفني 1 يعيد الفحص إلى مسودة بنجاح
        res = service.return_to_draft(insp_id, tech_session)
        assert res["inspection"]["status"] == "DRAFT"

    def test_sequential_report_number_atomic_generation(self, insp_db, tech_session, manager_session, base_measurements):
        """(f) تسلسل أرقام التقارير ومنع التكرار بالتوليد الذري داخل المعاملة."""
        service = InspectionService(insp_db)

        year = datetime.now(timezone.utc).year
        numbers = []
        for i in range(5):
            insp_id = service.create_draft(detector_id=1, session=tech_session)
            service.save_hpge_measurements(insp_id, base_measurements, tech_session)
            service.submit_for_approval(insp_id, tech_session)
            approved = service.approve_inspection(insp_id, manager_session)
            numbers.append(approved["inspection"]["report_number"])

        assert len(numbers) == 5
        assert len(set(numbers)) == 5
        expected = [f"KASHIF-HPGE-{year}-{idx:04d}" for idx in range(1, 6)]
        assert numbers == expected






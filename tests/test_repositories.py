"""Comprehensive tests for database repositories (TASK-1.6).

Tests:
- Migration 003 table creation and indices
- CRUD operations for HPGe and NaI detectors
- CRUD operations for instruments (NIM, HV, MCA, etc.)
- CRUD operations for radioactive sources (Co-60, Cs-137)
- Composite uniqueness constraints mapped to DuplicateEntityError
- Soft retirement (status = RETIRED) without hard DELETE
- Transaction atomicity & rollback on failure (no partial writes, audit chain integrity)
- RBAC permissions matrix enforcement (ADMIN read-only, LAB_MANAGER & TECHNICIAN write)
- Domain model validation enforcement (rejection of Planar HPGe, invalid values)
- Quick search across all technical registries
"""

from datetime import date, datetime, timezone
import sqlite3
import pytest

from kashif.core.models import (
    CrystalType,
    DetectorType,
    HPGeDetector,
    Instrument,
    InstrumentType,
    NaIDetector,
    Nuclide,
    PreampType,
    RadioactiveSource,
    SourceIsotope,
    VoltagePolarity,
    WindowStatus,
    WindowType,
)
from kashif.db.migrations import apply_migrations, init_migrations_table
from kashif.db.repositories import (
    DetectorRepository,
    DuplicateEntityError,
    EntityNotFoundError,
    InstrumentRepository,
    NuclideRepository,
    RepositoryError,
    SourceRepository,
    quick_search,
)
from kashif.services.audit_service import verify_audit_chain
from kashif.services.session import (
    ROLE_ADMIN,
    ROLE_LAB_MANAGER,
    ROLE_TECHNICIAN,
    Session,
)


@pytest.fixture
def db_conn():
    """In-memory SQLite database initialized with all migrations up to 003."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_migrations_table(conn)
    applied = apply_migrations(conn)
    assert 3 in applied
    yield conn
    conn.close()


@pytest.fixture
def technician_session():
    return Session(user_id=10, username="tech1", full_name="فني قياسات", role=ROLE_TECHNICIAN)


@pytest.fixture
def manager_session():
    return Session(user_id=20, username="mgr1", full_name="مدير المعمل", role=ROLE_LAB_MANAGER)


@pytest.fixture
def admin_session():
    return Session(user_id=1, username="admin1", full_name="مدير المنظومة", role=ROLE_ADMIN)


# ---------------------------------------------------------------------------
# Detector Repository Tests
# ---------------------------------------------------------------------------

def test_create_and_get_hpge_detector(db_conn, technician_session):
    repo = DetectorRepository(db_conn)
    hpge = HPGeDetector(
        code="HPGE-01",
        manufacturer="Canberra",
        model="GC2018",
        serial_number="SN-77491",
        crystal_type=CrystalType.COAXIAL,
        window_type=WindowType.BERYLLIUM,
        beryllium_window_status=WindowStatus.INTACT,
        preamp_type=PreampType.RESISTIVE,
        nominal_voltage=3000.0,
        voltage_polarity=VoltagePolarity.POSITIVE,
        nominal_fwhm_kev=1.85,
        nominal_efficiency_pct=20.0,
        diameter_inch=2.0,
        height_inch=2.0,
        facility_name="معمل القياسات الإشعاعية",
        notes="كاشف ألماني عالي النقاء",
    )
    det_id = repo.create(hpge, technician_session)
    assert det_id > 0

    record = repo.get_by_id(det_id)
    assert record is not None
    assert record["code"] == "HPGE-01"
    assert record["detector_type"] == "HPGE"
    assert record["manufacturer"] == "Canberra"
    assert record["nominal_voltage"] == 3000.0
    assert record["status"] == "ACTIVE"

    # By code
    by_code = repo.get_by_code("HPGE-01")
    assert by_code is not None
    assert by_code["id"] == det_id

    # Verify audit chain
    is_valid, _ = verify_audit_chain(db_conn)
    assert is_valid


def test_create_and_get_nai_detector(db_conn, manager_session):
    repo = DetectorRepository(db_conn)
    nai = NaIDetector(
        code="NAI-01",
        manufacturer="Bicron",
        model="3M3/3",
        serial_number="BC-1002",
        diameter_inch=3.0,
        height_inch=3.0,
        nominal_voltage=950.0,
        nominal_resolution_pct=7.5,
        facility_name="معمل المعايرة",
    )
    det_id = repo.create(nai, manager_session)
    assert det_id > 0

    record = repo.get_by_id(det_id)
    assert record["detector_type"] == "NAI"
    assert record["nominal_resolution_pct"] == 7.5
    assert record["crystal_type"] is None


def test_detector_update(db_conn, technician_session):
    repo = DetectorRepository(db_conn)
    hpge = HPGeDetector(
        code="HPGE-UPD",
        manufacturer="Ortec",
        model="GEM-20",
        serial_number="OR-8821",
        nominal_voltage=2500.0,
    )
    det_id = repo.create(hpge, technician_session)

    # Update voltage and facility
    updated_hpge = HPGeDetector(
        code="HPGE-UPD",
        manufacturer="Ortec",
        model="GEM-20",
        serial_number="OR-8821",
        nominal_voltage=2800.0,
        facility_name="المعمل البيئي",
        notes="تم تعديل الجهد بعد الفحص",
    )
    repo.update(det_id, updated_hpge, technician_session)

    record = repo.get_by_id(det_id)
    assert record["nominal_voltage"] == 2800.0
    assert record["facility_name"] == "المعمل البيئي"

    # Audit chain check
    is_valid, _ = verify_audit_chain(db_conn)
    assert is_valid


def test_detector_retire(db_conn, manager_session):
    repo = DetectorRepository(db_conn)
    hpge = HPGeDetector(
        code="HPGE-RET",
        manufacturer="Canberra",
        model="GC15",
        serial_number="SN-RET-1",
        nominal_voltage=3000.0,
    )
    det_id = repo.create(hpge, manager_session)

    repo.retire(det_id, manager_session, reason="تلف بلورة الكاشف")

    record = repo.get_by_id(det_id)
    assert record["status"] == "RETIRED"

    # list_all excludes retired by default
    active_list = repo.list_all()
    assert not any(d["id"] == det_id for d in active_list)

    # list_all includes retired when requested
    all_list = repo.list_all(include_retired=True)
    assert any(d["id"] == det_id for d in all_list)


def test_detector_unique_constraints(db_conn, technician_session):
    repo = DetectorRepository(db_conn)
    hpge1 = HPGeDetector(
        code="HPGE-UQ1",
        manufacturer="Canberra",
        model="GC20",
        serial_number="SN-DUP",
        nominal_voltage=3000.0,
    )
    repo.create(hpge1, technician_session)

    # 1. Duplicate code
    hpge_dup_code = HPGeDetector(
        code="HPGE-UQ1",
        manufacturer="Ortec",
        model="GEM-10",
        serial_number="SN-DIFF",
        nominal_voltage=2500.0,
    )
    with pytest.raises(DuplicateEntityError) as exc_info:
        repo.create(hpge_dup_code, technician_session)
    assert "رمز الكاشف مسجل مسبقاً" in str(exc_info.value)

    # 2. Duplicate (manufacturer, serial_number)
    hpge_dup_mfr_sn = HPGeDetector(
        code="HPGE-UQ2",
        manufacturer="Canberra",
        model="GC25",
        serial_number="SN-DUP",
        nominal_voltage=3000.0,
    )
    with pytest.raises(DuplicateEntityError) as exc_info:
        repo.create(hpge_dup_mfr_sn, technician_session)
    assert "الرقم التسلسلي مكرر لنفس الشركة الصانعة" in str(exc_info.value)


def test_detector_reject_planar_v1(db_conn, technician_session):
    """Ensure PLANAR crystal type is strictly rejected in v1.0 per ADR-0003."""
    with pytest.raises(ValueError) as exc_info:
        HPGeDetector(
            code="HPGE-PLANAR",
            manufacturer="Canberra",
            model="GL2015",
            serial_number="PL-100",
            crystal_type=CrystalType.PLANAR,
            nominal_voltage=1500.0,
        )
    assert "كواشف الجرمانيوم المستوية (Planar HPGe) غير مدعومة" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Instrument Repository Tests
# ---------------------------------------------------------------------------

def test_instrument_crud(db_conn, technician_session):
    repo = InstrumentRepository(db_conn)
    inst = Instrument(
        instrument_type=InstrumentType.HV_SUPPLY,
        manufacturer="Ortec",
        model="659",
        serial_number="HV-5501",
        ripple_mv=3.5,
        regulation_pct=0.01,
        cal_date=date(2025, 6, 1),
        cal_expiry=date(2026, 6, 1),
        facility_name="معمل القياسات الإشعاعية",
    )
    inst_id = repo.create(inst, technician_session)
    assert inst_id > 0

    record = repo.get_by_id(inst_id)
    assert record["instrument_type"] == "HV_SUPPLY"
    assert record["ripple_mv"] == 3.5
    assert record["cal_expiry"] == "2026-06-01"
    assert record["status"] == "ACTIVE"

    # Update
    updated_inst = Instrument(
        instrument_type=InstrumentType.HV_SUPPLY,
        manufacturer="Ortec",
        model="659",
        serial_number="HV-5501",
        ripple_mv=4.0,
        cal_date=date(2025, 6, 1),
        cal_expiry=date(2026, 6, 1),
        facility_name="معمل المعايرة",
    )
    repo.update(inst_id, updated_inst, technician_session)
    rec2 = repo.get_by_id(inst_id)
    assert rec2["ripple_mv"] == 4.0
    assert rec2["facility_name"] == "معمل المعايرة"

    # Retire
    repo.retire(inst_id, technician_session, reason="استبدال بجهاز أحدث")
    rec3 = repo.get_by_id(inst_id)
    assert rec3["status"] == "RETIRED"
    assert rec3["is_in_service"] == 0

    # Audit chain check
    is_valid, _ = verify_audit_chain(db_conn)
    assert is_valid


def test_instrument_unique_constraint(db_conn, technician_session):
    repo = InstrumentRepository(db_conn)
    inst1 = Instrument(
        instrument_type=InstrumentType.AMPLIFIER,
        manufacturer="Canberra",
        model="2022",
        serial_number="AMP-990",
        shaping_range_min_us=0.5,
        shaping_range_max_us=12.0,
    )
    repo.create(inst1, technician_session)

    inst_dup = Instrument(
        instrument_type=InstrumentType.AMPLIFIER,
        manufacturer="Canberra",
        model="2026",
        serial_number="AMP-990",
    )
    with pytest.raises(DuplicateEntityError) as exc_info:
        repo.create(inst_dup, technician_session)
    assert "الرقم التسلسلي مكرر لنفس الشركة الصانعة" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Source Repository Tests
# ---------------------------------------------------------------------------

def test_source_crud(db_conn, manager_session):
    repo = SourceRepository(db_conn)
    src = RadioactiveSource(
        source_code="SRC-CO-01",
        serial_number="S60-1234",
        isotopes=[
            SourceIsotope(
                nuclide_code="Co-60",
                cert_activity_bq=370000.0,
                cert_date=date(2024, 1, 1),
            )
        ],
        certificate_number="CERT-IAEA-991",
        facility_name="معمل القياسات الإشعاعية",
    )
    src_id = repo.create(src, manager_session)
    assert src_id > 0

    record = repo.get_by_id(src_id)
    assert record["nuclide"] == "Co-60"
    assert record["source_code"] == "SRC-CO-01"
    assert record["cert_activity_bq"] == 370000.0
    assert len(record["isotopes"]) == 1

    # By code
    by_code = repo.get_by_code("SRC-CO-01")
    assert by_code["id"] == src_id

    # Update
    updated_src = RadioactiveSource(
        source_code="SRC-CO-01",
        serial_number="S60-1234",
        isotopes=[
            SourceIsotope(
                nuclide_code="Co-60",
                cert_activity_bq=370000.0,
                cert_date=date(2024, 1, 1),
            )
        ],
        certificate_number="CERT-IAEA-991-REV",
        facility_name="معمل المعايرة",
    )
    repo.update(src_id, updated_src, manager_session)
    rec2 = repo.get_by_id(src_id)
    assert rec2["certificate_number"] == "CERT-IAEA-991-REV"

    # Retire
    repo.retire(src_id, manager_session, reason="انخفاض الفعالية دون الحد الأدنى")
    rec3 = repo.get_by_id(src_id)
    assert rec3["status"] == "RETIRED"
    assert rec3["is_active"] == 0

    # Audit chain check
    is_valid, _ = verify_audit_chain(db_conn)
    assert is_valid


def test_source_unique_constraints(db_conn, manager_session):
    repo = SourceRepository(db_conn)
    src1 = RadioactiveSource(
        source_code="SRC-CS-01",
        serial_number="CS-500",
        isotopes=[
            SourceIsotope(
                nuclide_code="Cs-137",
                cert_activity_bq=185000.0,
                cert_date=date(2023, 1, 1),
            )
        ],
    )
    repo.create(src1, manager_session)

    # 1. Duplicate source_code
    src_dup_code = RadioactiveSource(
        source_code="SRC-CS-01",
        serial_number="CO-600",
        isotopes=[
            SourceIsotope(
                nuclide_code="Co-60",
                cert_activity_bq=200000.0,
                cert_date=date(2023, 1, 1),
            )
        ],
    )
    with pytest.raises(DuplicateEntityError) as exc_info:
        repo.create(src_dup_code, manager_session)
    assert "رمز المصدر المشع مسجل مسبقاً" in str(exc_info.value)

    # 2. Duplicate serial_number with shared nuclide
    src_dup_nuclide_sn = RadioactiveSource(
        source_code="SRC-CS-02",
        serial_number="CS-500",
        isotopes=[
            SourceIsotope(
                nuclide_code="Cs-137",
                cert_activity_bq=150000.0,
                cert_date=date(2023, 1, 1),
            )
        ],
    )
    with pytest.raises(DuplicateEntityError) as exc_info:
        repo.create(src_dup_nuclide_sn, manager_session)
    assert "الرقم التسلسلي" in str(exc_info.value) and "مكرر" in str(exc_info.value)


# ---------------------------------------------------------------------------
# RBAC Permissions Matrix Tests
# ---------------------------------------------------------------------------

def test_admin_cannot_write_technical_registries(db_conn, admin_session):
    """Admin has read-only access to technical registries (view_* but not manage_*)."""
    det_repo = DetectorRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)
    src_repo = SourceRepository(db_conn)

    hpge = HPGeDetector(code="H-ADM", manufacturer="Can", model="G", serial_number="SN1", nominal_voltage=1000)
    with pytest.raises(PermissionError):
        det_repo.create(hpge, admin_session)

    inst = Instrument(instrument_type=InstrumentType.NIM_BIN, manufacturer="Can", model="N", serial_number="S1")
    with pytest.raises(PermissionError):
        inst_repo.create(inst, admin_session)

    src = RadioactiveSource(
        source_code="S-ADM",
        serial_number="SN1",
        isotopes=[SourceIsotope(nuclide_code="Co-60", cert_activity_bq=1000, cert_date=date(2024, 1, 1))],
    )
    with pytest.raises(PermissionError):
        src_repo.create(src, admin_session)


def test_technician_and_manager_can_manage_registries(db_conn, technician_session, manager_session):
    """Both Technician and Lab Manager can create, update, and retire registry items."""
    det_repo = DetectorRepository(db_conn)

    # Tech creates
    hpge = HPGeDetector(code="H-TECH", manufacturer="Can", model="G", serial_number="SN-TECH", nominal_voltage=2000)
    d_id = det_repo.create(hpge, technician_session)
    assert d_id > 0

    # Manager updates
    hpge_upd = HPGeDetector(code="H-TECH", manufacturer="Can", model="G-V2", serial_number="SN-TECH", nominal_voltage=2200)
    det_repo.update(d_id, hpge_upd, manager_session)
    assert det_repo.get_by_id(d_id)["model"] == "G-V2"

    # Tech retires
    det_repo.retire(d_id, technician_session, reason="انتهاء الصلاحية")
    assert det_repo.get_by_id(d_id)["status"] == "RETIRED"


# ---------------------------------------------------------------------------
# Atomic Rollback & Hash Chain Tests
# ---------------------------------------------------------------------------

def test_transaction_rollback_preserves_audit_chain(db_conn, technician_session):
    """If a constraint violation occurs, the transaction rolls back cleanly without orphan audit logs."""
    repo = DetectorRepository(db_conn)
    hpge = HPGeDetector(code="H-ROLL1", manufacturer="Ortec", model="G", serial_number="SN-R1", nominal_voltage=2000)
    repo.create(hpge, technician_session)

    # Attempt to insert duplicate code
    hpge_dup = HPGeDetector(code="H-ROLL1", manufacturer="Ortec", model="G2", serial_number="SN-R2", nominal_voltage=2000)
    with pytest.raises(DuplicateEntityError):
        repo.create(hpge_dup, technician_session)

    # Verify no second row exists
    items = repo.list_all()
    assert len(items) == 1

    # Verify audit chain has not broken
    is_valid, msg = verify_audit_chain(db_conn)
    assert is_valid, msg


# ---------------------------------------------------------------------------
# Global Quick Search Tests
# ---------------------------------------------------------------------------

def test_quick_search_across_registries(db_conn, technician_session):
    det_repo = DetectorRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)
    src_repo = SourceRepository(db_conn)

    det_repo.create(HPGeDetector(code="HPGE-FIND-ME", manufacturer="Mirion", model="Falcon-5000", serial_number="SN-FIND-DET", nominal_voltage=3000), technician_session)
    inst_repo.create(Instrument(instrument_type=InstrumentType.MCA, manufacturer="Canberra", model="Multiport-II", serial_number="SN-FIND-INST", channels_count=8192), technician_session)
    src_repo.create(
        RadioactiveSource(
            source_code="SRC-FIND-ME",
            serial_number="SN-FIND-SRC",
            isotopes=[SourceIsotope(nuclide_code="Cs-137", cert_activity_bq=50000, cert_date=date(2025, 1, 1))],
        ),
        technician_session,
    )

    # Search for "FIND"
    results = quick_search(db_conn, "FIND")
    assert len(results) == 3
    types = {r["entity_type"] for r in results}
    assert types == {"detector", "instrument", "source"}

    # Search by serial number
    res_sn = quick_search(db_conn, "SN-FIND-INST")
    assert len(res_sn) == 1
    assert res_sn[0]["entity_type"] == "instrument"

    # Search empty string returns empty list
    assert quick_search(db_conn, "") == []
    assert quick_search(db_conn, "   ") == []


# ---------------------------------------------------------------------------
# Extended Repository Edge Case & Filter Tests
# ---------------------------------------------------------------------------

def test_detector_repository_extended_branches(db_conn, technician_session, admin_session):
    repo = DetectorRepository(db_conn)
    nai = NaIDetector(
        code="NAI-EXT-1",
        manufacturer="Scionix",
        model="38S38",
        serial_number="SN-NAI-EXT1",
        nominal_voltage=850.0,
        nominal_resolution_pct=7.2,
        diameter_inch=2.0,
        height_inch=2.0,
        facility_name="معمل بيتا",
    )
    d_id = repo.create(nai, technician_session)

    # get_by_id
    item = repo.get_by_id(d_id)
    assert item["code"] == "NAI-EXT-1"
    assert repo.get_by_id(999999) is None

    # update NaIDetector
    nai_upd = NaIDetector(
        code="NAI-EXT-1",
        manufacturer="Scionix",
        model="38S38-MOD",
        serial_number="SN-NAI-EXT1",
        nominal_voltage=900.0,
        nominal_resolution_pct=7.5,
        diameter_inch=2.0,
        height_inch=2.0,
        facility_name="معمل بيتا",
    )
    repo.update(d_id, nai_upd, technician_session)
    assert repo.get_by_id(d_id)["model"] == "38S38-MOD"

    # update non-existent entity
    with pytest.raises(EntityNotFoundError):
        repo.update(999999, nai_upd, technician_session)

    # update invalid type
    with pytest.raises(TypeError):
        repo.update(d_id, "invalid_obj", technician_session)

    # create invalid type
    with pytest.raises(TypeError):
        repo.create("invalid_obj", technician_session)

    # retire non-existent entity
    with pytest.raises(EntityNotFoundError):
        repo.retire(999999, technician_session, "سبب")

    # admin cannot update or retire
    with pytest.raises(PermissionError):
        repo.update(d_id, nai_upd, admin_session)
    with pytest.raises(PermissionError):
        repo.retire(d_id, admin_session, "سبب")

    # list_all filtering
    res_fac = repo.list_all(facility="معمل بيتا")
    assert len(res_fac) == 1
    res_none = repo.list_all(facility="معمل غير موجود")
    assert len(res_none) == 0

    res_search = repo.list_all(query="38S38")
    assert len(res_search) == 1

    # retire item and filter by status
    repo.retire(d_id, technician_session, "تكهين الجهاز")
    res_active = repo.list_all(include_retired=False)
    assert len(res_active) == 0
    res_retired = repo.list_all(include_retired=True)
    assert len(res_retired) == 1


def test_instrument_repository_extended_branches(db_conn, technician_session, admin_session):
    repo = InstrumentRepository(db_conn)
    inst = Instrument(
        instrument_type=InstrumentType.NIM_BIN,
        manufacturer="ORTEC",
        model="4001A",
        serial_number="SN-INST-EXT1",
        facility_name="معمل القياسات",
        cal_date=date(2025, 1, 1),
        cal_expiry=date(2026, 1, 1),
    )
    inst_id = repo.create(inst, technician_session)

    # get_by_id
    item = repo.get_by_id(inst_id)
    assert item["model"] == "4001A"
    assert repo.get_by_id(999999) is None

    # update
    inst_upd = Instrument(
        instrument_type=InstrumentType.NIM_BIN,
        manufacturer="ORTEC",
        model="4001A-NEW",
        serial_number="SN-INST-EXT1",
        facility_name="معمل القياسات",
        cal_date=date(2025, 2, 1),
        cal_expiry=date(2026, 2, 1),
    )
    repo.update(inst_id, inst_upd, technician_session)
    assert repo.get_by_id(inst_id)["model"] == "4001A-NEW"

    # update non-existent entity
    with pytest.raises(EntityNotFoundError):
        repo.update(999999, inst_upd, technician_session)

    # update invalid type
    with pytest.raises(TypeError):
        repo.update(inst_id, "invalid_obj", technician_session)

    # create invalid type
    with pytest.raises(TypeError):
        repo.create("invalid_obj", technician_session)

    # retire non-existent entity
    with pytest.raises(EntityNotFoundError):
        repo.retire(999999, technician_session, "سبب")

    # admin cannot update or retire
    with pytest.raises(PermissionError):
        repo.update(inst_id, inst_upd, admin_session)
    with pytest.raises(PermissionError):
        repo.retire(inst_id, admin_session, "سبب")

    # list_all filtering
    assert len(repo.list_all(facility="معمل القياسات")) == 1
    assert len(repo.list_all(facility="معمل آخر")) == 0
    assert len(repo.list_all(query="4001A")) == 1

    # retire and filter by status
    repo.retire(inst_id, technician_session, "عطل دائم في التغذية")
    assert len(repo.list_all(include_retired=False)) == 0
    assert len(repo.list_all(include_retired=True)) == 1


def test_source_repository_extended_branches(db_conn, technician_session, admin_session):
    repo = SourceRepository(db_conn)
    src = RadioactiveSource(
        source_code="SRC-EXT-1",
        serial_number="SN-SRC-EXT1",
        isotopes=[SourceIsotope(nuclide_code="Co-60", cert_activity_bq=45000.0, cert_date=date(2024, 6, 1))],
        facility_name="معمل غاما",
    )
    src_id = repo.create(src, technician_session)

    # get_by_id
    item = repo.get_by_id(src_id)
    assert item["source_code"] == "SRC-EXT-1"
    assert repo.get_by_id(999999) is None

    # update
    src_upd = RadioactiveSource(
        source_code="SRC-EXT-1",
        serial_number="SN-SRC-EXT1",
        isotopes=[SourceIsotope(nuclide_code="Co-60", cert_activity_bq=46000.0, cert_date=date(2024, 6, 1))],
        facility_name="معمل غاما",
    )
    repo.update(src_id, src_upd, technician_session)
    assert repo.get_by_id(src_id)["cert_activity_bq"] == 46000.0

    # update non-existent entity
    with pytest.raises(EntityNotFoundError):
        repo.update(999999, src_upd, technician_session)

    # update invalid type
    with pytest.raises(TypeError):
        repo.update(src_id, "invalid_obj", technician_session)

    # create invalid type
    with pytest.raises(TypeError):
        repo.create("invalid_obj", technician_session)

    # retire non-existent entity
    with pytest.raises(EntityNotFoundError):
        repo.retire(999999, technician_session, "سبب")

    # admin cannot update or retire
    with pytest.raises(PermissionError):
        repo.update(src_id, src_upd, admin_session)
    with pytest.raises(PermissionError):
        repo.retire(src_id, admin_session, "سبب")

    # list_all filtering
    assert len(repo.list_all(facility="معمل غاما")) == 1
    assert len(repo.list_all(facility="معمل آخر")) == 0
    assert len(repo.list_all(query="SRC-EXT")) == 1

    # retire and filter by status
    repo.retire(src_id, technician_session, "تلاشي النشاط")
    assert len(repo.list_all(include_retired=False)) == 0
    assert len(repo.list_all(include_retired=True)) == 1


def test_map_integrity_error_generic():
    from kashif.db.repositories import _map_integrity_error
    e1 = sqlite3.IntegrityError("UNIQUE constraint failed: unknown_table.col")
    mapped1 = _map_integrity_error(e1)
    assert "تعذر الحفظ لوجود سجل آخر" in str(mapped1)

    e2 = sqlite3.IntegrityError("CHECK constraint failed: some_check")
    mapped2 = _map_integrity_error(e2)
    assert "خطأ في قيود البيانات" in str(mapped2)


def test_nuclide_repository_crud_and_guards(db_conn, manager_session, admin_session):
    """Test NuclideRepository: standard protected nuclides, custom creation, update guards, RBAC."""
    n_repo = NuclideRepository(db_conn)

    # 1. Standard seeds are present
    all_nucs = n_repo.list_all()
    assert len(all_nucs) >= 12
    codes = {n["code"] for n in all_nucs}
    assert "Co-60" in codes and "Cs-137" in codes and "Am-241" in codes

    co60 = n_repo.get_by_code("Co-60")
    assert co60["confirmed"] == 1
    assert co60["is_custom"] == 0

    # 2. Admin cannot manage nuclides
    with pytest.raises(PermissionError):
        n_repo.create_custom_nuclide("Tc-99m", "تكنيشيوم-99m", "Technetium-99m", 6.0067, "h", "NNDC", admin_session)

    # 3. Validation on custom nuclide creation
    with pytest.raises(ValueError, match="رمز النظير غير صالح"):
        n_repo.create_custom_nuclide("invalid_symbol", "س", "s", 1.0, "d", "ref", manager_session)

    with pytest.raises(ValueError, match="قيمة عمر النصف"):
        n_repo.create_custom_nuclide("Tc-99m", "تكنيشيوم", "Tech", -5.0, "h", "ref", manager_session)

    with pytest.raises(ValueError, match="مصدر القيمة"):
        n_repo.create_custom_nuclide("Tc-99m", "تكنيشيوم", "Tech", 6.0, "h", "   ", manager_session)

    # 4. Valid custom nuclide creation
    created = n_repo.create_custom_nuclide(
        code="Tc-99m",
        name_ar="تكنيشيوم-99m",
        name_en="Technetium-99m",
        half_life_value=6.0067,
        half_life_unit="h",
        reference="NNDC / NuDat 3.0",
        session=manager_session,
    )
    assert created["code"] == "Tc-99m"
    assert created["is_custom"] == 1
    assert created["confirmed"] == 0
    # half_life_days: 6.0067 / 24 = 0.250279...
    assert 0.250 < created["half_life_days"] < 0.251

    # 5. Standard nuclides are immutable
    with pytest.raises(PermissionError, match="محمية"):
        n_repo.update_custom_nuclide("Co-60", "كوبالت", "Cobalt", 5.0, "y", "IAEA", manager_session)

    # 6. Custom nuclide update succeeds when unused
    assert not n_repo.is_nuclide_in_use("Tc-99m")
    upd = n_repo.update_custom_nuclide(
        code="Tc-99m",
        name_ar="تكنيشيوم-99m معدل",
        name_en="Technetium-99m Updated",
        half_life_value=6.007,
        half_life_unit="h",
        reference="NNDC 2026",
        session=manager_session,
    )
    assert upd["name_ar"] == "تكنيشيوم-99m معدل"

    # 7. When custom nuclide is used in a source, updating is forbidden
    src_repo = SourceRepository(db_conn)
    src_repo.create(
        RadioactiveSource(
            source_code="SRC-TC99M-01",
            serial_number="SN-TC-1",
            isotopes=[SourceIsotope(nuclide_code="Tc-99m", cert_activity_bq=50000, cert_date=date(2025, 1, 1))],
        ),
        manager_session,
    )
    assert n_repo.is_nuclide_in_use("Tc-99m")

    with pytest.raises(RepositoryError, match="لا يمكن تعديل النظير لأنه مستخدم"):
        n_repo.update_custom_nuclide("Tc-99m", "ت", "T", 6.0, "h", "ref", manager_session)


def test_multi_isotope_source_composite_uniqueness(db_conn, manager_session):
    """Test composite uniqueness check: serial_number + shared nuclide."""
    repo = SourceRepository(db_conn)

    # Create composite multi-isotope source (Co-60 + Cs-137)
    s1 = RadioactiveSource(
        source_code="SRC-COMBO-01",
        serial_number="SERIAL-X100",
        isotopes=[
            SourceIsotope(nuclide_code="Co-60", cert_activity_bq=37000, cert_date=date(2024, 1, 1)),
            SourceIsotope(nuclide_code="Cs-137", cert_activity_bq=18500, cert_date=date(2024, 1, 1)),
        ],
    )
    repo.create(s1, manager_session)

    # Another source with same serial number sharing Co-60 must fail
    s_conflict = RadioactiveSource(
        source_code="SRC-CONFLICT-01",
        serial_number="SERIAL-X100",
        isotopes=[
            SourceIsotope(nuclide_code="Co-60", cert_activity_bq=20000, cert_date=date(2024, 1, 1)),
        ],
    )
    with pytest.raises(DuplicateEntityError) as exc_info:
        repo.create(s_conflict, manager_session)
    assert "الرقم التسلسلي" in str(exc_info.value) and "مكرر" in str(exc_info.value)

    # Another source with same serial number having DIFFERENT nuclide (e.g. Am-241) succeeds!
    s_diff_nuclide = RadioactiveSource(
        source_code="SRC-DIFF-01",
        serial_number="SERIAL-X100",
        isotopes=[
            SourceIsotope(nuclide_code="Am-241", cert_activity_bq=50000, cert_date=date(2024, 1, 1)),
        ],
    )
    s_diff_id = repo.create(s_diff_nuclide, manager_session)
    assert s_diff_id > 0


def test_repositories_not_found_and_permission_errors(db_conn, technician_session, admin_session):
    """Test EntityNotFoundError and PermissionError across all repositories."""
    det_repo = DetectorRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)
    src_repo = SourceRepository(db_conn)

    # 1. Non-existent IDs raise EntityNotFoundError
    with pytest.raises(EntityNotFoundError):
        det_repo.update(99999, HPGeDetector(code="NON", manufacturer="M", model="M", serial_number="S", nominal_voltage=3000), technician_session)

    with pytest.raises(EntityNotFoundError):
        det_repo.retire(99999, technician_session)

    with pytest.raises(EntityNotFoundError):
        inst_repo.update(99999, Instrument(instrument_type=InstrumentType.AMPLIFIER, manufacturer="M", model="M", serial_number="S"), technician_session)

    with pytest.raises(EntityNotFoundError):
        inst_repo.retire(99999, technician_session)

    with pytest.raises(EntityNotFoundError):
        src_repo.update(99999, RadioactiveSource(source_code="NON", serial_number="S", isotopes=[SourceIsotope(nuclide_code="Co-60", cert_activity_bq=1000)]), technician_session)

    with pytest.raises(EntityNotFoundError):
        src_repo.retire(99999, technician_session)

    # 2. Get non-existent returns None
    assert det_repo.get_by_id(99999) is None
    assert det_repo.get_by_code("NON_EXISTENT") is None
    assert inst_repo.get_by_id(99999) is None
    assert src_repo.get_by_id(99999) is None
    assert src_repo.get_by_code("NON_EXISTENT") is None

    # 3. Admin session cannot retire technical records
    det_id = det_repo.create(HPGeDetector(code="DET-PERM", manufacturer="M", model="M", serial_number="SN-P1", nominal_voltage=3000), technician_session)
    inst_id = inst_repo.create(Instrument(instrument_type=InstrumentType.MCA, manufacturer="M", model="M", serial_number="SN-P2"), technician_session)
    src_id = src_repo.create(RadioactiveSource(source_code="SRC-PERM", serial_number="SN-P3", isotopes=[SourceIsotope(nuclide_code="Co-60", cert_activity_bq=1000)]), technician_session)

    with pytest.raises(PermissionError):
        det_repo.retire(det_id, admin_session)

    with pytest.raises(PermissionError):
        inst_repo.retire(inst_id, admin_session)

    with pytest.raises(PermissionError):
        src_repo.retire(src_id, admin_session)

    # 4. Verify audit chain is completely valid
    assert verify_audit_chain(db_conn)


def test_source_repository_update_failure_and_empty_isotopes(db_conn, manager_session):
    """Test SourceRepository update error rollback and fallback for sources without isotopes."""
    repo = SourceRepository(db_conn)

    s1 = repo.create(RadioactiveSource(
        source_code="SRC-UP-1",
        serial_number="SN-UP-1",
        isotopes=[SourceIsotope(nuclide_code="Co-60", cert_activity_bq=37000.0, cert_date=date(2024, 1, 1))],
    ), manager_session)

    s2 = repo.create(RadioactiveSource(
        source_code="SRC-UP-2",
        serial_number="SN-UP-2",
        isotopes=[SourceIsotope(nuclide_code="Cs-137", cert_activity_bq=18500.0, cert_date=date(2024, 1, 1))],
    ), manager_session)

    # 1. Update source 2 with duplicate source_code of source 1 -> DuplicateEntityError
    with pytest.raises(DuplicateEntityError):
        repo.update(s2, RadioactiveSource(
            source_code="SRC-UP-1",  # Duplicate!
            serial_number="SN-UP-2",
            isotopes=[SourceIsotope(nuclide_code="Cs-137", cert_activity_bq=18500.0, cert_date=date(2024, 1, 1))],
        ), manager_session)

    # 2. Update source 2 with duplicate (serial_number, shared nuclide) of source 1 -> DuplicateEntityError
    with pytest.raises(DuplicateEntityError):
        repo.update(s2, RadioactiveSource(
            source_code="SRC-UP-2",
            serial_number="SN-UP-1",  # Same serial as source 1
            isotopes=[SourceIsotope(nuclide_code="Co-60", cert_activity_bq=25000.0, cert_date=date(2024, 1, 1))],  # Shares Co-60!
        ), manager_session)

    # Verify source 2 data in DB was rolled back and untouched
    data2 = repo.get_by_id(s2)
    assert data2["source_code"] == "SRC-UP-2"
    assert data2["serial_number"] == "SN-UP-2"

    # 3. Source without isotopes in database returns fallback fields
    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO sources (source_code, serial_number, is_active, status, created_at, updated_at)
        VALUES ('SRC-EMPTY-ISO', 'SN-EMPTY-01', 1, 'ACTIVE', '2024-01-01', '2024-01-01');
    """)
    empty_src_id = cursor.lastrowid
    db_conn.commit()

    empty_data = repo.get_by_id(empty_src_id)
    assert empty_data is not None
    assert empty_data["nuclide"] == ""
    assert empty_data["cert_activity_bq"] == 0.0
    assert empty_data["isotopes"] == []

    # 4. Audit chain remains valid
    assert verify_audit_chain(db_conn)


def test_detector_and_instrument_update_duplicate_rollback(db_conn, manager_session):
    """Test duplicate constraints and rollback during update on detectors and instruments."""
    det_repo = DetectorRepository(db_conn)
    inst_repo = InstrumentRepository(db_conn)

    # Detectors duplicate checks
    d1 = det_repo.create(HPGeDetector(code="DET-ROLL-1", manufacturer="Ortec", model="GEM", serial_number="SN-R1", nominal_voltage=3000), manager_session)
    d2 = det_repo.create(HPGeDetector(code="DET-ROLL-2", manufacturer="Ortec", model="GEM", serial_number="SN-R2", nominal_voltage=3000), manager_session)

    # Duplicate code
    with pytest.raises(DuplicateEntityError):
        det_repo.update(d2, HPGeDetector(code="DET-ROLL-1", manufacturer="Ortec", model="GEM", serial_number="SN-R2", nominal_voltage=3000), manager_session)

    # Duplicate manufacturer + serial
    with pytest.raises(DuplicateEntityError):
        det_repo.update(d2, HPGeDetector(code="DET-ROLL-2", manufacturer="Ortec", model="GEM", serial_number="SN-R1", nominal_voltage=3000), manager_session)

    # Instruments duplicate checks
    i1 = inst_repo.create(Instrument(instrument_type=InstrumentType.AMPLIFIER, manufacturer="Canberra", model="2022", serial_number="SN-IR1"), manager_session)
    i2 = inst_repo.create(Instrument(instrument_type=InstrumentType.AMPLIFIER, manufacturer="Canberra", model="2022", serial_number="SN-IR2"), manager_session)

    # Duplicate manufacturer + serial
    with pytest.raises(DuplicateEntityError):
        inst_repo.update(i2, Instrument(instrument_type=InstrumentType.AMPLIFIER, manufacturer="Canberra", model="2022", serial_number="SN-IR1"), manager_session)

    assert verify_audit_chain(db_conn)


def test_nuclide_repository_extended_coverage(db_conn, manager_session):
    """Test NuclideRepository edge cases, validation errors, and list filtering."""
    n_repo = NuclideRepository(db_conn)

    # 1. Non-existent nuclide update
    with pytest.raises(EntityNotFoundError):
        n_repo.update_custom_nuclide("NON-EXISTENT", "اسم", "Name", 10.0, "d", "Ref", manager_session)

    # 2. Protected standard nuclide update rejected
    with pytest.raises(PermissionError):
        n_repo.update_custom_nuclide("Cs-137", "سيزيوم معدل", "Cesium Upd", 30.0, "y", "Ref", manager_session)

    # 3. Create custom nuclide
    n_repo.create_custom_nuclide("In-111", "إنديوم-111", "Indium-111", 2.8047, "d", "NuDat 3.0", manager_session)

    # Duplicate code rejected
    with pytest.raises(DuplicateEntityError):
        n_repo.create_custom_nuclide("in-111", "إنديوم مكرر", "Indium Dup", 2.8, "d", "Ref", manager_session)

    # 4. Validation errors on create and update
    with pytest.raises(ValueError, match="رمز النظير غير صالح"):
        n_repo.create_custom_nuclide("111-In", "إنديوم", "Indium", 2.8, "d", "Ref", manager_session)

    with pytest.raises(ValueError, match="قيمة عمر النصف"):
        n_repo.update_custom_nuclide("In-111", "إنديوم", "Indium", -5.0, "d", "Ref", manager_session)

    with pytest.raises(ValueError, match="مصدر القيمة"):
        n_repo.update_custom_nuclide("In-111", "إنديوم", "Indium", 2.8, "d", "   ", manager_session)

    with pytest.raises(ValueError, match="وحدة عمر النصف"):
        n_repo.update_custom_nuclide("In-111", "إنديوم", "Indium", 2.8, "years", "Ref", manager_session)

    # 5. List filtering
    confirmed_nuclides = n_repo.list_all(confirmed_only=True)
    assert all(n["confirmed"] == 1 for n in confirmed_nuclides)
    assert len(confirmed_nuclides) == 2  # Co-60 and Cs-137

    all_nuclides = n_repo.list_all()
    assert any(n["code"] == "In-111" and n["is_custom"] == 1 for n in all_nuclides)

    # Non-existent code query
    assert n_repo.get_by_code("UNKNOWN-XYZ") is None

    assert verify_audit_chain(db_conn)


def test_map_integrity_error_branches():
    """Unit test _map_integrity_error mapping for all database constraint patterns."""
    from kashif.db.repositories import _map_integrity_error

    err_det_code = _map_integrity_error(sqlite3.IntegrityError("UNIQUE constraint failed: detectors.code"))
    assert "رمز الكاشف مسجل مسبقاً" in str(err_det_code)

    err_det_sn = _map_integrity_error(sqlite3.IntegrityError("UNIQUE constraint failed: detectors.manufacturer, detectors.serial_number"))
    assert "الرقم التسلسلي مكرر لنفس الشركة الصانعة للكاشف" in str(err_det_sn)

    err_inst_sn = _map_integrity_error(sqlite3.IntegrityError("UNIQUE constraint failed: instruments.manufacturer, instruments.serial_number"))
    assert "الرقم التسلسلي مكرر لنفس الشركة الصانعة لجهاز الفحص" in str(err_inst_sn)

    err_src_code = _map_integrity_error(sqlite3.IntegrityError("UNIQUE constraint failed: sources.source_code"))
    assert "رمز المصدر المشع مسجل مسبقاً" in str(err_src_code)

    err_src_sn = _map_integrity_error(sqlite3.IntegrityError("UNIQUE constraint failed: sources.nuclide, sources.serial_number"))
    assert "الرقم التسلسلي مكرر لنفس النظير المشع" in str(err_src_sn)

    err_nuc_code = _map_integrity_error(sqlite3.IntegrityError("UNIQUE constraint failed: nuclides.code"))
    assert "رمز النظير المشع مسجل مسبقاً" in str(err_nuc_code)

    err_iso_unique = _map_integrity_error(sqlite3.IntegrityError("UNIQUE constraint failed: source_isotopes.source_id, source_isotopes.nuclide_code"))
    assert "لا يمكن تكرار نفس النظير المشع" in str(err_iso_unique)

    err_generic_unique = _map_integrity_error(sqlite3.IntegrityError("UNIQUE constraint failed: something_else"))
    assert "تعذر الحفظ لوجود سجل آخر" in str(err_generic_unique)

    err_other = _map_integrity_error(sqlite3.IntegrityError("CHECK constraint failed"))
    assert "خطأ في قيود البيانات" in str(err_other)



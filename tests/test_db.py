"""Integration and unit tests for KASHIF-QA Phase 0.

Tests SQLite WAL connectivity, schema migrations, audit log cryptographic
hash chaining, tamper-evident trigger enforcement, Argon2id security,
and administrator provisioning.
"""

import math
import os
import sqlite3
import tempfile
import pytest

from kashif.db.connection import create_connection
from kashif.db.migrations import apply_migrations, get_applied_migrations
from kashif.services.audit_service import (
    GENESIS_HASH,
    record_audit_event,
    verify_audit_chain,
)
from kashif.services.security import (
    AuthenticationError,
    GENERIC_LOGIN_ERROR,
    authenticate_user,
    create_first_admin,
    hash_password,
    hash_password_pbkdf2,
    reset_admin_with_recovery_key,
    verify_password,
)


@pytest.fixture
def temp_db_path():
    """Create a temporary database file on disk to test file-based SQLite and WAL mode."""
    temp_dir = tempfile.mkdtemp()
    db_file = os.path.join(temp_dir, "test_kashif.db")
    yield db_file
    # Cleanup
    try:
        if os.path.exists(db_file):
            os.remove(db_file)
        wal_file = db_file + "-wal"
        if os.path.exists(wal_file):
            os.remove(wal_file)
        shm_file = db_file + "-shm"
        if os.path.exists(shm_file):
            os.remove(shm_file)
        os.rmdir(temp_dir)
    except Exception:
        pass


@pytest.fixture
def db_conn(temp_db_path):
    """Provide a configured SQLite connection to a temporary file database."""
    conn = create_connection(temp_db_path)
    apply_migrations(conn)
    yield conn
    conn.close()


def test_db_connection_and_wal_mode(temp_db_path):
    """TASK-0.3: Connection must enforce Foreign Keys and WAL mode on file databases."""
    conn = create_connection(temp_db_path)
    cursor = conn.cursor()
    
    # Check foreign keys
    cursor.execute("PRAGMA foreign_keys;")
    fk_enabled = cursor.fetchone()[0]
    assert fk_enabled == 1, "Foreign keys pragma must be enabled (1)."
    
    # Check WAL mode
    cursor.execute("PRAGMA journal_mode;")
    journal_mode = cursor.fetchone()[0].lower()
    assert journal_mode == "wal", f"Journal mode must be WAL, got {journal_mode}."
    
    cursor.close()
    conn.close()


def test_schema_migrations_runner(temp_db_path):
    """TASK-0.3: Migration engine applies Version 1 and records execution in schema_migrations."""
    conn = create_connection(temp_db_path)
    
    # First run applies migrations 1 through 9
    applied = apply_migrations(conn)
    assert 1 in applied
    assert 2 in applied
    assert 3 in applied
    assert 4 in applied
    assert 5 in applied
    assert 6 in applied
    assert 7 in applied
    assert 8 in applied
    assert 9 in applied
    assert get_applied_migrations(conn) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    
    # Second run is idempotent
    second_run = apply_migrations(conn)
    assert second_run == []
    
    cursor = conn.cursor()
    cursor.execute("SELECT version, description FROM schema_migrations WHERE version = 1;")
    row1 = cursor.fetchone()
    assert row1 is not None
    assert "users, audit_log" in row1["description"]

    cursor.execute("SELECT version, description FROM schema_migrations WHERE version = 2;")
    row2 = cursor.fetchone()
    assert row2 is not None
    assert "lockout" in row2["description"]

    cursor.execute("SELECT version, description FROM schema_migrations WHERE version = 3;")
    row3 = cursor.fetchone()
    assert row3 is not None
    assert "registries" in row3["description"]

    cursor.execute("SELECT version, description FROM schema_migrations WHERE version = 4;")
    row4 = cursor.fetchone()
    assert row4 is not None
    assert "Nuclides reference" in row4["description"]
    cursor.close()
    conn.close()


def test_audit_log_immutable_triggers(db_conn):
    """TASK-0.3: SQLite triggers must strictly block UPDATE and DELETE on audit_log."""
    # Insert initial record
    audit_id = record_audit_event(
        conn=db_conn,
        action="CREATE",
        user_id=None,
        entity_type="SYSTEM",
        entity_id=1,
        new_value={"test": "initial_data"}
    )
    assert audit_id > 0
    
    # 1. Attempt UPDATE -> must fail with trigger exception
    cursor = db_conn.cursor()
    with pytest.raises(sqlite3.DatabaseError) as exc_update:
        cursor.execute("UPDATE audit_log SET action = 'HACKED' WHERE id = ?;", (audit_id,))
    assert "Updates are strictly prohibited" in str(exc_update.value)
    
    # 2. Attempt DELETE -> must fail with trigger exception
    with pytest.raises(sqlite3.DatabaseError) as exc_delete:
        cursor.execute("DELETE FROM audit_log WHERE id = ?;", (audit_id,))
    assert "Deletions are strictly prohibited" in str(exc_delete.value)
    cursor.close()


def test_audit_log_cryptographic_hash_chaining(db_conn):
    """TASK-0.3: audit_log maintains a contiguous SHA-256 hash chain verified by verify_audit_chain."""
    id1 = record_audit_event(db_conn, action="CREATE", entity_type="TEST", entity_id=101)
    id2 = record_audit_event(db_conn, action="UPDATE", entity_type="TEST", entity_id=101)
    id3 = record_audit_event(db_conn, action="SUBMIT", entity_type="TEST", entity_id=101)
    
    cursor = db_conn.cursor()
    cursor.execute("SELECT id, prev_hash, row_hash FROM audit_log ORDER BY id ASC;")
    rows = cursor.fetchall()
    cursor.close()
    
    assert len(rows) >= 3
    # Check chaining sequence
    assert rows[0]["prev_hash"] == GENESIS_HASH
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]
    assert rows[2]["prev_hash"] == rows[1]["row_hash"]
    
    # Verify whole chain
    is_valid, corrupted = verify_audit_chain(db_conn)
    assert is_valid is True
    assert corrupted == []


def test_password_hashing_and_verification():
    """TASK-0.4: Argon2id (and PBKDF2) password hashing and secure verification."""
    password = "KashifSecureLab2026!"
    hashed = hash_password(password)
    
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword123", hashed) is False
    assert verify_password("", hashed) is False


def test_initial_admin_provisioning(db_conn):
    """TASK-0.4: Initial admin is provisioned with custom credentials and Master Recovery Key."""
    init_pass = "MyStrongAdminPassword2026!"
    admin_row, recovery_key = create_first_admin(db_conn, "admin", "مدير النظام الرئيسي", init_pass)
    assert admin_row is not None
    assert len(recovery_key) >= 32
    
    # Query admin user
    cursor = db_conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = 'admin';")
    admin = cursor.fetchone()
    assert admin is not None
    assert admin["role"] == "ADMIN"
    assert verify_password(init_pass, admin["password_hash"]) is True
    
    # Query recovery key hash in system_settings
    cursor.execute("SELECT value FROM system_settings WHERE key = 'master_recovery_key_hash';")
    rec_row = cursor.fetchone()
    assert rec_row is not None
    assert verify_password(recovery_key, rec_row["value"]) is True
    cursor.close()
    
    # Calling create_first_admin again fails because an admin already exists
    with pytest.raises(ValueError):
        create_first_admin(db_conn, "admin2", "مدير آخر", "AnotherPass123!")


def test_authentication_and_audit_events(db_conn):
    """TASK-0.4: Authentication flow records LOGIN and FAILED_LOGIN events in audit_log."""
    init_pass = "SecureAdminPass2026!"
    create_first_admin(db_conn, "admin", "مدير النظام", init_pass)
    
    # 1. Successful authentication
    user = authenticate_user(db_conn, "admin", init_pass)
    assert user["username"] == "admin"
    assert user["last_login_at"] is not None
    
    # Verify LOGIN audit event was logged
    cursor = db_conn.cursor()
    cursor.execute("SELECT * FROM audit_log WHERE action = 'LOGIN' ORDER BY id DESC LIMIT 1;")
    login_event = cursor.fetchone()
    assert login_event is not None
    assert login_event["user_id"] == user["id"]
    
    # 2. Failed authentication (wrong password)
    with pytest.raises(AuthenticationError):
        authenticate_user(db_conn, "admin", "IncorrectPassword")
        
    cursor.execute("SELECT * FROM audit_log WHERE action = 'FAILED_LOGIN' ORDER BY id DESC LIMIT 1;")
    failed_event = cursor.fetchone()
    assert failed_event is not None
    assert failed_event["user_id"] == user["id"]
    
    # 3. Failed authentication (non-existent username)
    with pytest.raises(AuthenticationError):
        authenticate_user(db_conn, "non_existent_user", "AnyPassword")
        
    cursor.execute("SELECT * FROM audit_log WHERE action = 'FAILED_LOGIN' ORDER BY id DESC LIMIT 1;")
    failed_unknown = cursor.fetchone()
    assert failed_unknown is not None
    assert failed_unknown["user_id"] is None
    cursor.close()


def test_master_recovery_key_reset(db_conn):
    """TASK-0.4: Master Recovery Key resets admin password and logs audit event."""
    init_pass = "InitialPass2026!"
    _, recovery_key = create_first_admin(db_conn, "admin", "مدير النظام", init_pass)
    new_admin_pass = "NewMasterPassword2026!"
    
    # 1. Invalid recovery key fails
    assert reset_admin_with_recovery_key(db_conn, "InvalidKey123", new_admin_pass) is False
    
    # 2. Valid recovery key succeeds
    assert reset_admin_with_recovery_key(db_conn, recovery_key, new_admin_pass) is True
    
    # Admin can now authenticate with new password
    user = authenticate_user(db_conn, "admin", new_admin_pass)
    assert user["username"] == "admin"
    assert user["must_change_password"] == 1
    
    # Verify audit event for recovery key usage
    cursor = db_conn.cursor()
    cursor.execute(
        "SELECT * FROM audit_log WHERE entity_type = 'SECURITY' AND action = 'UPDATE' ORDER BY id DESC LIMIT 1;"
    )
    sec_event = cursor.fetchone()
    assert sec_event is not None
    assert "ADMIN_PASSWORD_RESET_VIA_MASTER_KEY" in sec_event["new_value_json"]
    cursor.close()


def test_audit_log_tamper_detection_after_trigger_drop(db_conn):
    """TASK-0.3 / S2: verify_audit_chain detects tampered row if triggers are bypassed or dropped."""
    id1 = record_audit_event(db_conn, action="CREATE", entity_type="DETECTOR", entity_id=1)
    id2 = record_audit_event(db_conn, action="UPDATE", entity_type="DETECTOR", entity_id=1)
    id3 = record_audit_event(db_conn, action="APPROVE", entity_type="DETECTOR", entity_id=1)

    # Initial chain is intact
    valid_initial, corrupted_initial = verify_audit_chain(db_conn)
    assert valid_initial is True
    assert corrupted_initial == []

    # Drop trigger to simulate unauthorized direct DB file modification / tampering
    cursor = db_conn.cursor()
    cursor.execute("DROP TRIGGER prevent_audit_update;")
    cursor.execute(
        "UPDATE audit_log SET action = 'TAMPERED_ACTION' WHERE id = ?;", (id2,)
    )
    cursor.close()
    db_conn.commit()

    # verify_audit_chain must detect the tampered row and return False
    is_valid, corrupted = verify_audit_chain(db_conn)
    assert is_valid is False
    assert id2 in corrupted


def test_inactive_user_authentication_failure(db_conn):
    """TASK-0.4 / S2: Inactive user (is_active=0) fails authentication and logs FAILED_LOGIN."""
    cursor = db_conn.cursor()
    cursor.execute(
        """
        INSERT INTO users (username, password_hash, full_name, role, is_active, must_change_password, created_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'));
        """,
        ("disabled_user", hash_password("SecretPass123!"), "مستخدم معطل", "TECHNICIAN", 0, 0)
    )
    user_id = cursor.lastrowid
    cursor.close()
    db_conn.commit()

    # Attempt login with valid password but inactive account
    with pytest.raises(AuthenticationError) as exc_info:
        authenticate_user(db_conn, "disabled_user", "SecretPass123!")
    assert str(exc_info.value) == GENERIC_LOGIN_ERROR

    # Verify FAILED_LOGIN audit event recorded with user_id
    cursor = db_conn.cursor()
    cursor.execute(
        "SELECT * FROM audit_log WHERE action = 'FAILED_LOGIN' AND user_id = ? ORDER BY id DESC LIMIT 1;",
        (user_id,)
    )
    failed_event = cursor.fetchone()
    assert failed_event is not None
    assert "Account inactive" in failed_event["new_value_json"]
    cursor.close()


def test_pbkdf2_hashing_and_verification():
    """TASK-0.4 / S2: Explicit test of PBKDF2-HMAC-SHA256 hashing and verification."""
    password = "MySecurePbkdf2Password2026!"
    hashed = hash_password_pbkdf2(password, iterations=100000)

    assert hashed.startswith("pbkdf2:sha256:100000$")
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


def test_main_window_smoke_qt(qtbot, db_conn):
    """TASK-0.5 / S2: Smoke test for MainWindow using pytest-qt."""
    from kashif.gui.main_window import MainWindow

    window = MainWindow(db_conn=db_conn)
    qtbot.addWidget(window)
    window.show()

    assert "كاشف" in window.windowTitle()
    assert "KASHIF-QA" in window.windowTitle()
    assert window.lbl_db_status.text().startswith("🟢")
    assert "سليم" in window.lbl_audit_status.text()
    window._is_logging_out = True


def test_migration_004_data_preservation_and_nuclides_seeds(temp_db_path):
    """Migration 004 preserves existing source data and seeds reference nuclides correctly."""
    from kashif.db.migrations import (
        init_migrations_table,
        migration_001_initial_core_security,
        migration_002_user_lockout_and_session,
        migration_003_registries_detectors_instruments_sources,
    )
    conn = create_connection(temp_db_path)
    init_migrations_table(conn)

    # Apply 1, 2, 3 manually to simulate pre-migration database state
    migration_001_initial_core_security(conn)
    migration_002_user_lockout_and_session(conn)
    migration_003_registries_detectors_instruments_sources(conn)
    conn.execute("INSERT INTO schema_migrations (version, applied_at, description) VALUES (1, 't', '1'), (2, 't', '2'), (3, 't', '3');")
    conn.commit()

    # Insert an existing source in old single-nuclide schema
    conn.execute("""
        INSERT INTO sources (
            nuclide, source_code, serial_number, cert_activity_bq,
            cert_date, half_life_days, certificate_number, facility_name,
            is_active, status, notes, created_at, updated_at
        ) VALUES (
            'Co-60', 'SRC-LEGACY-01', 'SN-LEGACY-999', 37000.0,
            '2024-01-01', 1925.38, 'CERT-OLD-01', 'Lab Alpha',
            1, 'ACTIVE', 'Legacy source note', '2024-01-01T00:00:00', '2024-01-01T00:00:00'
        );
    """)
    conn.commit()

    # Now apply migrations (migration 4 runs)
    newly_applied = apply_migrations(conn)
    assert 4 in newly_applied

    # Verify source table has new structure and retained legacy record
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sources WHERE source_code = 'SRC-LEGACY-01';")
    s_row = cursor.fetchone()
    assert s_row is not None
    assert s_row["serial_number"] == "SN-LEGACY-999"
    assert s_row["facility_name"] == "Lab Alpha"
    assert "nuclide" not in s_row.keys()  # Column moved to source_isotopes

    # Verify isotope was migrated to source_isotopes
    cursor.execute("SELECT * FROM source_isotopes WHERE source_id = ?;", (s_row["id"],))
    iso_row = cursor.fetchone()
    assert iso_row is not None
    assert iso_row["nuclide_code"] == "Co-60"
    assert iso_row["cert_activity_bq"] == 37000.0
    assert iso_row["cert_date"] == "2024-01-01"

    # Verify reference nuclides seeds (Item 3 in CYCLE-2B-FIX-2)
    from kashif.core.decay import half_life_to_days
    import math

    cursor.execute("SELECT code, half_life_days, half_life_value, half_life_unit, confirmed, reference FROM nuclides;")
    nuclides = {r["code"]: r for r in cursor.fetchall()}
    assert len(nuclides) == 12

    # Verify half_life_days is strictly derived from (value * unit factor) for every single seeded nuclide
    for code, row in nuclides.items():
        expected_days = half_life_to_days(row["half_life_value"], row["half_life_unit"])
        assert math.isclose(row["half_life_days"], expected_days, rel_tol=1e-6), (
            f"Nuclide {code} half_life_days {row['half_life_days']} does not match derived {expected_days}"
        )
        # Verify reference has URL and evaluation year (or reference_url populated by migration 006)
        cursor.execute("SELECT reference_url FROM nuclides WHERE code = ?;", (code,))
        n_url = cursor.fetchone()[0]
        assert "http" in row["reference"] or "MRNI" in row["reference"] or "LNHB" in row["reference"] or (n_url and "http" in n_url)

    # Standard nuclides confirmed
    assert nuclides["Co-60"]["confirmed"] == 1
    assert nuclides["Cs-137"]["confirmed"] == 1

    # Verify Zn-65 updated by Migration 006 to 244.01 d per LNHB/DDEP
    cursor.execute("SELECT * FROM nuclides WHERE code = 'Zn-65';")
    zn_row = cursor.fetchone()
    assert zn_row["confirmed"] == 1
    assert zn_row["half_life_value"] == 244.01
    assert zn_row["half_life_unit"] == "d"
    assert zn_row["half_life_days"] == 244.01
    assert zn_row["half_life_uncertainty"] == 0.09
    assert "LNHB" in zn_row["reference"]
    assert "lnhb.fr" in zn_row["reference_url"]

    # All 12 standard gamma nuclides are confirmed following Migration 007
    assert nuclides["Am-241"]["confirmed"] == 1
    assert "LNHB" in nuclides["Am-241"]["reference"]
    assert nuclides["Ba-133"]["confirmed"] == 1
    assert nuclides["Ra-226"]["confirmed"] == 1

    cursor.close()
    conn.close()




def test_migration_005_hpge_specs_and_existing_data(temp_db_path):
    """TASK-1.9: Migration 005 adds HPGe semiconductor type and detector specs without data loss."""
    from kashif.db.migrations import (
        init_migrations_table,
        migration_001_initial_core_security,
        migration_002_user_lockout_and_session,
        migration_003_registries_detectors_instruments_sources,
        migration_004_multi_isotope_sources,
        migration_005_hpge_pn_and_detector_specs,
    )
    conn = create_connection(temp_db_path)
    init_migrations_table(conn)
    migration_001_initial_core_security(conn)
    migration_002_user_lockout_and_session(conn)
    migration_003_registries_detectors_instruments_sources(conn)
    migration_004_multi_isotope_sources(conn)

    # Insert existing detector in migration 004 schema (without semiconductor_type)
    conn.execute("""
        INSERT INTO detectors (
            code, detector_type, crystal_type, manufacturer, model,
            serial_number, nominal_voltage, voltage_polarity,
            status, created_at, updated_at
        ) VALUES (
            'HPGE-OLD-01', 'HPGE', 'COAXIAL', 'Canberra', 'GC2018',
            'SN-OLD-01', 3000.0, 'POSITIVE',
            'ACTIVE', '2024-01-01T00:00:00', '2024-01-01T00:00:00'
        );
    """)
    conn.commit()

    # Apply Migration 005
    migration_005_hpge_pn_and_detector_specs(conn)

    # Verify column existence and defaults on legacy record
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM detectors WHERE code = 'HPGE-OLD-01';")
    row = cursor.fetchone()
    assert row is not None
    assert row["semiconductor_type"] == "UNSPECIFIED"
    assert row["certified_polarity"] is None
    assert row["certificate_ref"] is None
    assert row["cooling_hours_cert"] is None
    assert row["hv_ramp_max_v_per_s"] == 10.0
    assert row["hv_ramp_reference"] is None

    # Test CHECK constraint: invalid semiconductor_type rejected
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO detectors (
                code, detector_type, crystal_type, semiconductor_type,
                manufacturer, model, serial_number, nominal_voltage,
                voltage_polarity, status, created_at, updated_at
            ) VALUES (
                'HPGE-BAD', 'HPGE', 'COAXIAL', 'INVALID_TYPE',
                'Ortec', 'GEM', 'SN-BAD', 2500.0,
                'POSITIVE', 'ACTIVE', '2024-01-01T00:00:00', '2024-01-01T00:00:00'
            );
        """)

    cursor.close()
    conn.close()


def test_migration_006_nuclides_uncertainty_and_zn65_ddep(temp_db_path):
    """TASK-1.9: Migration 006 updates Zn-65 to LNHB/DDEP and preserves custom nuclides."""
    from kashif.db.migrations import (
        init_migrations_table,
        migration_001_initial_core_security,
        migration_002_user_lockout_and_session,
        migration_003_registries_detectors_instruments_sources,
        migration_004_multi_isotope_sources,
        migration_005_hpge_pn_and_detector_specs,
        migration_006_nuclides_uncertainty_and_zn65_ddep,
    )
    conn = create_connection(temp_db_path)
    init_migrations_table(conn)
    migration_001_initial_core_security(conn)
    migration_002_user_lockout_and_session(conn)
    migration_003_registries_detectors_instruments_sources(conn)
    migration_004_multi_isotope_sources(conn)
    migration_005_hpge_pn_and_detector_specs(conn)

    # Insert a custom radionuclide (is_custom = 1)
    conn.execute("""
        INSERT INTO nuclides (
            code, name_ar, name_en, half_life_days, half_life_value,
            half_life_unit, confirmed, reference, is_custom, created_at, updated_at
        ) VALUES (
            'Custom-99', 'مخصص-99', 'Custom-99', 100.0, 100.0,
            'd', 0, 'Internal Lab Ref', 1, '2024-01-01T00:00:00', '2024-01-01T00:00:00'
        );
    """)
    conn.commit()

    # Apply Migration 006
    migration_006_nuclides_uncertainty_and_zn65_ddep(conn)

    cursor = conn.cursor()

    # 1. Custom nuclide was NOT touched
    cursor.execute("SELECT * FROM nuclides WHERE code = 'Custom-99';")
    custom_row = cursor.fetchone()
    assert custom_row is not None
    assert custom_row["half_life_days"] == 100.0
    assert custom_row["reference"] == "Internal Lab Ref"
    assert custom_row["is_custom"] == 1

    # 2. Zn-65 was corrected per LNHB/DDEP (Decision 15 / TASK-1.9)
    cursor.execute("SELECT * FROM nuclides WHERE code = 'Zn-65';")
    zn_row = cursor.fetchone()
    assert zn_row is not None
    assert zn_row["half_life_value"] == 244.01
    assert zn_row["half_life_days"] == 244.01
    assert zn_row["half_life_uncertainty"] == 0.09
    assert zn_row["confirmed"] == 1
    assert "LNHB" in zn_row["reference"]
    assert "http://www.lnhb.fr/nuclides/Zn-65_tables.pdf" in zn_row["reference_url"]
    assert zn_row["reference_accessed"] == "2026-10-27"

    cursor.close()
    conn.close()

def test_migration_007_lnhb_half_lives(temp_db_path):
    """TASK-1.10: Migration 007 updates 9 standard radionuclides with verified LNHB/DDEP data."""
    from kashif.db.connection import create_connection
    from kashif.services.audit_service import verify_audit_chain
    from kashif.db.migrations import (
        init_migrations_table,
        apply_migrations,
        migration_001_initial_core_security,
        migration_002_user_lockout_and_session,
        migration_003_registries_detectors_instruments_sources,
        migration_004_multi_isotope_sources,
        migration_005_hpge_pn_and_detector_specs,
        migration_006_nuclides_uncertainty_and_zn65_ddep,
        migration_007_lnhb_half_lives,
    )

    # --- PART A: Test fresh database (001 -> 007) via apply_migrations ---
    conn_fresh = create_connection(temp_db_path)
    applied = apply_migrations(conn_fresh)
    assert 7 in applied, "Migration 007 must be applied on fresh database"

    cursor_fresh = conn_fresh.cursor()

    # Manual calculations verification (1 year = 365.25 days per IAU/BIPM standard):
    # 1. Am-241: 432.6 * 365.25 = 158007.15 days
    # 2. Na-22:  2.6029 * 365.25 = 950.709225 days
    # 3. Co-57:  271.81 days (exact unit in days)
    cursor_fresh.execute("SELECT * FROM nuclides WHERE code = 'Am-241';")
    am = cursor_fresh.fetchone()
    assert am["half_life_value"] == 432.6
    assert am["half_life_unit"] == "y"
    assert math.isclose(am["half_life_days"], 158007.15, rel_tol=1e-7)  # 432.6 * 365.25
    assert am["half_life_uncertainty"] == 0.6
    assert am["confirmed"] == 1
    assert am["reference"] == "LNHB/DDEP - Table de Radionucléides (Am-241)"
    assert am["reference_url"] == "https://www.lnhb.fr/nuclides/Am-241_tables.pdf"
    assert am["reference_accessed"] == "2026-10-28"

    cursor_fresh.execute("SELECT * FROM nuclides WHERE code = 'Na-22';")
    na = cursor_fresh.fetchone()
    assert na["half_life_value"] == 2.6029
    assert na["half_life_unit"] == "y"
    assert math.isclose(na["half_life_days"], 950.709225, rel_tol=1e-7)  # 2.6029 * 365.25
    assert na["half_life_uncertainty"] == 0.0008
    assert na["confirmed"] == 1
    assert na["reference"] == "LNHB/DDEP - Table de Radionucléides (Na-22)"
    assert na["reference_url"] == "https://www.lnhb.fr/nuclides/Na-22_tables.pdf"

    cursor_fresh.execute("SELECT * FROM nuclides WHERE code = 'Co-57';")
    co57 = cursor_fresh.fetchone()
    assert co57["half_life_value"] == 271.81
    assert co57["half_life_unit"] == "d"
    assert co57["half_life_days"] == 271.81  # Exact days
    assert co57["half_life_uncertainty"] == 0.04
    assert co57["confirmed"] == 1
    assert co57["reference"] == "LNHB/DDEP - Table de Radionucléides (Co-57)"
    assert co57["reference_url"] == "https://www.lnhb.fr/nuclides/Co-57_tables.pdf"

    # Verify audit chain remains pristine
    valid_fresh, corrupted_fresh = verify_audit_chain(conn_fresh)
    assert valid_fresh is True and corrupted_fresh == []

    cursor_fresh.close()
    conn_fresh.close()

    # --- PART B: Test existing database migrated from 006 -> 007 ---
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_mig_path = tf.name

    try:
        conn_mig = create_connection(db_mig_path)
        init_migrations_table(conn_mig)
        migration_001_initial_core_security(conn_mig)
        migration_002_user_lockout_and_session(conn_mig)
        migration_003_registries_detectors_instruments_sources(conn_mig)
        migration_004_multi_isotope_sources(conn_mig)
        migration_005_hpge_pn_and_detector_specs(conn_mig)
        migration_006_nuclides_uncertainty_and_zn65_ddep(conn_mig)

        for v in range(1, 7):
            conn_mig.execute("INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, datetime('now'), 'migration');", (v,))
        conn_mig.commit()

        # Insert custom nuclide before 007
        conn_mig.execute("""
            INSERT INTO nuclides (
                code, name_ar, name_en, half_life_days, half_life_value,
                half_life_unit, half_life_uncertainty, confirmed, reference, is_custom, created_at, updated_at
            ) VALUES (
                'Custom-10', 'مخصص-10', 'Custom-10', 99.0, 99.0,
                'd', 0.01, 0, 'Lab Internal SOP-42', 1, '2025-01-01T00:00:00', '2025-01-01T00:00:00'
            );
        """)
        conn_mig.commit()

        # Apply Migration 007
        migration_007_lnhb_half_lives(conn_mig)
        conn_mig.execute("INSERT INTO schema_migrations (version, applied_at, description) VALUES (7, datetime('now'), 'migration');")
        conn_mig.commit()

        cur_mig = conn_mig.cursor()

        # 1. Custom nuclide was NOT touched
        cur_mig.execute("SELECT * FROM nuclides WHERE code = 'Custom-10';")
        custom_row = cur_mig.fetchone()
        assert custom_row is not None
        assert custom_row["half_life_days"] == 99.0
        assert custom_row["reference"] == "Lab Internal SOP-42"
        assert custom_row["is_custom"] == 1
        assert custom_row["confirmed"] == 0

        # 2. Co-60 and Cs-137 untouched
        cur_mig.execute("SELECT * FROM nuclides WHERE code = 'Co-60';")
        co60_row = cur_mig.fetchone()
        assert co60_row["half_life_value"] == 5.2714
        assert co60_row["half_life_unit"] == "y"
        assert "MRNI-507" in co60_row["reference"]

        cur_mig.execute("SELECT * FROM nuclides WHERE code = 'Cs-137';")
        cs137_row = cur_mig.fetchone()
        assert cs137_row["half_life_value"] == 30.08
        assert cs137_row["half_life_unit"] == "y"
        assert "MRNI-504" in cs137_row["reference"]

        # 3. Zn-65 untouched from 006
        cur_mig.execute("SELECT * FROM nuclides WHERE code = 'Zn-65';")
        zn_row = cur_mig.fetchone()
        assert zn_row["half_life_value"] == 244.01
        assert zn_row["half_life_uncertainty"] == 0.09
        assert zn_row["confirmed"] == 1

        # 4. Verify all other 6 LNHB nuclides
        expected_lnhb = {
            "Ba-133": (10.539, "y", 0.006, 10.539 * 365.25),
            "Cd-109": (461.9, "d", 0.4, 461.9),
            "Eu-152": (13.522, "y", 0.016, 13.522 * 365.25),
            "Mn-54":  (312.19, "d", 0.03, 312.19),
            "Y-88":   (106.63, "d", 0.05, 106.63),
            "Ra-226": (1600.0, "y", 7.0, 1600.0 * 365.25),
        }
        for code, (val, unit, uncert, expected_d) in expected_lnhb.items():
            cur_mig.execute("SELECT * FROM nuclides WHERE code = ?;", (code,))
            row = cur_mig.fetchone()
            assert row["half_life_value"] == val
            assert row["half_life_unit"] == unit
            assert math.isclose(row["half_life_days"], expected_d, rel_tol=1e-7)
            assert row["half_life_uncertainty"] == uncert
            assert row["confirmed"] == 1
            assert row["reference"] == f"LNHB/DDEP - Table de Radionucléides ({code})"
            assert row["reference_url"] == f"https://www.lnhb.fr/nuclides/{code}_tables.pdf"
            assert row["reference_accessed"] == "2026-10-28"

        # 5. Idempotent: re-running migration_007 does nothing
        migration_007_lnhb_half_lives(conn_mig)

        valid_mig, corrupted_mig = verify_audit_chain(conn_mig)
        assert valid_mig is True and corrupted_mig == []
        cur_mig.close()
        conn_mig.close()
    finally:
        if os.path.exists(db_mig_path):
            try:
                os.remove(db_mig_path)
            except OSError:
                pass




def test_migration_008_co60_cs137_lnhb_half_lives(temp_db_path):
    """TASK-1.14: Migration 008 updates Co-60 and Cs-137 with verified official LNHB/DDEP data."""
    from kashif.db.connection import create_connection
    from kashif.services.audit_service import verify_audit_chain
    from kashif.db.migrations import (
        init_migrations_table,
        apply_migrations,
        migration_001_initial_core_security,
        migration_002_user_lockout_and_session,
        migration_003_registries_detectors_instruments_sources,
        migration_004_multi_isotope_sources,
        migration_005_hpge_pn_and_detector_specs,
        migration_006_nuclides_uncertainty_and_zn65_ddep,
        migration_007_lnhb_half_lives,
        migration_008_co60_cs137_lnhb_half_lives,
    )

    # --- PART A: Test fresh database (001 -> 008) via apply_migrations ---
    conn_fresh = create_connection(temp_db_path)
    applied = apply_migrations(conn_fresh)
    assert 8 in applied, "Migration 008 must be applied on fresh database"

    cursor_fresh = conn_fresh.cursor()

    # Manual calculations verification (1 year = 365.25 days):
    # Co-60:  5.2711 * 365.25 = 1925.269275 days
    # Cs-137: 30.018 * 365.25 = 10964.0745 days
    cursor_fresh.execute("SELECT * FROM nuclides WHERE code = 'Co-60';")
    co60 = cursor_fresh.fetchone()
    assert co60["half_life_value"] == 5.2711
    assert co60["half_life_unit"] == "y"
    assert math.isclose(co60["half_life_days"], 1925.269275, rel_tol=1e-7)
    assert co60["half_life_uncertainty"] == 0.0008
    assert co60["confirmed"] == 1
    assert co60["reference"] == "LNHB/DDEP - Table de Radionucléides (Co-60)"
    assert co60["reference_url"] == "https://www.lnhb.fr/nuclides/Co-60_tables.pdf"

    cursor_fresh.execute("SELECT * FROM nuclides WHERE code = 'Cs-137';")
    cs137 = cursor_fresh.fetchone()
    assert cs137["half_life_value"] == 30.018
    assert cs137["half_life_unit"] == "y"
    assert math.isclose(cs137["half_life_days"], 10964.0745, rel_tol=1e-7)
    assert cs137["half_life_uncertainty"] == 0.022
    assert cs137["confirmed"] == 1
    assert cs137["reference"] == "LNHB/DDEP - Table de Radionucléides (Cs-137)"
    assert cs137["reference_url"] == "https://www.lnhb.fr/nuclides/Cs-137_tables.pdf"

    # Verify audit chain remains pristine
    valid_fresh, corrupted_fresh = verify_audit_chain(conn_fresh)
    assert valid_fresh is True and corrupted_fresh == []

    cursor_fresh.close()
    conn_fresh.close()

    # --- PART B: Test existing database migrated from 007 -> 008 ---
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_mig_path = tf.name

    try:
        conn_mig = create_connection(db_mig_path)
        init_migrations_table(conn_mig)
        migration_001_initial_core_security(conn_mig)
        migration_002_user_lockout_and_session(conn_mig)
        migration_003_registries_detectors_instruments_sources(conn_mig)
        migration_004_multi_isotope_sources(conn_mig)
        migration_005_hpge_pn_and_detector_specs(conn_mig)
        migration_006_nuclides_uncertainty_and_zn65_ddep(conn_mig)
        migration_007_lnhb_half_lives(conn_mig)

        for v in range(1, 8):
            conn_mig.execute("INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, datetime('now'), 'migration');", (v,))
        conn_mig.commit()

        # Insert custom nuclide before 008
        conn_mig.execute("""
            INSERT INTO nuclides (
                code, name_ar, name_en, half_life_days, half_life_value,
                half_life_unit, half_life_uncertainty, confirmed, reference, is_custom, created_at, updated_at
            ) VALUES (
                'Custom-99', 'مخصص-99', 'Custom-99', 88.0, 88.0,
                'd', 0.05, 0, 'Lab Internal SOP-99', 1, '2025-01-01T00:00:00', '2025-01-01T00:00:00'
            );
        """)
        conn_mig.commit()

        # Apply Migration 008 via apply_migrations
        newly_applied = apply_migrations(conn_mig)
        assert newly_applied == [8, 9]

        cur_mig = conn_mig.cursor()

        # 1. Custom nuclide was NOT touched
        cur_mig.execute("SELECT * FROM nuclides WHERE code = 'Custom-99';")
        custom_row = cur_mig.fetchone()
        assert custom_row is not None
        assert custom_row["half_life_days"] == 88.0
        assert custom_row["reference"] == "Lab Internal SOP-99"
        assert custom_row["is_custom"] == 1
        assert custom_row["confirmed"] == 0

        # 2. Co-60 and Cs-137 updated correctly
        cur_mig.execute("SELECT * FROM nuclides WHERE code = 'Co-60';")
        co60_row = cur_mig.fetchone()
        assert co60_row["half_life_value"] == 5.2711
        assert co60_row["half_life_unit"] == "y"
        assert math.isclose(co60_row["half_life_days"], 1925.269275, rel_tol=1e-7)
        assert co60_row["half_life_uncertainty"] == 0.0008
        assert co60_row["confirmed"] == 1
        assert co60_row["reference"] == "LNHB/DDEP - Table de Radionucléides (Co-60)"
        assert co60_row["reference_url"] == "https://www.lnhb.fr/nuclides/Co-60_tables.pdf"

        cur_mig.execute("SELECT * FROM nuclides WHERE code = 'Cs-137';")
        cs137_row = cur_mig.fetchone()
        assert cs137_row["half_life_value"] == 30.018
        assert cs137_row["half_life_unit"] == "y"
        assert math.isclose(cs137_row["half_life_days"], 10964.0745, rel_tol=1e-7)
        assert cs137_row["half_life_uncertainty"] == 0.022
        assert cs137_row["confirmed"] == 1
        assert cs137_row["reference"] == "LNHB/DDEP - Table de Radionucléides (Cs-137)"
        assert cs137_row["reference_url"] == "https://www.lnhb.fr/nuclides/Cs-137_tables.pdf"

        # 3. Other radionuclides were NOT touched
        other_nuclides = {
            "Am-241": (432.6, 0.6),
            "Ba-133": (10.539, 0.006),
            "Cd-109": (461.9, 0.4),
            "Co-57":  (271.81, 0.04),
            "Eu-152": (13.522, 0.016),
            "Mn-54":  (312.19, 0.03),
            "Na-22":  (2.6029, 0.0008),
            "Y-88":   (106.63, 0.05),
            "Ra-226": (1600.0, 7.0),
            "Zn-65":  (244.01, 0.09),
        }
        for code, (expected_val, expected_uncert) in other_nuclides.items():
            cur_mig.execute("SELECT half_life_value, half_life_uncertainty FROM nuclides WHERE code = ?;", (code,))
            row = cur_mig.fetchone()
            assert row["half_life_value"] == expected_val, f"Nuclide {code} value changed"
            assert row["half_life_uncertainty"] == expected_uncert, f"Nuclide {code} uncertainty changed"

        # 4. Idempotent: re-running does nothing and returns empty list
        re_applied = apply_migrations(conn_mig)
        assert re_applied == []

        # Calling function directly is also safe and idempotent
        migration_008_co60_cs137_lnhb_half_lives(conn_mig)

        cur_mig.execute("SELECT half_life_value FROM nuclides WHERE code = 'Co-60';")
        assert cur_mig.fetchone()["half_life_value"] == 5.2711

        valid_mig, corrupted_mig = verify_audit_chain(conn_mig)
        assert valid_mig is True and corrupted_mig == []
        cur_mig.close()
        conn_mig.close()
    finally:
        if os.path.exists(db_mig_path):
            try:
                os.remove(db_mig_path)
            except OSError:
                pass

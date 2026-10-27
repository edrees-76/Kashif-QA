"""Integration and unit tests for KASHIF-QA Phase 0.

Tests SQLite WAL connectivity, schema migrations, audit log cryptographic
hash chaining, tamper-evident trigger enforcement, Argon2id security,
and administrator provisioning.
"""

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
    
    # First run applies migrations 1, 2, 3, and 4
    applied = apply_migrations(conn)
    assert 1 in applied
    assert 2 in applied
    assert 3 in applied
    assert 4 in applied
    assert get_applied_migrations(conn) == [1, 2, 3, 4]
    
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
        # Verify reference has URL and evaluation year
        assert "http" in row["reference"] or "MRNI" in row["reference"]

    # Standard nuclides confirmed
    assert nuclides["Co-60"]["confirmed"] == 1
    assert nuclides["Cs-137"]["confirmed"] == 1

    # Verify Zn-65 updated to 243.66 d per NuDat 3.0 / ENSDF
    assert nuclides["Zn-65"]["confirmed"] == 0
    assert nuclides["Zn-65"]["half_life_value"] == 243.66
    assert nuclides["Zn-65"]["half_life_unit"] == "d"
    assert nuclides["Zn-65"]["half_life_days"] == 243.66
    assert "NuDat" in nuclides["Zn-65"]["reference"]

    # Other gamma nuclides unconfirmed with detailed published references
    assert nuclides["Am-241"]["confirmed"] == 0
    assert "NuDat" in nuclides["Am-241"]["reference"]
    assert nuclides["Ba-133"]["confirmed"] == 0
    assert nuclides["Ra-226"]["confirmed"] == 0

    cursor.close()
    conn.close()



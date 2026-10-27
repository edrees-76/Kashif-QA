"""Database schema migration engine for KASHIF-QA.

Tracks applied migrations in schema_migrations table and executes new migrations sequentially.
"""

from datetime import datetime, timezone
import logging
import sqlite3
from typing import Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)


def migration_001_initial_core_security(conn: sqlite3.Connection) -> None:
    """Migration 001: Foundation schema - users, audit_log, triggers, system_settings."""
    cursor = conn.cursor()
    
    # 1. users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('TECHNICIAN', 'LAB_MANAGER', 'ADMIN')),
        is_active INTEGER NOT NULL DEFAULT 1,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        last_login_at TEXT,
        created_at TEXT NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);")

    # 2. audit_log table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        user_id INTEGER,
        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id INTEGER,
        old_value_json TEXT,
        new_value_json TEXT,
        prev_hash TEXT NOT NULL,
        row_hash TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_log(user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);")

    # 3. Triggers preventing UPDATE and DELETE on audit_log
    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS prevent_audit_update
    BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(FAIL, 'Updates are strictly prohibited on audit_log table.');
    END;
    """)

    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS prevent_audit_delete
    BEFORE DELETE ON audit_log
    BEGIN
        SELECT RAISE(FAIL, 'Deletions are strictly prohibited on audit_log table.');
    END;
    """)

    # 4. system_settings table (for recovery key hash, system metadata)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    cursor.close()


def migration_002_user_lockout_and_session(conn: sqlite3.Connection) -> None:
    """Migration 002: Add failed_attempts and locked_until columns to users table for lockout enforcement."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(users);")
    cols = {row["name"] for row in cursor.fetchall()}
    if "failed_attempts" not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0;")
    if "locked_until" not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN locked_until TEXT;")
    cursor.close()


# Registry of migrations: version -> (description, migration_function)
MIGRATIONS: Dict[int, Tuple[str, Callable[[sqlite3.Connection], None]]] = {
    1: ("Initial schema: users, audit_log with hash-chaining triggers, system_settings", migration_001_initial_core_security),
    2: ("Add account lockout and session tracking fields to users table", migration_002_user_lockout_and_session),
}


def init_migrations_table(conn: sqlite3.Connection) -> None:
    """Ensure schema_migrations table exists."""
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        description TEXT NOT NULL
    );
    """)
    cursor.close()
    conn.commit()


def get_applied_migrations(conn: sqlite3.Connection) -> List[int]:
    """Return a sorted list of applied migration versions."""
    init_migrations_table(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
    rows = cursor.fetchall()
    cursor.close()
    return [row["version"] for row in rows]


def apply_migrations(conn: sqlite3.Connection) -> List[int]:
    """Apply all unapplied migrations in ascending order.
    
    Returns the list of newly applied migration versions.
    """
    init_migrations_table(conn)
    applied = set(get_applied_migrations(conn))
    newly_applied = []
    
    for version in sorted(MIGRATIONS.keys()):
        if version not in applied:
            description, migrate_fn = MIGRATIONS[version]
            logger.info("Applying migration %03d: %s", version, description)
            
            # Execute migration in transaction
            try:
                migrate_fn(conn)
                now_str = datetime.now(timezone.utc).isoformat()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?);",
                    (version, now_str, description),
                )
                cursor.close()
                conn.commit()
                newly_applied.append(version)
            except Exception as e:
                conn.rollback()
                logger.error("Migration %03d failed: %s", version, e)
                raise
                
    return newly_applied

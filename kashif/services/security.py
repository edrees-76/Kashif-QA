"""Authentication, password hashing, user management, and security services for KASHIF-QA.

Implements Argon2id password hashing (with PBKDF2 fallback),
persistent account lockout after 5 consecutive failures,
and Master Recovery Key management.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import logging
import math
import os
import re
import secrets
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from kashif.services.audit_service import record_audit_event
from kashif.services.session import ROLES

logger = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 10
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 5

# Try Argon2id from argon2-cffi
try:
    from argon2 import PasswordHasher, Type
    from argon2.exceptions import VerifyMismatchError
    _ARGON2_AVAILABLE = True
    _hasher = PasswordHasher(
        time_cost=2,
        memory_cost=65536,
        parallelism=1,
        hash_len=32,
        type=Type.ID
    )
except ImportError:
    _ARGON2_AVAILABLE = False
    _hasher = None


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    pass


class SecurityError(Exception):
    """Raised for authorization or security violation errors."""
    pass


def hash_password_pbkdf2(password: str, iterations: int = 600000) -> str:
    """Hash password explicitly using PBKDF2-HMAC-SHA256 with high iteration count."""
    if not password:
        raise ValueError("Password cannot be empty.")
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2:sha256:{iterations}${salt}${derived.hex()}"


def hash_password_argon2(password: str) -> str:
    """Hash password explicitly using Argon2id."""
    if not password:
        raise ValueError("Password cannot be empty.")
    if _ARGON2_AVAILABLE and _hasher is not None:
        return _hasher.hash(password)
    raise RuntimeError("Argon2id is not available.")


def hash_password(password: str, method: str = "argon2") -> str:
    """Hash a plaintext password using Argon2id (default) or PBKDF2 fallback."""
    if method == "pbkdf2" or not _ARGON2_AVAILABLE or _hasher is None:
        return hash_password_pbkdf2(password)
    return hash_password_argon2(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against an Argon2id or PBKDF2 hash."""
    if not password or not password_hash:
        return False
        
    if password_hash.startswith("$argon2"):
        if not _ARGON2_AVAILABLE or _hasher is None:
            try:
                from argon2 import PasswordHasher
                return PasswordHasher().verify(password_hash, password)
            except Exception:
                logger.error("Argon2 hash encountered but argon2-cffi is not available.")
                return False
        try:
            return _hasher.verify(password_hash, password)
        except VerifyMismatchError:
            return False
        except Exception as e:
            logger.error("Argon2 verification error: %s", e)
            return False
            
    if password_hash.startswith("pbkdf2:sha256:"):
        try:
            parts = password_hash.split("$")
            meta = parts[0].split(":")
            iterations = int(meta[2])
            salt = parts[1]
            stored_digest = parts[2]
            computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
            return hmac.compare_digest(stored_digest, computed)
        except Exception as e:
            logger.error("PBKDF2 verification error: %s", e)
            return False
            
    return False


def validate_password_strength(password: str) -> None:
    """Validate password against minimum security constraints."""
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"يجب ألا يقل طول كلمة المرور عن {MIN_PASSWORD_LENGTH} أحرف.")


def create_first_admin(
    conn: sqlite3.Connection,
    username: str,
    full_name: str,
    password: str
) -> Tuple[sqlite3.Row, str]:
    """Create the initial administrator account with a user-chosen password.
    
    Generates and returns the Master Recovery Key (shown once).
    Never writes plaintext credentials to files or logs.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM users;")
    row = cursor.fetchone()
    if row and row["count"] > 0:
        cursor.close()
        raise ValueError("تم إعداد مدير النظام بالفعل مسبقاً.")

    clean_user = username.strip()
    clean_name = full_name.strip()
    if not clean_user:
        cursor.close()
        raise ValueError("اسم المستخدم لا يمكن أن يكون فارغاً.")
    if not clean_name:
        cursor.close()
        raise ValueError("الاسم الكامل لا يمكن أن يكون فارغاً.")

    validate_password_strength(password)

    password_hash = hash_password(password)
    recovery_key = secrets.token_hex(32)
    recovery_key_hash = hash_password(recovery_key)
    now_str = datetime.now(timezone.utc).isoformat()

    cursor.execute(
        """
        INSERT INTO users (
            username, password_hash, full_name, role, is_active,
            must_change_password, failed_attempts, locked_until, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            clean_user,
            password_hash,
            clean_name,
            "ADMIN",
            1,
            0,  # Admin chose their own password, so must_change_password = 0
            0,
            None,
            now_str,
        ),
    )
    admin_id = cursor.lastrowid

    cursor.execute(
        """
        INSERT OR REPLACE INTO system_settings (key, value, updated_at)
        VALUES (?, ?, ?);
        """,
        ("master_recovery_key_hash", recovery_key_hash, now_str),
    )
    cursor.close()

    record_audit_event(
        conn=conn,
        action="CREATE",
        user_id=admin_id,
        entity_type="USER",
        entity_id=admin_id,
        new_value={"username": clean_user, "role": "ADMIN", "note": "Initial administrator setup"},
        commit=True,
    )

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?;", (admin_id,))
    admin_row = cursor.fetchone()
    cursor.close()

    return admin_row, recovery_key


# Standard unified generic error message to prevent username enumeration & status leaks
GENERIC_LOGIN_ERROR = "اسم المستخدم أو كلمة المرور غير صحيحة أو الحساب غير متاح."


def authenticate_user(
    conn: sqlite3.Connection,
    username: str,
    password: str
) -> sqlite3.Row:
    """Authenticate user with database-persisted lockout after 5 consecutive failures.
    
    Uses unified generic error message for all failure conditions (user not found,
    locked account, inactive account, bad password) to prevent enumeration or timing attacks.
    Specific failure reasons remain strictly in audit_log.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?;", (username,))
    user = cursor.fetchone()
    cursor.close()

    now = datetime.now(timezone.utc)

    # 1. Non-existent username
    if user is None:
        record_audit_event(
            conn=conn,
            action="FAILED_LOGIN",
            user_id=None,
            entity_type="USER",
            new_value={"attempted_username": username, "reason": "User not found"},
            commit=True,
        )
        raise AuthenticationError(GENERIC_LOGIN_ERROR)

    user_dict = dict(user)

    # 2. Check if account is currently locked
    locked_until_str = user_dict.get("locked_until")
    if locked_until_str:
        try:
            locked_dt = datetime.fromisoformat(locked_until_str)
            if locked_dt > now:
                remaining_sec = (locked_dt - now).total_seconds()
                remaining_min = math.ceil(remaining_sec / 60)
                record_audit_event(
                    conn=conn,
                    action="FAILED_LOGIN",
                    user_id=user["id"],
                    entity_type="USER",
                    entity_id=user["id"],
                    new_value={"reason": "Attempt during account lockout", "remaining_minutes": remaining_min},
                    commit=True,
                )
                raise AuthenticationError(GENERIC_LOGIN_ERROR)
            else:
                # Lockout period expired; reset counter
                conn.execute(
                    "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?;",
                    (user["id"],)
                )
                conn.commit()
                user_dict["failed_attempts"] = 0
                user_dict["locked_until"] = None
        except (ValueError, TypeError):
            pass

    # 3. Check if account is deactivated
    if not user_dict.get("is_active", 1):
        record_audit_event(
            conn=conn,
            action="FAILED_LOGIN",
            user_id=user["id"],
            entity_type="USER",
            entity_id=user["id"],
            new_value={"attempted_username": username, "reason": "Account inactive"},
            commit=True,
        )
        raise AuthenticationError(GENERIC_LOGIN_ERROR)

    # 4. Verify password
    if not verify_password(password, user["password_hash"]):
        failed_count = (user_dict.get("failed_attempts") or 0) + 1
        
        if failed_count >= MAX_FAILED_ATTEMPTS:
            locked_time = now + timedelta(minutes=LOCKOUT_MINUTES)
            conn.execute(
                "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?;",
                (failed_count, locked_time.isoformat(), user["id"])
            )
            record_audit_event(
                conn=conn,
                action="ACCOUNT_LOCKED",
                user_id=user["id"],
                entity_type="USER",
                entity_id=user["id"],
                new_value={
                    "event": "ACCOUNT_LOCKED",
                    "failed_attempts": failed_count,
                    "lockout_minutes": LOCKOUT_MINUTES,
                },
                commit=True,
            )
            raise AuthenticationError(GENERIC_LOGIN_ERROR)
        else:
            conn.execute(
                "UPDATE users SET failed_attempts = ? WHERE id = ?;",
                (failed_count, user["id"])
            )
            record_audit_event(
                conn=conn,
                action="FAILED_LOGIN",
                user_id=user["id"],
                entity_type="USER",
                entity_id=user["id"],
                new_value={"attempt": failed_count, "reason": "Password mismatch"},
                commit=True,
            )
            raise AuthenticationError(GENERIC_LOGIN_ERROR)

    # 5. Successful login: reset failed counters and update last_login_at
    conn.execute(
        "UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login_at = ? WHERE id = ?;",
        (now.isoformat(), user["id"])
    )
    record_audit_event(
        conn=conn,
        action="LOGIN",
        user_id=user["id"],
        entity_type="USER",
        entity_id=user["id"],
        new_value={"username": username, "role": user["role"]},
        commit=True,
    )

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?;", (user["id"],))
    updated_user = cursor.fetchone()
    cursor.close()
    return updated_user


def reset_admin_with_recovery_key(
    conn: sqlite3.Connection,
    recovery_key: str,
    new_admin_password: str,
    admin_username: Optional[str] = None,
) -> bool:
    """Reset an administrator's password using the Master Recovery Key.
    
    Targets any user account with role='ADMIN' (or specific admin_username).
    Returns True only if an admin row was actually updated in the database.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM system_settings WHERE key = 'master_recovery_key_hash';")
    row = cursor.fetchone()
    cursor.close()

    if not row or not verify_password(recovery_key, row["value"]):
        record_audit_event(
            conn=conn,
            action="FAILED_LOGIN",
            entity_type="SECURITY",
            new_value={"event": "INVALID_MASTER_RECOVERY_KEY_ATTEMPT"},
            commit=True,
        )
        return False

    validate_password_strength(new_admin_password)
    new_hash = hash_password(new_admin_password)

    cursor = conn.cursor()
    if admin_username:
        cursor.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1, failed_attempts = 0, locked_until = NULL WHERE username = ? AND role = 'ADMIN';",
            (new_hash, admin_username)
        )
    else:
        cursor.execute("SELECT id, username FROM users WHERE role = 'ADMIN' ORDER BY id ASC LIMIT 1;")
        admin_row = cursor.fetchone()
        if not admin_row:
            cursor.close()
            return False
        cursor.execute(
            "UPDATE users SET password_hash = ?, must_change_password = 1, failed_attempts = 0, locked_until = NULL WHERE id = ?;",
            (new_hash, admin_row["id"])
        )

    updated = cursor.rowcount > 0
    cursor.close()

    if not updated:
        return False

    record_audit_event(
        conn=conn,
        action="UPDATE",
        entity_type="SECURITY",
        new_value={"event": "ADMIN_PASSWORD_RESET_VIA_MASTER_KEY", "target_admin": admin_username or "FIRST_ADMIN"},
        commit=True,
    )
    return True



# ----------------------------------------------------------------------
# Administrative User Management (Restricted strictly to ADMIN role)
# ----------------------------------------------------------------------

def _require_admin(conn: sqlite3.Connection, operator_user_id: Optional[int]) -> None:
    """Verify that operator_user_id exists, is active, and possesses the ADMIN role.
    
    Raises:
        PermissionError: If operator does not possess the ADMIN role or is inactive.
    """
    if operator_user_id is None:
        raise PermissionError("مطلوب معرف مسؤول صالح (ADMIN role required).")
    cursor = conn.cursor()
    cursor.execute("SELECT role, is_active FROM users WHERE id = ?;", (operator_user_id,))
    row = cursor.fetchone()
    cursor.close()
    if not row or not row["is_active"] or row["role"] != "ADMIN":
        raise PermissionError("غير مصرح: هذه العملية تتطلب صلاحية مدير النظام (ADMIN).")


def list_users(conn: sqlite3.Connection, operator_user_id: Optional[int] = None) -> List[sqlite3.Row]:
    """Return all users in the system. Requires ADMIN role."""
    _require_admin(conn, operator_user_id)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, username, full_name, role, is_active, must_change_password,
               failed_attempts, locked_until, last_login_at, created_at
        FROM users
        ORDER BY id ASC;
        """
    )
    rows = cursor.fetchall()
    cursor.close()
    return rows


def list_active_usernames(conn: sqlite3.Connection) -> List[Tuple[str, str]]:
    """Return list of (username, full_name) for active users."""
    cursor = conn.cursor()
    cursor.execute("SELECT username, full_name FROM users WHERE is_active = 1 ORDER BY username ASC;")
    rows = cursor.fetchall()
    cursor.close()
    return [(r["username"], r["full_name"]) for r in rows]


def create_user(
    conn: sqlite3.Connection,
    operator_user_id: int,
    username: str,
    full_name: str,
    role: str,
    password: str,
    must_change_password: bool = True,
) -> int:
    """Create a new user. Strictly restricted to ADMIN role and logged in audit_log."""
    _require_admin(conn, operator_user_id)
    if role not in ROLES:
        raise ValueError(f"الدور غير صالح: {role}")
    clean_user = username.strip()
    clean_name = full_name.strip()
    if not clean_user:
        raise ValueError("اسم المستخدم لا يمكن أن يكون فارغاً.")
    if not clean_name:
        raise ValueError("الاسم الكامل لا يمكن أن يكون فارغاً.")

    validate_password_strength(password)

    # Check username uniqueness
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?;", (clean_user,))
    if cursor.fetchone():
        cursor.close()
        raise ValueError(f"اسم المستخدم '{clean_user}' مسجل مسبقاً.")

    pw_hash = hash_password(password)
    now_str = datetime.now(timezone.utc).isoformat()

    cursor.execute(
        """
        INSERT INTO users (
            username, password_hash, full_name, role, is_active,
            must_change_password, failed_attempts, locked_until, created_at
        ) VALUES (?, ?, ?, ?, 1, ?, 0, NULL, ?);
        """,
        (clean_user, pw_hash, clean_name, role, 1 if must_change_password else 0, now_str),
    )
    new_user_id = cursor.lastrowid
    cursor.close()

    record_audit_event(
        conn=conn,
        action="CREATE",
        user_id=operator_user_id,
        entity_type="USER",
        entity_id=new_user_id,
        new_value={"username": clean_user, "role": role, "full_name": clean_name},
        commit=True,
    )
    return new_user_id


def set_user_active_status(
    conn: sqlite3.Connection,
    operator_user_id: int,
    target_user_id: int,
    is_active: bool,
) -> None:
    """Activate or deactivate a user account. Requires ADMIN role.
    
    Prevents self-deactivation and deactivating the last active administrator.
    """
    _require_admin(conn, operator_user_id)
    if operator_user_id == target_user_id and not is_active:
        raise SecurityError("لا يمكنك تعطيل حسابك الشخصي النشط.")

    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?;", (target_user_id,))
    target_user = cursor.fetchone()
    if not target_user:
        cursor.close()
        raise ValueError("المستخدم غير موجود.")

    # Guard: prevent deactivating the last active admin
    if target_user["role"] == "ADMIN" and not is_active:
        cursor.execute("SELECT COUNT(*) as count FROM users WHERE role = 'ADMIN' AND is_active = 1;")
        admin_count = cursor.fetchone()["count"]
        if admin_count <= 1:
            cursor.close()
            raise SecurityError("لا يمكن تعطيل المسؤول الوحيد النشط في المنظومة.")

    cursor.execute("UPDATE users SET is_active = ? WHERE id = ?;", (1 if is_active else 0, target_user_id))
    cursor.close()

    record_audit_event(
        conn=conn,
        action="UPDATE",
        user_id=operator_user_id,
        entity_type="USER",
        entity_id=target_user_id,
        new_value={"is_active": 1 if is_active else 0},
        old_value={"is_active": target_user["is_active"]},
        commit=True,
    )


def admin_reset_user_password(
    conn: sqlite3.Connection,
    operator_user_id: int,
    target_user_id: int,
    new_password: str,
) -> None:
    """Reset a user's password by administrator. Requires ADMIN role."""
    _require_admin(conn, operator_user_id)
    validate_password_strength(new_password)
    new_hash = hash_password(new_password)

    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE id = ?;", (target_user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.close()

        raise ValueError("المستخدم غير موجود.")

    cursor.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1, failed_attempts = 0, locked_until = NULL WHERE id = ?;",
        (new_hash, target_user_id),
    )
    cursor.close()

    record_audit_event(
        conn=conn,
        action="UPDATE",
        user_id=operator_user_id,
        entity_type="USER",
        entity_id=target_user_id,
        new_value={"event": "ADMIN_PASSWORD_RESET", "username": user["username"]},
        commit=True,
    )


def change_own_password(
    conn: sqlite3.Connection,
    user_id: int,
    old_password: str,
    new_password: str,
) -> None:
    """Change user password, verifying old password and clearing must_change_password."""
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash, username FROM users WHERE id = ?;", (user_id,))
    user = cursor.fetchone()
    cursor.close()

    if not user or not verify_password(old_password, user["password_hash"]):
        raise AuthenticationError("كلمة المرور الحالية غير صحيحة.")

    validate_password_strength(new_password)
    new_hash = hash_password(new_password)

    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 0, failed_attempts = 0, locked_until = NULL WHERE id = ?;",
        (new_hash, user_id),
    )
    cursor.close()

    record_audit_event(
        conn=conn,
        action="UPDATE",
        user_id=user_id,
        entity_type="USER",
        entity_id=user_id,
        new_value={"event": "PASSWORD_CHANGED", "username": user["username"]},
        commit=True,
    )

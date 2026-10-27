"""Authentication, password hashing, and security services for KASHIF-QA.

Implements Argon2id password hashing (with PBKDF2 fallback),
immutable audit event logging, and Master Recovery Key management.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import logging
import os
import secrets
import sqlite3
from typing import Any, Dict, Optional, Tuple

from kashif.services.audit_service import record_audit_event

logger = logging.getLogger(__name__)

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
            # Re-initialize basic hasher if available
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


def ensure_initial_admin(conn: sqlite3.Connection) -> Optional[Tuple[str, str]]:
    """Provision the initial admin user and Master Recovery Key if no users exist.
    
    Returns (initial_admin_password, master_recovery_key) if provisioned, or None.
    NEVER hardcodes passwords in code or Git.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM users;")
    row = cursor.fetchone()
    
    if row and row["count"] > 0:
        cursor.close()
        return None
        
    # Generate secure random credentials
    initial_password = os.getenv("KASHIF_INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    recovery_key = secrets.token_hex(32)
    
    password_hash = hash_password(initial_password)
    recovery_key_hash = hash_password(recovery_key)
    now_str = datetime.now(timezone.utc).isoformat()
    
    # 1. Insert admin user
    cursor.execute(
        """
        INSERT INTO users (
            username, password_hash, full_name, role, is_active,
            must_change_password, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (
            "admin",
            password_hash,
            "مدير النظام الافتراضي",
            "ADMIN",
            1,
            1,  # must_change_password = TRUE
            now_str,
        ),
    )
    admin_id = cursor.lastrowid
    
    # 2. Store Master Recovery Key hash in system_settings
    cursor.execute(
        """
        INSERT OR REPLACE INTO system_settings (key, value, updated_at)
        VALUES (?, ?, ?);
        """,
        ("master_recovery_key_hash", recovery_key_hash, now_str),
    )
    cursor.close()
    
    # 3. Log audit event
    record_audit_event(
        conn=conn,
        action="CREATE",
        user_id=admin_id,
        entity_type="USER",
        entity_id=admin_id,
        new_value={"username": "admin", "role": "ADMIN", "note": "Initial admin provisioning"},
        commit=True,
    )
    
    return initial_password, recovery_key


def authenticate_user(
    conn: sqlite3.Connection,
    username: str,
    password: str
) -> sqlite3.Row:
    """Authenticate a user by username and password.
    
    Logs LOGIN on success, FAILED_LOGIN on failure into audit_log.
    """
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE username = ?;", (username,))
    user = cursor.fetchone()
    cursor.close()
    
    if user is None:
        record_audit_event(
            conn=conn,
            action="FAILED_LOGIN",
            user_id=None,
            entity_type="USER",
            new_value={"attempted_username": username, "reason": "User not found"},
            commit=True,
        )
        raise AuthenticationError("اسم المستخدم أو كلمة المرور غير صحيحة.")
        
    if not user["is_active"]:
        record_audit_event(
            conn=conn,
            action="FAILED_LOGIN",
            user_id=user["id"],
            entity_type="USER",
            entity_id=user["id"],
            new_value={"attempted_username": username, "reason": "Account inactive"},
            commit=True,
        )
        raise AuthenticationError("هذا الحساب معطّل. يرجى مراجعة إدارة النظام.")
        
    if not verify_password(password, user["password_hash"]):
        record_audit_event(
            conn=conn,
            action="FAILED_LOGIN",
            user_id=user["id"],
            entity_type="USER",
            entity_id=user["id"],
            new_value={"attempted_username": username, "reason": "Invalid password"},
            commit=True,
        )
        raise AuthenticationError("اسم المستخدم أو كلمة المرور غير صحيحة.")
        
    # Successful login: update last_login_at
    now_str = datetime.now(timezone.utc).isoformat()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET last_login_at = ? WHERE id = ?;", (now_str, user["id"]))
    cursor.close()
    
    record_audit_event(
        conn=conn,
        action="LOGIN",
        user_id=user["id"],
        entity_type="USER",
        entity_id=user["id"],
        new_value={"username": username, "role": user["role"]},
        commit=True,
    )
    
    # Return fresh user record
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?;", (user["id"],))
    updated_user = cursor.fetchone()
    cursor.close()
    return updated_user


def reset_admin_with_recovery_key(
    conn: sqlite3.Connection,
    recovery_key: str,
    new_admin_password: str
) -> bool:
    """Reset the admin user password using the Master Recovery Key.
    
    Usage is strictly logged in audit_log.
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
        
    # Reset admin password
    new_hash = hash_password(new_admin_password)
    now_str = datetime.now(timezone.utc).isoformat()
    
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET password_hash = ?, must_change_password = 1 WHERE username = 'admin';",
        (new_hash,)
    )
    cursor.close()
    
    record_audit_event(
        conn=conn,
        action="UPDATE",
        entity_type="SECURITY",
        new_value={"event": "ADMIN_PASSWORD_RESET_VIA_MASTER_KEY"},
        commit=True,
    )
    return True

"""Tamper-evident audit service for KASHIF-QA.

Maintains a cryptographic SHA-256 hash chain for all logged audit events.
"""

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

GENESIS_HASH = "0" * 64


def calculate_row_hash(
    prev_hash: str,
    timestamp: str,
    user_id: Optional[int],
    action: str,
    entity_type: Optional[str],
    entity_id: Optional[int],
    old_value_json: Optional[str],
    new_value_json: Optional[str],
) -> str:
    """Calculate the cryptographic SHA-256 hash for an audit log record."""
    payload = "|".join([
        prev_hash,
        timestamp,
        str(user_id) if user_id is not None else "",
        action,
        entity_type or "",
        str(entity_id) if entity_id is not None else "",
        old_value_json or "",
        new_value_json or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_audit_event(
    conn: sqlite3.Connection,
    action: str,
    user_id: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    old_value: Optional[Any] = None,
    new_value: Optional[Any] = None,
    commit: bool = True,
) -> int:
    """Record an immutable, cryptographically chained audit event."""
    cursor = conn.cursor()
    
    # 1. Fetch previous row's hash
    cursor.execute("SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1;")
    last_row = cursor.fetchone()
    prev_hash = last_row["row_hash"] if last_row and last_row["row_hash"] else GENESIS_HASH
    
    # 2. Format values
    timestamp = datetime.now(timezone.utc).isoformat()
    old_json = json.dumps(old_value, ensure_ascii=False) if old_value is not None else None
    new_json = json.dumps(new_value, ensure_ascii=False) if new_value is not None else None
    
    # 3. Compute row hash
    row_hash = calculate_row_hash(
        prev_hash=prev_hash,
        timestamp=timestamp,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value_json=old_json,
        new_value_json=new_json,
    )
    
    # 4. Insert parameterized record
    cursor.execute(
        """
        INSERT INTO audit_log (
            timestamp, user_id, action, entity_type, entity_id,
            old_value_json, new_value_json, prev_hash, row_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            timestamp,
            user_id,
            action,
            entity_type,
            entity_id,
            old_json,
            new_json,
            prev_hash,
            row_hash,
        ),
    )
    inserted_id = cursor.lastrowid
    cursor.close()
    
    if commit:
        conn.commit()
        
    return inserted_id


def verify_audit_chain(conn: sqlite3.Connection) -> Tuple[bool, List[int]]:
    """Verify integrity of the entire audit chain.
    
    Returns (is_valid, corrupted_row_ids).
    """
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM audit_log ORDER BY id ASC;")
    rows = cursor.fetchall()
    cursor.close()
    
    if not rows:
        return True, []
        
    corrupted_ids = []
    expected_prev = GENESIS_HASH
    
    for row in rows:
        # Check chaining
        if row["prev_hash"] != expected_prev:
            corrupted_ids.append(row["id"])
            
        # Check hash calculation
        computed_hash = calculate_row_hash(
            prev_hash=row["prev_hash"],
            timestamp=row["timestamp"],
            user_id=row["user_id"],
            action=row["action"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            old_value_json=row["old_value_json"],
            new_value_json=row["new_value_json"],
        )
        if computed_hash != row["row_hash"]:
            if row["id"] not in corrupted_ids:
                corrupted_ids.append(row["id"])
                
        expected_prev = row["row_hash"]
        
    return len(corrupted_ids) == 0, corrupted_ids

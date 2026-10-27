"""User session management and Role-Based Access Control (RBAC) for KASHIF-QA.

Enforces separation of duties across TECHNICIAN, LAB_MANAGER, and ADMIN roles.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any, Dict, Optional, Set

# Role definitions
ROLE_ADMIN = "ADMIN"
ROLE_LAB_MANAGER = "LAB_MANAGER"
ROLE_TECHNICIAN = "TECHNICIAN"

ROLES = (ROLE_ADMIN, ROLE_LAB_MANAGER, ROLE_TECHNICIAN)

# Permissions matrix
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    ROLE_ADMIN: {
        "manage_users",
        "view_audit_log",
        "backup_restore",
        "system_settings",
        "view_reports",
        "view_detectors",
        "view_instruments",
        "view_sources",
    },
    ROLE_LAB_MANAGER: {
        "view_audit_log",
        "manage_detectors",
        "manage_instruments",
        "manage_sources",
        "view_reports",
        "review_inspection",
        "approve_inspection",
        "reject_inspection",
        "sign_report",
        "export_data",
    },
    ROLE_TECHNICIAN: {
        "conduct_inspection",
        "input_measurements",
        "save_draft",
        "manage_detectors",
        "manage_instruments",
        "manage_sources",
        "view_reports",
        "export_data",
    },
}


@dataclass
class Session:
    """Represents an active authenticated user session."""
    user_id: int
    username: str
    full_name: str
    role: str
    must_change_password: bool = False
    login_time: Optional[datetime] = None

    def __post_init__(self):
        if self.login_time is None:
            self.login_time = datetime.now(timezone.utc)

    def can(self, permission: str) -> bool:
        """Check whether the session's role is granted the requested permission."""
        perms = ROLE_PERMISSIONS.get(self.role, set())
        return permission in perms

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Session":
        """Construct a Session object from a SQLite user row."""
        return cls(
            user_id=row["id"],
            username=row["username"],
            full_name=row["full_name"],
            role=row["role"],
            must_change_password=bool(row["must_change_password"]),
            login_time=datetime.now(timezone.utc),
        )

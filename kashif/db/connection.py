"""Database connection management for KASHIF-QA.

Enforces SQLite WAL (Write-Ahead Logging) mode and Foreign Key constraints.
"""

import os
import sqlite3
from typing import Optional

DEFAULT_DB_FILENAME = "kashif.db"


def get_default_db_path() -> str:
    """Return the configured or default database file path."""
    return os.getenv("KASHIF_DB_PATH", os.path.abspath(DEFAULT_DB_FILENAME))


def create_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Create and configure a new SQLite connection.
    
    Enforces:
    - PRAGMA foreign_keys = ON;
    - PRAGMA journal_mode = WAL;
    - Row factory set to sqlite3.Row for dict-like column access.
    """
    path = db_path if db_path is not None else get_default_db_path()
    
    # Ensure directory exists if path has parent directories
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
        
    conn = sqlite3.connect(path, timeout=30.0, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    
    # Enforce SQLite pragma configuration
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    
    # Memory databases do not use WAL
    if path != ":memory:":
        cursor.execute("PRAGMA journal_mode = WAL;")
        
    cursor.close()
    return conn

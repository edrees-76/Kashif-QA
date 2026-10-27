"""Shared pytest fixtures for KASHIF-QA test suite."""

import os
import sqlite3
import tempfile
import pytest

from kashif.db.connection import create_connection
from kashif.db.migrations import apply_migrations


@pytest.fixture
def temp_db_path():
    """Create a temporary database file on disk."""
    temp_dir = tempfile.mkdtemp()
    db_file = os.path.join(temp_dir, "test_kashif.db")
    yield db_file
    try:
        if os.path.exists(db_file):
            os.remove(db_file)
        for ext in ("-wal", "-shm", "-journal"):
            f = db_file + ext
            if os.path.exists(f):
                os.remove(f)
        os.rmdir(temp_dir)
    except Exception:
        pass


@pytest.fixture
def db_conn(temp_db_path):
    """Provide a configured SQLite connection with all migrations applied."""
    conn = create_connection(temp_db_path)
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def auto_mock_qt_modals(monkeypatch):
    """Automatically answer Yes/Ok to modal prompts and make QDialog.exec non-blocking across tests."""
    from PyQt6.QtWidgets import QMessageBox, QDialog
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)

    def non_blocking_exec(self):
        self.show()
        return 1
    monkeypatch.setattr(QDialog, "exec", non_blocking_exec)


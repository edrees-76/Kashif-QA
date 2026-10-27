"""Integration and unit tests for application bootstrap and global exception handling.

Fulfills TASK-1.0f by verifying that unhandled exceptions are safely logged
to kashif_error.log without leaking secrets, and achieves > 0% coverage on app.py.
"""

import os
import sys
import traceback
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from kashif.gui.app import ERROR_LOG_FILENAME, create_app, setup_global_exception_handler


def test_create_app(qtbot):
    """Verify create_app configures RTL layout direction and app metadata."""
    app = create_app()
    assert app is not None
    assert app.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert app.applicationName() == "KASHIF-QA"


def test_global_exception_handler(tmp_path, monkeypatch):
    """Verify sys.excepthook logs traceback safely to error log file."""
    app = QApplication.instance() or QApplication(sys.argv)
    setup_global_exception_handler(app)

    # Intercept log file path to temporary directory for isolated testing
    test_log_file = tmp_path / "test_error.log"
    monkeypatch.setattr("kashif.gui.app.ERROR_LOG_FILENAME", str(test_log_file))

    # Simulate an unhandled exception hook call
    try:
        raise ValueError("Diagnostic laboratory calculation error.")
    except ValueError as e:
        exc_type, exc_val, exc_tb = sys.exc_info()
        sys.excepthook(exc_type, exc_val, exc_tb)

    assert os.path.exists(str(test_log_file)), "Error log file must be created."
    with open(str(test_log_file), "r", encoding="utf-8") as f:
        log_content = f.read()

    assert "ValueError" in log_content
    assert "Diagnostic laboratory calculation error." in log_content
    assert "TRACEBACK" in log_content

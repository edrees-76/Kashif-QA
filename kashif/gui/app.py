"""Application initialization and global exception handling for KASHIF-QA.

Intercepts unhandled exceptions, writes standard execution traces to kashif_error.log
(without capturing or dumping local variables or function parameter values),
and presents a user-friendly dialog in Arabic.
"""

from datetime import datetime, timezone
import logging
import os
import sys
import traceback
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from kashif.gui.main_window import MainWindow

ERROR_LOG_FILENAME = "kashif_error.log"

logger = logging.getLogger(__name__)


def setup_global_exception_handler(app: QApplication) -> None:
    """Configure sys.excepthook to intercept unhandled exceptions."""
    def exception_hook(exc_type, exc_value, exc_tb):
        # Format standard traceback (files, line numbers, and function calls only - no locals captured)
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        tb_text = "".join(tb_lines)
        timestamp = datetime.now(timezone.utc).isoformat()
        
        log_entry = (
            f"================================================================================\n"
            f"TIMESTAMP: {timestamp}\n"
            f"EXCEPTION: {exc_type.__name__}: {exc_value}\n"
            f"TRACEBACK (Standard call stack without parameter values or locals):\n{tb_text}\n"
            f"================================================================================\n\n"
        )
        
        # Write to log file
        try:
            log_path = os.path.abspath(ERROR_LOG_FILENAME)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as file_err:
            logger.error("Failed to write to error log: %s", file_err)
            
        logger.error("Unhandled exception recorded in %s: %s: %s", ERROR_LOG_FILENAME, exc_type.__name__, exc_value)
        
        # Display polite dialog to user (suppressed when running offscreen)
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            try:
                msg_box = QMessageBox()
                msg_box.setIcon(QMessageBox.Icon.Critical)
                msg_box.setWindowTitle("تنبيه: حدث خطأ غير متوقع")
                msg_box.setText("عذراً، حدث خطأ غير متوقع أثناء تشغيل المنظومة.")
                msg_box.setInformativeText(
                    f"تم تسجيل التفاصيل التقنية للخطأ في ملف السجل:\n{ERROR_LOG_FILENAME}\n\n"
                    f"نوع الخطأ: {exc_type.__name__}"
                )
                msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg_box.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
                msg_box.exec()
            except Exception as dlg_err:
                logger.error("Failed to display error dialog: %s", dlg_err)

    sys.excepthook = exception_hook


def create_app(argv=None) -> QApplication:
    """Create and configure the QApplication instance with RTL support."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or sys.argv)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    # Fusion + forced light scheme so the OS dark mode never overrides the identity palette
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setApplicationName("KASHIF-QA")
    app.setOrganizationName("Kashif Nuclear Lab Systems")
    setup_global_exception_handler(app)
    return app

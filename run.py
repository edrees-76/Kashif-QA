"""Entry point for KASHIF-QA desktop application.

Handles application bootstrapping, schema migrations, first administrator creation,
persistent authentication session lifecycle, forced password changes, and idle locking.
"""

import logging
import sqlite3
import sys
from typing import Optional

from kashif.db.connection import create_connection
from kashif.db.migrations import apply_migrations
from kashif.gui.app import create_app
from kashif.gui.auth_dialogs import (
    ChangePasswordDialog,
    FirstAdminDialog,
    LoginDialog,
    MasterRecoveryKeyDialog,
)
from kashif.gui.main_window import MainWindow
from kashif.services.session import Session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("kashif.launcher")


def has_users(conn: sqlite3.Connection) -> bool:
    """Check whether any users exist in the database."""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM users;")
    row = cursor.fetchone()
    cursor.close()
    return bool(row and row["count"] > 0)


def main() -> int:
    """Bootstrap application services and run session loop."""
    logger.info("Initializing KASHIF-QA system...")

    # 1. Connect to SQLite database
    conn = create_connection()
    logger.info("Database connection established.")

    # 2. Apply pending schema migrations (Migration 001 & 002)
    applied = apply_migrations(conn)
    if applied:
        logger.info("Applied schema migrations: %s", applied)
    else:
        logger.info("Database schema is up to date.")

    # 3. Initialize Qt Application with RTL support
    app = create_app(sys.argv)

    # 4. If no users exist, present First Admin Creation dialog
    if not has_users(conn):
        first_admin_dlg = FirstAdminDialog(conn=conn)
        if not first_admin_dlg.exec():
            logger.info("First admin setup cancelled. Exiting.")
            return 0

        # Display Master Recovery Key once
        if first_admin_dlg.recovery_key:
            rec_dlg = MasterRecoveryKeyDialog(recovery_key=first_admin_dlg.recovery_key)
            rec_dlg.exec()

    # 5. Application Session Loop
    while True:
        # Show Login Dialog
        login_dlg = LoginDialog(conn=conn)
        if not login_dlg.exec() or not login_dlg.session:
            logger.info("Login cancelled by user. Terminating application.")
            return 0

        session: Session = login_dlg.session

        # Enforce must_change_password if flagged
        if session.must_change_password:
            pw_dlg = ChangePasswordDialog(conn=conn, user_id=session.user_id, username=session.username)
            if not pw_dlg.exec() or not pw_dlg.password_changed:
                logger.info("Password change aborted; returning to login screen.")
                continue
            session.must_change_password = False

        # Launch Main Window
        logged_out = False

        def on_logout():
            nonlocal logged_out
            logged_out = True

        window = MainWindow(db_conn=conn, session=session, on_logout=on_logout)
        window.show()

        app.exec()

        if not logged_out:
            # User closed window normally, exit app
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())

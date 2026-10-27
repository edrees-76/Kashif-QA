"""Entry point for KASHIF-QA desktop application.

Initializes SQLite database with WAL mode, runs pending schema migrations,
provisions the initial admin user if needed, and launches the PyQt6 GUI.
"""

import logging
import sys

from kashif.db.connection import create_connection
from kashif.db.migrations import apply_migrations
from kashif.gui.app import create_app
from kashif.gui.main_window import MainWindow
from kashif.gui.widgets.initial_credentials_dialog import InitialCredentialsDialog
from kashif.services.security import ensure_initial_admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("kashif.launcher")


def main() -> int:
    """Bootstrap application services and launch primary window."""
    logger.info("Initializing KASHIF-QA system...")
    
    # 1. Connect to SQLite database
    conn = create_connection()
    logger.info("Database connection established.")
    
    # 2. Apply pending migrations
    applied = apply_migrations(conn)
    if applied:
        logger.info("Applied schema migrations: %s", applied)
    else:
        logger.info("Database schema is up to date.")
        
    # 3. Ensure initial administrator exists (returns credentials only on initial run)
    init_creds = ensure_initial_admin(conn)
    
    # 4. Initialize PyQt6 UI application
    app = create_app(sys.argv)
    
    # 5. If initial credentials were provisioned, display them once in a secure modal dialog
    if init_creds:
        init_pass, recovery_key = init_creds
        cred_dialog = InitialCredentialsDialog(
            admin_password=init_pass,
            recovery_key=recovery_key
        )
        cred_dialog.exec()
        # Wipe sensitive in-memory references immediately
        del init_pass
        del recovery_key
        del init_creds
        
    # 6. Show main window
    window = MainWindow(db_conn=conn)
    window.show()
    
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

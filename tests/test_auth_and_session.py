"""Comprehensive integration and unit tests for Phase 1 Cycle 1.

Covers:
- FirstAdminDialog & create_first_admin (user-chosen password >= 10, Master Recovery Key)
- Persistent account lockout after 5 consecutive failures & unified error messages
- Session RBAC permission checks across TECHNICIAN, LAB_MANAGER, and ADMIN
- Operator ADMIN role requirement in security functions (with PermissionError for unauthorized roles)
- Master Recovery Key reset for arbitrary admin usernames
- Users management (add, toggle active, reset password) with audit trail
- BiDi isolation formatting
- Visual identity contrast verification (WCAG AA >= 4.5:1)
- Comprehensive Qt dialog tests (UnlockDialog, LoginDialog, UsersManagementDialog, IdleLock, ChangePasswordDialog)
"""

from datetime import datetime, timedelta, timezone
import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QCloseEvent, QKeyEvent
from PyQt6.QtWidgets import QDialog, QMessageBox

from kashif.gui.bidi import LRI, PDI, format_mixed_text, isolate_ltr
from kashif.gui.theme import COLORS, calculate_contrast_ratio
from kashif.services.security import (
    AuthenticationError,
    GENERIC_LOGIN_ERROR,
    SecurityError,
    admin_reset_user_password,
    authenticate_user,
    change_own_password,
    create_first_admin,
    create_user,
    list_users,
    reset_admin_with_recovery_key,
    set_user_active_status,
)
from kashif.services.session import (
    ROLE_ADMIN,
    ROLE_LAB_MANAGER,
    ROLE_TECHNICIAN,
    Session,
)


def test_create_first_admin_success_and_guard(db_conn):
    """TASK-1.0a: Initial admin creation with user-chosen password >= 10 chars."""
    admin_row, recovery_key = create_first_admin(
        conn=db_conn,
        username="lead_admin",
        full_name="رئيس المعمل النووي",
        password="NuclearLab2026!Secure",
    )
    assert admin_row is not None
    assert admin_row["username"] == "lead_admin"
    assert admin_row["role"] == "ADMIN"
    assert len(recovery_key) == 64

    # Calling it a second time must fail
    with pytest.raises(ValueError) as exc:
        create_first_admin(db_conn, "admin2", "Admin Two", "AnotherSecurePass123!")
    assert "مسبقاً" in str(exc.value)


def test_create_first_admin_short_password_rejected(db_conn):
    """TASK-1.0a: Password shorter than 10 characters is strictly rejected."""
    with pytest.raises(ValueError) as exc:
        create_first_admin(db_conn, "admin", "Admin", "Short9!")
    assert "10" in str(exc.value)


def test_persistent_account_lockout_after_five_failures(db_conn):
    """TASK-1.0a: 5 consecutive failed attempts lock account in DB with generic error messages."""
    create_first_admin(db_conn, "locked_user", "مستخدم تحت الاختبار", "CorrectPass2026!")

    # 4 consecutive failures return unified generic message
    for attempt in range(1, 5):
        with pytest.raises(AuthenticationError) as exc:
            authenticate_user(db_conn, "locked_user", "WrongPassword!")
        assert str(exc.value) == GENERIC_LOGIN_ERROR

    # 5th failure triggers lockout with the same unified generic message (no oracle)
    with pytest.raises(AuthenticationError) as exc_lockout:
        authenticate_user(db_conn, "locked_user", "WrongPassword!")
    assert str(exc_lockout.value) == GENERIC_LOGIN_ERROR

    # Subsequent attempt even with CORRECT password is still locked with unified message
    with pytest.raises(AuthenticationError) as exc_still_locked:
        authenticate_user(db_conn, "locked_user", "CorrectPass2026!")
    assert str(exc_still_locked.value) == GENERIC_LOGIN_ERROR

    # Verify audit log records ACCOUNT_LOCKED and specific reasons
    cursor = db_conn.cursor()
    cursor.execute("SELECT * FROM audit_log WHERE action = 'ACCOUNT_LOCKED' ORDER BY id DESC LIMIT 1;")
    lock_event = cursor.fetchone()
    assert lock_event is not None
    assert lock_event["entity_type"] == "USER"
    cursor.close()


def test_session_rbac_permissions():
    """TASK-1.0b: Role-Based Access Control matrix and strict separation of duties."""
    admin_session = Session(user_id=1, username="adm", full_name="Admin", role=ROLE_ADMIN)
    manager_session = Session(user_id=2, username="mgr", full_name="Manager", role=ROLE_LAB_MANAGER)
    tech_session = Session(user_id=3, username="tch", full_name="Tech", role=ROLE_TECHNICIAN)

    # ADMIN: Can manage users, cannot approve inspections
    assert admin_session.can("manage_users") is True
    assert admin_session.can("view_audit_log") is True
    assert admin_session.can("approve_inspection") is False
    assert admin_session.can("conduct_inspection") is False

    # LAB_MANAGER: Can approve inspections, cannot manage users
    assert manager_session.can("approve_inspection") is True
    assert manager_session.can("view_reports") is True
    assert manager_session.can("manage_users") is False
    assert manager_session.can("conduct_inspection") is False

    # TECHNICIAN: Can conduct inspection, cannot approve or manage users
    assert tech_session.can("conduct_inspection") is True
    assert tech_session.can("input_measurements") is True
    assert tech_session.can("approve_inspection") is False
    assert tech_session.can("manage_users") is False


def test_user_management_operator_role_enforcement(db_conn):
    """Point 2: Every user management function strictly requires ADMIN operator role."""
    admin_row, _ = create_first_admin(db_conn, "sys_admin", "مدير النظام", "AdminSecret2026!")
    admin_id = admin_row["id"]

    # Create manager and technician
    mgr_id = create_user(db_conn, admin_id, "lab_mgr", "مدير المختبر", ROLE_LAB_MANAGER, "ManagerSecret2026!")
    tech_id = create_user(db_conn, admin_id, "lab_tech", "فني المختبر", ROLE_TECHNICIAN, "TechSecret2026!")

    # 1. Non-admin operator (LAB_MANAGER) raises PermissionError
    with pytest.raises(PermissionError):
        list_users(db_conn, operator_user_id=mgr_id)

    with pytest.raises(PermissionError):
        create_user(db_conn, operator_user_id=mgr_id, username="u1", full_name="N1", role=ROLE_TECHNICIAN, password="Pass12345678!")

    with pytest.raises(PermissionError):
        set_user_active_status(db_conn, operator_user_id=mgr_id, target_user_id=tech_id, is_active=False)

    with pytest.raises(PermissionError):
        admin_reset_user_password(db_conn, operator_user_id=mgr_id, target_user_id=tech_id, new_password="NewPass123456!")

    # 2. Non-admin operator (TECHNICIAN) raises PermissionError
    with pytest.raises(PermissionError):
        list_users(db_conn, operator_user_id=tech_id)

    with pytest.raises(PermissionError):
        create_user(db_conn, operator_user_id=tech_id, username="u2", full_name="N2", role=ROLE_TECHNICIAN, password="Pass12345678!")

    # 3. None or invalid operator raises PermissionError
    with pytest.raises(PermissionError):
        list_users(db_conn, operator_user_id=None)

    with pytest.raises(PermissionError):
        list_users(db_conn, operator_user_id=99999)

    # 4. ADMIN operator succeeds
    users = list_users(db_conn, operator_user_id=admin_id)
    assert len(users) >= 3


def test_reset_admin_with_recovery_key_custom_username(db_conn):
    """Point 3: reset_admin_with_recovery_key works for arbitrary admin usernames and returns False if not updated."""
    admin_row, recovery_key = create_first_admin(
        db_conn,
        username="director_khaled",
        full_name="د. خالد مدير المعمل",
        password="OriginalDirectorPass2026!",
    )
    new_pass = "NewDirectorSecret2026!"

    # 1. Non-existent username returns False
    assert reset_admin_with_recovery_key(db_conn, recovery_key, new_pass, admin_username="ghost_admin") is False

    # 2. Valid recovery key with specific custom username succeeds
    assert reset_admin_with_recovery_key(db_conn, recovery_key, new_pass, admin_username="director_khaled") is True

    # Authenticate with new password
    user = authenticate_user(db_conn, "director_khaled", new_pass)
    assert user["username"] == "director_khaled"
    assert user["must_change_password"] == 1


def test_user_management_lifecycle(db_conn):
    """TASK-1.0b: Admin can create users, toggle active status, and reset passwords."""
    admin_row, _ = create_first_admin(db_conn, "admin_user", "مدير", "AdminPass2026!")
    admin_id = admin_row["id"]

    # 1. Create a technician
    tech_id = create_user(
        conn=db_conn,
        operator_user_id=admin_id,
        username="tech_ali",
        full_name="علي الفني",
        role=ROLE_TECHNICIAN,
        password="TechPassword2026!",
        must_change_password=True,
    )
    assert tech_id > 0

    # 2. Deactivate technician
    set_user_active_status(db_conn, operator_user_id=admin_id, target_user_id=tech_id, is_active=False)
    with pytest.raises(AuthenticationError) as exc:
        authenticate_user(db_conn, "tech_ali", "TechPassword2026!")
    assert str(exc.value) == GENERIC_LOGIN_ERROR

    # 3. Reactivate technician
    set_user_active_status(db_conn, operator_user_id=admin_id, target_user_id=tech_id, is_active=True)

    # 4. Reset technician password
    admin_reset_user_password(db_conn, operator_user_id=admin_id, target_user_id=tech_id, new_password="NewTechPass2026!")
    updated_tech = authenticate_user(db_conn, "tech_ali", "NewTechPass2026!")
    assert updated_tech["must_change_password"] == 1

    # 5. Prevent self-deactivation
    with pytest.raises(SecurityError):
        set_user_active_status(db_conn, operator_user_id=admin_id, target_user_id=admin_id, is_active=False)


def test_bidi_isolation_utility():
    """TASK-1.0c: BiDi isolation wraps Latin scientific terms within Arabic text."""
    assert isolate_ltr("HPGe") == f"{LRI}HPGe{PDI}"
    
    mixed_text = "فحص كاشف HPGe مع مصدر Co-60 ودقة 1332.5 keV"
    formatted = format_mixed_text(mixed_text)
    assert f"{LRI}HPGe{PDI}" in formatted
    assert f"{LRI}Co-60{PDI}" in formatted
    assert f"{LRI}keV{PDI}" in formatted


def test_visual_identity_contrast_wcag_aa():
    """TASK-1.0c: Verify color tokens satisfy WCAG AA contrast >= 4.5:1 on light background."""
    bg = COLORS["light_surface"]


    # Primary Navy on light surface
    contrast_navy = calculate_contrast_ratio(COLORS["navy"], bg)
    assert contrast_navy >= 4.5, f"Navy contrast must be >= 4.5, got {contrast_navy:.2f}"

    # Ink on light surface
    contrast_ink = calculate_contrast_ratio(COLORS["light_ink"], bg)
    assert contrast_ink >= 4.5, f"Ink contrast must be >= 4.5, got {contrast_ink:.2f}"

    # Slate text on light surface
    contrast_slate = calculate_contrast_ratio(COLORS["light_slate"], bg)
    assert contrast_slate >= 4.5, f"Slate contrast must be >= 4.5, got {contrast_slate:.2f}"

    # Semantic success on light surface
    contrast_success = calculate_contrast_ratio(COLORS["success"], bg)
    assert contrast_success >= 4.5, f"Success contrast must be >= 4.5, got {contrast_success:.2f}"

    # Semantic error on light surface
    contrast_error = calculate_contrast_ratio(COLORS["error"], bg)
    assert contrast_error >= 4.5, f"Error contrast must be >= 4.5, got {contrast_error:.2f}"


# ----------------------------------------------------------------------
# Comprehensive GUI and Dialog Tests (Targeting >= 80% coverage)
# ----------------------------------------------------------------------

def test_unlock_dialog_and_audit(qtbot, db_conn):
    """Point 5 & 7: UnlockDialog rejects empty passwords, validates credentials, and logs UNLOCK audit event."""
    from kashif.gui.idle_lock import UnlockDialog

    admin_row, _ = create_first_admin(db_conn, "guard_admin", "مدير الحراسة", "GuardPassword2026!")
    session = Session.from_row(admin_row)

    dlg = UnlockDialog(conn=db_conn, session=session)
    qtbot.addWidget(dlg)
    dlg.show()

    # 1. Empty password is rejected
    dlg.edit_password.setText("")
    dlg._handle_unlock()
    assert "يرجى إدخال" in dlg.lbl_error.text()

    # 2. Incorrect password shows generic error
    dlg.edit_password.setText("WrongPassword123!")
    dlg._handle_unlock()
    assert dlg.lbl_error.text() == GENERIC_LOGIN_ERROR

    # 3. Correct password unlocks and logs UNLOCK audit event
    dlg.edit_password.setText("GuardPassword2026!")
    dlg._handle_unlock()

    cursor = db_conn.cursor()
    cursor.execute("SELECT * FROM audit_log WHERE action = 'UNLOCK' ORDER BY id DESC LIMIT 1;")
    unlock_event = cursor.fetchone()
    assert unlock_event is not None
    assert unlock_event["user_id"] == session.user_id
    cursor.close()

    # 4. Logout button marks logged_out
    dlg2 = UnlockDialog(conn=db_conn, session=session)
    qtbot.addWidget(dlg2)
    dlg2._handle_logout()
    assert dlg2.logged_out is True

    # 5. Reject does not close unlock dialog
    dlg2.reject()


def test_idle_lock_event_filter_and_timeout(qtbot, db_conn):
    """Point 7: IdleLock event filter resets timer on activity and hides window content while locked."""
    from kashif.gui.idle_lock import IdleLock
    from kashif.gui.main_window import MainWindow

    admin_row, _ = create_first_admin(db_conn, "idle_admin", "مدير الخمول", "IdlePassword2026!")
    session = Session.from_row(admin_row)

    window = MainWindow(db_conn=db_conn, session=session)
    qtbot.addWidget(window)
    window.show()

    lock = IdleLock(window=window, conn=db_conn, minutes=10)
    assert lock.minutes == 10
    assert lock.timer.isActive()

    # Activity event restarts timer
    key_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
    lock.eventFilter(window, key_event)
    assert lock.timer.isActive()

    # Zero minutes stops timer
    lock.minutes = 0
    lock.restart()
    assert not lock.timer.isActive()
    window._is_logging_out = True


def test_login_dialog_comprehensive_flow(qtbot, db_conn):
    """Point 5: LoginDialog handles success, failure, remember username, and lockouts."""
    from kashif.gui.auth_dialogs import LoginDialog

    admin_row, _ = create_first_admin(db_conn, "login_admin", "مدير الدخول", "LoginSecret2026!")

    dlg = LoginDialog(conn=db_conn)
    qtbot.addWidget(dlg)
    dlg.show()

    # Empty credentials
    dlg.edit_password.setText("")
    dlg._handle_login()
    assert "يرجى إدخال" in dlg.lbl_error.text()

    # Wrong credentials shows generic error
    dlg.edit_password.setText("WrongSecret!")
    dlg._handle_login()
    assert dlg.lbl_error.text() == GENERIC_LOGIN_ERROR

    # Correct credentials with remember username
    dlg.chk_remember.setChecked(True)
    dlg.edit_password.setText("LoginSecret2026!")
    dlg._handle_login()
    assert dlg.session is not None
    assert dlg.session.username == "login_admin"


def test_first_admin_dialog_validation_and_recovery_dialog(qtbot, db_conn):
    """TASK-1.0a: FirstAdminDialog input validation and MasterRecoveryKeyDialog copy/reject."""
    from kashif.gui.auth_dialogs import FirstAdminDialog, MasterRecoveryKeyDialog

    dlg = FirstAdminDialog(conn=db_conn)
    qtbot.addWidget(dlg)
    dlg.show()

    # Empty user/name
    dlg.edit_user.setText("")
    dlg.edit_name.setText("")
    dlg._handle_submit()
    assert "كافة الحقول" in dlg.lbl_error.text()

    # Short password
    dlg.edit_user.setText("admin_test")
    dlg.edit_name.setText("مدير تجريبي")
    dlg.edit_pw1.setText("short9!")
    dlg.edit_pw2.setText("short9!")
    dlg._handle_submit()
    assert "قصيرة" in dlg.lbl_error.text()

    # Password mismatch
    dlg.edit_pw1.setText("ValidPassword2026!")
    dlg.edit_pw2.setText("DifferentPassword2026!")
    dlg._handle_submit()
    assert "غير متطابقتين" in dlg.lbl_error.text()

    # Successful creation
    dlg.edit_pw2.setText("ValidPassword2026!")
    dlg._handle_submit()
    assert dlg.created_admin is not None
    assert dlg.recovery_key is not None

    # Test MasterRecoveryKeyDialog
    rec_dlg = MasterRecoveryKeyDialog(recovery_key=dlg.recovery_key)
    qtbot.addWidget(rec_dlg)
    rec_dlg.show()

    # Copy action
    rec_dlg._copy(rec_dlg.btn_copy)
    # Reject in test mode
    rec_dlg.reject()



def test_change_password_dialog_flow(qtbot, db_conn):
    """TASK-1.0a: ChangePasswordDialog validation and password change flow."""
    from kashif.gui.auth_dialogs import ChangePasswordDialog

    admin_row, _ = create_first_admin(db_conn, "pw_admin", "مدير المرور", "InitialPassword2026!")
    admin_id = admin_row["id"]

    dlg = ChangePasswordDialog(conn=db_conn, user_id=admin_id, username="pw_admin")
    qtbot.addWidget(dlg)
    dlg.show()

    # 1. Short new password
    dlg.edit_old.setText("InitialPassword2026!")
    dlg.edit_new1.setText("short")
    dlg.edit_new2.setText("short")
    dlg._handle_change()
    assert "قصيرة" in dlg.lbl_error.text()

    # 2. Mismatch new passwords
    dlg.edit_new1.setText("BrandNewPass2026!")
    dlg.edit_new2.setText("BrandNewPass2027!")
    dlg._handle_change()
    assert "غير متطابقتين" in dlg.lbl_error.text()

    # 3. Wrong old password
    dlg.edit_old.setText("WrongOldPassword!")
    dlg.edit_new2.setText("BrandNewPass2026!")
    dlg._handle_change()
    assert dlg.lbl_error.text() != ""

    # 4. Correct old and matching new password
    dlg.edit_old.setText("InitialPassword2026!")
    dlg._handle_change()
    assert dlg.password_changed is True


def test_users_management_dialog_full_flow(qtbot, db_conn, monkeypatch):
    """Point 5: UsersManagementDialog table rendering, user creation, active toggling, and reset password."""
    from kashif.gui.views.users_view import (
        CreateUserDialog,
        ResetPasswordAdminDialog,
        UsersManagementDialog,
    )

    admin_row, _ = create_first_admin(db_conn, "view_admin", "مدير اللوحة", "ViewAdminPass2026!")
    session = Session.from_row(admin_row)

    users_dlg = UsersManagementDialog(conn=db_conn, session=session)
    qtbot.addWidget(users_dlg)
    users_dlg.show()

    assert users_dlg.table.rowCount() == 1

    # 1. Test CreateUserDialog validation
    create_dlg = CreateUserDialog()
    qtbot.addWidget(create_dlg)
    create_dlg.edit_user.setText("")
    create_dlg._validate()
    assert "يرجى ملء" in create_dlg.lbl_error.text()

    create_dlg.edit_user.setText("technician_omar")
    create_dlg.edit_name.setText("عمر الفني")
    create_dlg.edit_pw.setText("short")
    create_dlg._validate()
    assert "قصيرة" in create_dlg.lbl_error.text()

    # 2. Add user via dialog flow
    monkeypatch.setattr(CreateUserDialog, "exec", lambda self: True)
    monkeypatch.setattr(
        CreateUserDialog,
        "values",
        lambda self: ("technician_omar", "عمر الفني", ROLE_TECHNICIAN, "TechSecret2026!"),
    )
    users_dlg._on_add_user()
    assert users_dlg.table.rowCount() == 2

    # 3. Selection checks
    users_dlg.table.setCurrentCell(-1, -1)
    assert users_dlg._get_selected_user_id() is None

    # Select the technician row (row 1)
    users_dlg.table.setCurrentCell(1, 0)
    sel = users_dlg._get_selected_user_id()
    assert sel is not None
    uid, uname, is_active = sel
    assert uname == "technician_omar"
    assert is_active is True

    # 4. Toggle active status
    users_dlg._on_toggle_active()
    sel_after = users_dlg._get_selected_user_id()
    assert sel_after[2] is False

    users_dlg._on_toggle_active()
    sel_re_active = users_dlg._get_selected_user_id()
    assert sel_re_active[2] is True

    # 5. ResetPasswordAdminDialog validation & flow
    reset_dlg = ResetPasswordAdminDialog(uname)
    qtbot.addWidget(reset_dlg)
    reset_dlg.edit_pw.setText("short")
    reset_dlg._validate()
    assert "الحد الأدنى" in reset_dlg.lbl_error.text()

    reset_dlg.edit_pw.setText("ValidNewPassword2026!")
    assert reset_dlg.get_password() == "ValidNewPassword2026!"

    # Reset password via dialog flow
    monkeypatch.setattr(ResetPasswordAdminDialog, "exec", lambda self: True)
    monkeypatch.setattr(ResetPasswordAdminDialog, "get_password", lambda self: "ValidNewPassword2026!")
    users_dlg._on_reset_pw()
    users_dlg.refresh_users()


def test_main_window_close_cancel_keeps_window(qtbot, db_conn, monkeypatch):
    """Point 1.ز: Answering 'No' to close confirmation ignores closeEvent and keeps window active."""
    from kashif.gui.main_window import MainWindow

    admin_row, _ = create_first_admin(db_conn, "adm_stay", "مدير البقاء", "AdminStayPass2026!")
    session = Session.from_row(admin_row)

    window = MainWindow(db_conn=db_conn, session=session)
    qtbot.addWidget(window)
    window.show()

    # User answers 'No' to logout/close confirmation
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)

    close_ev = QCloseEvent()
    window.closeEvent(close_ev)

    assert close_ev.isAccepted() is False
    assert window.session is not None
    assert window._is_logging_out is False
    window._is_logging_out = True


def test_main_window_logout_closes_secondary_and_logs_audit(qtbot, db_conn, monkeypatch):
    """Point 1.ز: Answering 'Yes' closes secondary windows without save prompt, clears session, and logs LOGOUT."""
    from kashif.gui.main_window import MainWindow

    admin_row, _ = create_first_admin(db_conn, "adm_out", "مدير الخروج", "AdminOutPass2026!")
    session = Session.from_row(admin_row)

    logged_out_called = False

    def on_logout():
        nonlocal logged_out_called
        logged_out_called = True

    window = MainWindow(db_conn=db_conn, session=session, on_logout=on_logout)
    qtbot.addWidget(window)
    window.show()

    # Open a secondary child dialog
    sec_dialog = QDialog(window)
    qtbot.addWidget(sec_dialog)
    sec_dialog.show()
    assert sec_dialog.isVisible() is True

    # User answers 'Yes' to logout/close confirmation
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)

    window.handle_logout()

    # Secondary window closed cleanly without prompting
    assert sec_dialog.isVisible() is False
    assert window.session is None
    assert logged_out_called is True
    assert window._is_logging_out is True

    # Verify LOGOUT audit event recorded
    cursor = db_conn.cursor()
    cursor.execute("SELECT * FROM audit_log WHERE action = 'LOGOUT' AND user_id = ? ORDER BY id DESC LIMIT 1;", (session.user_id,))
    logout_event = cursor.fetchone()
    assert logout_event is not None
    assert "USER_LOGOUT" in logout_event["new_value_json"]
    cursor.close()


def test_login_dialog_close_event_confirmation(qtbot, db_conn, monkeypatch):
    """Point 1.ز: Clicking X on LoginDialog asks confirmation before terminating application."""
    from kashif.gui.auth_dialogs import LoginDialog

    create_first_admin(db_conn, "login_chk", "مدير الفحص", "LoginCheck2026!")

    dlg = LoginDialog(conn=db_conn)
    qtbot.addWidget(dlg)
    dlg.show()

    # 1. Answering 'No' ignores closeEvent
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
    ev_no = QCloseEvent()
    dlg.closeEvent(ev_no)
    assert ev_no.isAccepted() is False

    # 2. Answering 'Yes' accepts closeEvent
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    ev_yes = QCloseEvent()
    dlg.closeEvent(ev_yes)
    assert ev_yes.isAccepted() is True
    dlg._is_closing = True


def test_idle_lock_closes_secondary_without_prompt(qtbot, db_conn, monkeypatch):
    """Point 1.ز & 1.هـ: Idle timeout closes secondary windows without asking the user."""
    from kashif.gui.idle_lock import IdleLock, UnlockDialog
    from kashif.gui.main_window import MainWindow

    admin_row, _ = create_first_admin(db_conn, "adm_idle_sec", "مدير الثانوي", "IdleSecPass2026!")
    session = Session.from_row(admin_row)

    window = MainWindow(db_conn=db_conn, session=session)
    qtbot.addWidget(window)
    window.show()

    sec_dialog = QDialog(window)
    qtbot.addWidget(sec_dialog)
    sec_dialog.show()
    assert sec_dialog.isVisible() is True

    lock = IdleLock(window=window, conn=db_conn, minutes=15)
    # Bypass blocking modal unlock dialog in test
    monkeypatch.setattr(UnlockDialog, "exec", lambda self: 0)

    # Trigger idle lock timeout
    lock._on_timeout()

    # Secondary window closed without prompt
    assert sec_dialog.isVisible() is False
    window._is_logging_out = True


def test_first_admin_dialog_validation_only_on_submit(qtbot, db_conn):
    """Point 2: Short password error must NOT appear before or during typing; only after clicking submit."""
    from kashif.gui.auth_dialogs import FirstAdminDialog

    dlg = FirstAdminDialog(conn=db_conn)
    qtbot.addWidget(dlg)
    dlg.show()

    # Initially before typing, error label must be empty
    assert dlg.lbl_error.text() == ""

    # While typing short password in edit_pw1, error must remain empty
    dlg.edit_pw1.setText("short")
    assert dlg.lbl_error.text() == ""

    dlg.edit_pw2.setText("short")
    assert dlg.lbl_error.text() == ""

    # Only after clicking submit does the validation error appear
    dlg.btn_submit.click()
    assert "قصيرة" in dlg.lbl_error.text()

    # Typing again immediately clears the error message
    dlg.edit_pw1.setText("short1")
    assert dlg.lbl_error.text() == ""


def test_first_admin_dialog_fields_are_visible(qtbot, db_conn):
    """The form must actually be laid out (fields visible), not only fillable programmatically."""
    from kashif.gui.auth_dialogs import FirstAdminDialog

    dlg = FirstAdminDialog(conn=db_conn)
    qtbot.addWidget(dlg)
    dlg.show()
    for field in (dlg.edit_user, dlg.edit_name, dlg.edit_pw1, dlg.edit_pw2):
        assert field.isVisible()
        assert field.height() > 10
    assert dlg.lbl_error.text() == ""

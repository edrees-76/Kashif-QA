"""Database schema migration engine for KASHIF-QA.

Tracks applied migrations in schema_migrations table and executes new migrations sequentially.
"""

from datetime import datetime, timezone
import logging
import sqlite3
from typing import Callable, Dict, List, Tuple

from kashif.core.decay import half_life_to_days

logger = logging.getLogger(__name__)


def migration_001_initial_core_security(conn: sqlite3.Connection) -> None:
    """Migration 001: Foundation schema - users, audit_log, triggers, system_settings."""
    cursor = conn.cursor()
    
    # 1. users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('TECHNICIAN', 'LAB_MANAGER', 'ADMIN')),
        is_active INTEGER NOT NULL DEFAULT 1,
        must_change_password INTEGER NOT NULL DEFAULT 0,
        last_login_at TEXT,
        created_at TEXT NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);")

    # 2. audit_log table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        user_id INTEGER,
        action TEXT NOT NULL,
        entity_type TEXT,
        entity_id INTEGER,
        old_value_json TEXT,
        new_value_json TEXT,
        prev_hash TEXT NOT NULL,
        row_hash TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_user_id ON audit_log(user_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);")

    # 3. Triggers preventing UPDATE and DELETE on audit_log
    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS prevent_audit_update
    BEFORE UPDATE ON audit_log
    BEGIN
        SELECT RAISE(FAIL, 'Updates are strictly prohibited on audit_log table.');
    END;
    """)

    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS prevent_audit_delete
    BEFORE DELETE ON audit_log
    BEGIN
        SELECT RAISE(FAIL, 'Deletions are strictly prohibited on audit_log table.');
    END;
    """)

    # 4. system_settings table (for recovery key hash, system metadata)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    cursor.close()


def migration_002_user_lockout_and_session(conn: sqlite3.Connection) -> None:
    """Migration 002: Add failed_attempts and locked_until columns to users table for lockout enforcement."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(users);")
    cols = {row["name"] for row in cursor.fetchall()}
    if "failed_attempts" not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN failed_attempts INTEGER NOT NULL DEFAULT 0;")
    if "locked_until" not in cols:
        cursor.execute("ALTER TABLE users ADD COLUMN locked_until TEXT;")
    cursor.close()



def migration_003_registries_detectors_instruments_sources(conn: sqlite3.Connection) -> None:
    """Migration 003: Technical registries - detectors, instruments, and radioactive sources.
    
    Includes multi-facility support, strict composite uniqueness constraints,
    audit lifecycle status, and calibration date tracking.
    """
    cursor = conn.cursor()
    
    # 1. detectors table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS detectors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        detector_type TEXT NOT NULL CHECK(detector_type IN ('HPGE', 'NAI')),
        manufacturer TEXT NOT NULL,
        model TEXT NOT NULL,
        serial_number TEXT NOT NULL,
        crystal_type TEXT CHECK(crystal_type IN ('COAXIAL', 'PLANAR')),
        window_type TEXT CHECK(window_type IN ('BERYLLIUM', 'ALUMINUM', 'CARBON_COMPOSITE', 'OTHER')),
        beryllium_window_status TEXT CHECK(beryllium_window_status IN ('INTACT', 'SCRATCHED', 'BROKEN')),
        preamp_type TEXT CHECK(preamp_type IN ('RESISTIVE', 'TRP', 'OTHER')),
        nominal_voltage REAL NOT NULL,
        voltage_polarity TEXT CHECK(voltage_polarity IN ('POSITIVE', 'NEGATIVE')),
        nominal_fwhm_kev REAL,
        nominal_efficiency_pct REAL,
        nominal_resolution_pct REAL,
        diameter_inch REAL,
        height_inch REAL,
        facility_name TEXT,
        status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'MAINTENANCE', 'RETIRED')),
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(manufacturer, serial_number)
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_detectors_code ON detectors(code);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_detectors_mfr_sn ON detectors(manufacturer, serial_number);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_detectors_status ON detectors(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_detectors_facility ON detectors(facility_name);")

    # 2. instruments table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS instruments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instrument_type TEXT NOT NULL CHECK(instrument_type IN ('NIM_BIN', 'HV_SUPPLY', 'AMPLIFIER', 'MCA', 'SCOPE')),
        manufacturer TEXT NOT NULL,
        model TEXT NOT NULL,
        serial_number TEXT NOT NULL,
        ripple_mv REAL,
        regulation_pct REAL,
        channels_count INTEGER,
        shaping_range_min_us REAL,
        shaping_range_max_us REAL,
        cal_date TEXT,
        cal_expiry TEXT,
        is_in_service INTEGER NOT NULL DEFAULT 1,
        facility_name TEXT,
        status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'MAINTENANCE', 'RETIRED')),
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(manufacturer, serial_number)
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_instruments_mfr_sn ON instruments(manufacturer, serial_number);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_instruments_type ON instruments(instrument_type);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_instruments_status ON instruments(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_instruments_facility ON instruments(facility_name);")

    # 3. sources table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sources (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nuclide TEXT NOT NULL CHECK(nuclide IN ('Co-60', 'Cs-137')),
        source_code TEXT UNIQUE NOT NULL,
        serial_number TEXT NOT NULL,
        cert_activity_bq REAL NOT NULL,
        cert_date TEXT NOT NULL,
        half_life_days REAL NOT NULL,
        certificate_number TEXT,
        facility_name TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'RETIRED')),
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(nuclide, serial_number),
        UNIQUE(source_code)
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_code ON sources(source_code);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_nuclide_sn ON sources(nuclide, serial_number);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_facility ON sources(facility_name);")

    cursor.close()


def migration_004_multi_isotope_sources(conn: sqlite3.Connection) -> None:
    """Migration 004: Reference table nuclides and multi-isotope source architecture.
    
    Creates nuclides reference table, populates standard gamma nuclides,
    recreates sources table without single-nuclide check constraint,
    creates source_isotopes table, and migrates existing source data safely.
    """
    cursor = conn.cursor()
    now_str = datetime.now(timezone.utc).isoformat()
    
    # 1. nuclides reference table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS nuclides (
        code TEXT PRIMARY KEY,
        name_ar TEXT NOT NULL,
        name_en TEXT NOT NULL,
        half_life_days REAL NOT NULL,
        half_life_value REAL NOT NULL,
        half_life_unit TEXT NOT NULL CHECK(half_life_unit IN ('s', 'm', 'h', 'd', 'y')),
        reference TEXT NOT NULL,
        confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0, 1)),
        is_custom INTEGER NOT NULL DEFAULT 0 CHECK(is_custom IN (0, 1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_nuclides_code ON nuclides(code);")
    
    # Seed standard gamma nuclides (half_life_days derived dynamically via half_life_to_days)
    standard_nuclides = [
        # code, name_ar, name_en, half_life_value, half_life_unit, reference, confirmed, is_custom
        ("Co-60", "كوبالت-60", "Cobalt-60", 5.2714, "y", "IAEA Procedure Nº MRNI-507 (HPGe) / docs/tasks.md TASK-1.3", 1, 0),
        ("Cs-137", "سيزيوم-137", "Cesium-137", 30.08, "y", "IAEA Procedure Nº MRNI-504 (NaI) / docs/tasks.md TASK-1.3", 1, 0),
        ("Am-241", "أمريسيوم-241", "Americium-241", 432.6, "y", "NNDC NuDat 3.0 (https://www.nndc.bnl.gov/nudat3/) / ENSDF (Evaluation year: 2014, M.J. Martin)", 0, 0),
        ("Ba-133", "باريوم-133", "Barium-133", 10.551, "y", "NNDC NuDat 3.0 (https://www.nndc.bnl.gov/nudat3/) / ENSDF (Evaluation year: 2021, S. Singh et al.)", 0, 0),
        ("Cd-109", "كادميوم-109", "Cadmium-109", 461.4, "d", "DDEP / LNHB (http://www.lnhb.fr/nuclear-data/nuclear-data-table/) & BIPM Monographie 5 Vol. 5 (Evaluation year: 2010, E. Schönfeld)", 0, 0),
        ("Co-57", "كوبالت-57", "Cobalt-57", 271.74, "d", "NNDC NuDat 3.0 (https://www.nndc.bnl.gov/nudat3/) / ENSDF (Evaluation year: 2019, M. Shamsuzzoha Basunia)", 0, 0),
        ("Eu-152", "يوروبيوم-152", "Europium-152", 13.517, "y", "NNDC NuDat 3.0 (https://www.nndc.bnl.gov/nudat3/) / ENSDF (Evaluation year: 2013, M.J. Martin)", 0, 0),
        ("Mn-54", "منغنيز-54", "Manganese-54", 312.20, "d", "NNDC NuDat 3.0 (https://www.nndc.bnl.gov/nudat3/) / ENSDF (Evaluation year: 2014, D. Abriola et al.)", 0, 0),
        ("Na-22", "صوديوم-22", "Sodium-22", 2.6018, "y", "NNDC NuDat 3.0 (https://www.nndc.bnl.gov/nudat3/) / ENSDF (Evaluation year: 2015, M. Shamsuzzoha Basunia)", 0, 0),
        ("Y-88", "إتريوم-88", "Yttrium-88", 106.626, "d", "DDEP / LNHB (http://www.lnhb.fr/nuclear-data/nuclear-data-table/) & BIPM Monographie 5 (Evaluation year: 2014, E. Browne)", 0, 0),
        ("Zn-65", "زنك-65", "Zinc-65", 243.66, "d", "NNDC NuDat 3.0 (https://www.nndc.bnl.gov/nudat3/) / ENSDF (Evaluation year: 2024, B. Singh et al.)", 0, 0),
        ("Ra-226", "راديوم-226", "Radium-226", 1600.0, "y", "DDEP / LNHB (http://www.lnhb.fr/nuclear-data/nuclear-data-table/) & BIPM Monographie 5 Vol. 5 (Evaluation year: 2010, V. Chisté)", 0, 0),
    ]
    for code, name_ar, name_en, val, unit, ref, conf, is_cust in standard_nuclides:
        hl_days = half_life_to_days(val, unit)
        cursor.execute("""
        INSERT OR IGNORE INTO nuclides (
            code, name_ar, name_en, half_life_days, half_life_value,
            half_life_unit, reference, confirmed, is_custom, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (code, name_ar, name_en, hl_days, val, unit, ref, conf, is_cust, now_str, now_str))

    # 2. Check existing sources table structure
    cursor.execute("PRAGMA table_info(sources);")
    sources_cols = {row["name"] for row in cursor.fetchall()}
    
    # 3. Create sources_new table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sources_new (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_code TEXT UNIQUE NOT NULL,
        serial_number TEXT NOT NULL,
        certificate_number TEXT,
        facility_name TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'RETIRED')),
        notes TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(source_code)
    );
    """)

    old_sources_data = []
    if "nuclide" in sources_cols:
        cursor.execute("""
        SELECT id, nuclide, source_code, serial_number, cert_activity_bq, cert_date,
               certificate_number, facility_name, is_active, status, notes, created_at, updated_at
        FROM sources;
        """)
        old_sources_data = cursor.fetchall()
        
        for row in old_sources_data:
            cursor.execute("""
            INSERT INTO sources_new (
                id, source_code, serial_number, certificate_number,
                facility_name, is_active, status, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (
                row["id"], row["source_code"], row["serial_number"], row["certificate_number"],
                row["facility_name"], row["is_active"], row["status"], row["notes"],
                row["created_at"], row["updated_at"],
            ))
        
        cursor.execute("DROP TABLE sources;")
        cursor.execute("ALTER TABLE sources_new RENAME TO sources;")
    else:
        cursor.execute("DROP TABLE IF EXISTS sources_new;")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_code ON sources(source_code);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_sn ON sources(serial_number);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_facility ON sources(facility_name);")

    # 4. Create source_isotopes table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS source_isotopes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        nuclide_code TEXT NOT NULL REFERENCES nuclides(code),
        cert_activity_bq REAL NOT NULL,
        cert_date TEXT NOT NULL,
        UNIQUE(source_id, nuclide_code)
    );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source_isotopes_source_id ON source_isotopes(source_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source_isotopes_nuclide ON source_isotopes(nuclide_code);")

    # Migrate old isotopes
    for row in old_sources_data:
        cursor.execute("""
        INSERT OR IGNORE INTO source_isotopes (source_id, nuclide_code, cert_activity_bq, cert_date)
        VALUES (?, ?, ?, ?);
        """, (row["id"], row["nuclide"], row["cert_activity_bq"], row["cert_date"]))

    cursor.close()


def migration_005_hpge_pn_and_detector_specs(conn: sqlite3.Connection) -> None:
    """Migration 005: Add semiconductor type (P/N), certified polarity, ramp limits to detectors table.
    
    Implements Decisions 14 & 15-3:
    - semiconductor_type (P_TYPE, N_TYPE, UNSPECIFIED)
    - certified_polarity (POSITIVE, NEGATIVE)
    - certificate_ref
    - cooling_hours_cert
    - hv_ramp_max_v_per_s (default 10.0)
    - hv_ramp_reference
    - pmt_voltage_tolerance_pct for NaI
    Existing detectors retain NULL / UNSPECIFIED crystal type without data loss.
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(detectors);")
    cols = {row["name"] for row in cursor.fetchall()}

    if "semiconductor_type" not in cols:
        cursor.execute("ALTER TABLE detectors ADD COLUMN semiconductor_type TEXT CHECK(semiconductor_type IN ('P_TYPE', 'N_TYPE', 'UNSPECIFIED'));")
        cursor.execute("UPDATE detectors SET semiconductor_type = 'UNSPECIFIED' WHERE detector_type = 'HPGE' AND (semiconductor_type IS NULL OR semiconductor_type = '');")
    if "certified_polarity" not in cols:
        cursor.execute("ALTER TABLE detectors ADD COLUMN certified_polarity TEXT CHECK(certified_polarity IN ('POSITIVE', 'NEGATIVE'));")
    if "certificate_ref" not in cols:
        cursor.execute("ALTER TABLE detectors ADD COLUMN certificate_ref TEXT;")
    if "cooling_hours_cert" not in cols:
        cursor.execute("ALTER TABLE detectors ADD COLUMN cooling_hours_cert REAL;")
    if "hv_ramp_max_v_per_s" not in cols:
        cursor.execute("ALTER TABLE detectors ADD COLUMN hv_ramp_max_v_per_s REAL NOT NULL DEFAULT 10.0;")
    if "hv_ramp_reference" not in cols:
        cursor.execute("ALTER TABLE detectors ADD COLUMN hv_ramp_reference TEXT;")
    if "pmt_voltage_tolerance_pct" not in cols:
        cursor.execute("ALTER TABLE detectors ADD COLUMN pmt_voltage_tolerance_pct REAL;")

    cursor.close()


def migration_006_nuclides_uncertainty_and_zn65_ddep(conn: sqlite3.Connection) -> None:
    """Migration 006: Add uncertainty and reference URL fields, and apply verified Zn-65 DDEP data.
    
    Implements Decision 15-8:
    - half_life_uncertainty (same unit as half_life_value)
    - reference_url
    - reference_accessed
    - Zn-65 corrected to 244.01 (9) d from official LNHB evaluation (2026-10-27)
    - Recalculates half_life_days using half_life_to_days
    - Modifies non-custom nuclides only (is_custom=0)
    - Leaves custom nuclides and Becquerel source activities completely untouched
    """
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(nuclides);")
    cols = {row["name"] for row in cursor.fetchall()}

    if "half_life_uncertainty" not in cols:
        cursor.execute("ALTER TABLE nuclides ADD COLUMN half_life_uncertainty REAL;")
    if "reference_url" not in cols:
        cursor.execute("ALTER TABLE nuclides ADD COLUMN reference_url TEXT;")
    if "reference_accessed" not in cols:
        cursor.execute("ALTER TABLE nuclides ADD COLUMN reference_accessed TEXT;")

    # Populate verified Zn-65 from LNHB/DDEP (Decision 15-8, verified 2026-10-27)
    now_str = datetime.now(timezone.utc).isoformat()
    zn65_days = half_life_to_days(244.01, "d")
    cursor.execute("""
    UPDATE nuclides
    SET half_life_value = 244.01,
        half_life_unit = 'd',
        half_life_days = ?,
        half_life_uncertainty = 0.09,
        confirmed = 1,
        reference = 'LNHB / CEA - Table de Radionucléides (Zn-65)',
        reference_url = 'http://www.lnhb.fr/nuclides/Zn-65_tables.pdf',
        reference_accessed = '2026-10-27',
        updated_at = ?
    WHERE code = 'Zn-65' AND is_custom = 0;
    """, (zn65_days, now_str))

    cursor.close()




def migration_007_lnhb_half_lives(conn: sqlite3.Connection) -> None:
    """Migration 007: Update 9 standard gamma radionuclides with verified LNHB/DDEP half-lives.

    Source: Official LNHB/DDEP (Laboratoire National Henri Becquerel / Decay Data Evaluation Project)
    Table de Radionucléides, verified by Project Manager on 2026-10-28.
    Unit 'a' (annum/year) in LNHB tables corresponds to standard year 'y' (1 y = 365.25 d).

    Radionuclides updated (where is_custom = 0):
    - Am-241: 432.6 (6) y, uncertainty = 0.6 y, https://www.lnhb.fr/nuclides/Am-241_tables.pdf
    - Ba-133: 10.539 (6) y, uncertainty = 0.006 y, https://www.lnhb.fr/nuclides/Ba-133_tables.pdf
    - Cd-109: 461.9 (4) d, uncertainty = 0.4 d, https://www.lnhb.fr/nuclides/Cd-109_tables.pdf
    - Co-57:  271.81 (4) d, uncertainty = 0.04 d, https://www.lnhb.fr/nuclides/Co-57_tables.pdf
    - Eu-152: 13.522 (16) y, uncertainty = 0.016 y, https://www.lnhb.fr/nuclides/Eu-152_tables.pdf
    - Mn-54:  312.19 (3) d, uncertainty = 0.03 d, https://www.lnhb.fr/nuclides/Mn-54_tables.pdf
    - Na-22:  2.6029 (8) y, uncertainty = 0.0008 y, https://www.lnhb.fr/nuclides/Na-22_tables.pdf
    - Y-88:   106.63 (5) d, uncertainty = 0.05 d, https://www.lnhb.fr/nuclides/Y-88_tables.pdf
    - Ra-226: 1600 (7) y, uncertainty = 7.0 y, https://www.lnhb.fr/nuclides/Ra-226_tables.pdf

    Unchanged:
    - Co-60, Cs-137, Zn-65
    - Custom nuclides (is_custom = 1)
    - Any source activity records
    """
    cursor = conn.cursor()
    now_str = datetime.now(timezone.utc).isoformat()

    lnhb_updates = [
        # code, value, unit, uncertainty, url
        ("Am-241", 432.6, "y", 0.6, "https://www.lnhb.fr/nuclides/Am-241_tables.pdf"),
        ("Ba-133", 10.539, "y", 0.006, "https://www.lnhb.fr/nuclides/Ba-133_tables.pdf"),
        ("Cd-109", 461.9, "d", 0.4, "https://www.lnhb.fr/nuclides/Cd-109_tables.pdf"),
        ("Co-57", 271.81, "d", 0.04, "https://www.lnhb.fr/nuclides/Co-57_tables.pdf"),
        ("Eu-152", 13.522, "y", 0.016, "https://www.lnhb.fr/nuclides/Eu-152_tables.pdf"),
        ("Mn-54", 312.19, "d", 0.03, "https://www.lnhb.fr/nuclides/Mn-54_tables.pdf"),
        ("Na-22", 2.6029, "y", 0.0008, "https://www.lnhb.fr/nuclides/Na-22_tables.pdf"),
        ("Y-88", 106.63, "d", 0.05, "https://www.lnhb.fr/nuclides/Y-88_tables.pdf"),
        ("Ra-226", 1600.0, "y", 7.0, "https://www.lnhb.fr/nuclides/Ra-226_tables.pdf"),
    ]

    for code, val, unit, uncert, url in lnhb_updates:
        hl_days = half_life_to_days(val, unit)
        ref_text = f"LNHB/DDEP - Table de Radionucléides ({code})"
        cursor.execute("""
        UPDATE nuclides
        SET half_life_value = ?,
            half_life_unit = ?,
            half_life_days = ?,
            half_life_uncertainty = ?,
            reference = ?,
            reference_url = ?,
            reference_accessed = '2026-10-28',
            confirmed = 1,
            updated_at = ?
        WHERE code = ? AND is_custom = 0;
        """, (val, unit, hl_days, uncert, ref_text, url, now_str, code))

    cursor.close()


def migration_008_co60_cs137_lnhb_half_lives(conn: sqlite3.Connection) -> None:
    """Migration 008: Update Co-60 and Cs-137 with verified official LNHB/DDEP half-lives and uncertainties.

    Source: Official LNHB/DDEP (Laboratoire National Henri Becquerel / Decay Data Evaluation Project)
    Table de Radionucléides, verified by Project Manager on 2026-10-29.
    Unit 'a' (annum/year) in LNHB tables corresponds to standard year 'y' (1 y = 365.25 d).

    Radionuclides updated (where is_custom = 0):
    - Co-60:  5.2711 (8) y, uncertainty = 0.0008 y, https://www.lnhb.fr/nuclides/Co-60_tables.pdf
    - Cs-137: 30.018 (22) y, uncertainty = 0.022 y, https://www.lnhb.fr/nuclides/Cs-137_tables.pdf

    Unchanged:
    - All other radionuclides
    - Custom nuclides (is_custom = 1)
    - Any source activity records
    """
    cursor = conn.cursor()
    now_str = datetime.now(timezone.utc).isoformat()

    lnhb_updates = [
        # code, value, unit, uncertainty, url
        ("Co-60", 5.2711, "y", 0.0008, "https://www.lnhb.fr/nuclides/Co-60_tables.pdf"),
        ("Cs-137", 30.018, "y", 0.022, "https://www.lnhb.fr/nuclides/Cs-137_tables.pdf"),
    ]

    for code, val, unit, uncert, url in lnhb_updates:
        hl_days = half_life_to_days(val, unit)
        ref_text = f"LNHB/DDEP - Table de Radionucléides ({code})"
        cursor.execute("""
        UPDATE nuclides
        SET half_life_value = ?,
            half_life_unit = ?,
            half_life_days = ?,
            half_life_uncertainty = ?,
            reference = ?,
            reference_url = ?,
            reference_accessed = '2026-10-28',
            confirmed = 1,
            updated_at = ?
        WHERE code = ? AND is_custom = 0;
        """, (val, unit, hl_days, uncert, ref_text, url, now_str, code))

    cursor.close()


# Registry of migrations: version -> (description, migration_function)
MIGRATIONS: Dict[int, Tuple[str, Callable[[sqlite3.Connection], None]]] = {
    1: ("Initial schema: users, audit_log with hash-chaining triggers, system_settings", migration_001_initial_core_security),
    2: ("Add account lockout and session tracking fields to users table", migration_002_user_lockout_and_session),
    3: ("Technical registries: detectors, instruments, sources with composite uniqueness", migration_003_registries_detectors_instruments_sources),
    4: ("Nuclides reference registry and multi-isotope radioactive source architecture", migration_004_multi_isotope_sources),
    5: ("HPGe crystal semiconductor type (P/N), certified polarity, ramp limits, and NaI specs", migration_005_hpge_pn_and_detector_specs),
    6: ("Nuclide uncertainties, DDEP references, and Zn-65 verified half-life correction", migration_006_nuclides_uncertainty_and_zn65_ddep),
    7: ("Update verified LNHB/DDEP half-lives and uncertainties for 9 standard gamma radionuclides", migration_007_lnhb_half_lives),
    8: ("Update verified LNHB/DDEP half-lives and uncertainties for Co-60 and Cs-137", migration_008_co60_cs137_lnhb_half_lives),
}


def init_migrations_table(conn: sqlite3.Connection) -> None:
    """Ensure schema_migrations table exists."""
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        description TEXT NOT NULL
    );
    """)
    cursor.close()
    conn.commit()


def get_applied_migrations(conn: sqlite3.Connection) -> List[int]:
    """Return a sorted list of applied migration versions."""
    init_migrations_table(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
    rows = cursor.fetchall()
    cursor.close()
    return [row["version"] for row in rows]


def apply_migrations(conn: sqlite3.Connection) -> List[int]:
    """Apply all unapplied migrations in ascending order.
    
    Returns the list of newly applied migration versions.
    """
    init_migrations_table(conn)
    applied = set(get_applied_migrations(conn))
    newly_applied = []
    
    for version in sorted(MIGRATIONS.keys()):
        if version not in applied:
            description, migrate_fn = MIGRATIONS[version]
            logger.info("Applying migration %03d: %s", version, description)
            
            # Execute migration in transaction
            try:
                migrate_fn(conn)
                now_str = datetime.now(timezone.utc).isoformat()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?);",
                    (version, now_str, description),
                )
                cursor.close()
                conn.commit()
                newly_applied.append(version)
            except Exception as e:
                conn.rollback()
                logger.error("Migration %03d failed: %s", version, e)
                raise
                
    return newly_applied

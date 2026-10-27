"""Data repositories for KASHIF-QA technical registries.

Implements the Repository Pattern for:
- Detectors (HPGe and NaI)
- Instruments (NIM Bin, HV, Amp, MCA, Scope)
- Radioactive Sources (Co-60, Cs-137)

All write operations are atomic, validated via core Pydantic models,
enforce strict RBAC permissions, and write immutable audit log events.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import logging
import math
import re
import sqlite3
from typing import Any, Dict, List, Optional, Union

from kashif.core.decay import half_life_to_days
from kashif.core.models import (
    HPGeDetector,
    Instrument,
    NaIDetector,
    RadioactiveSource,
    SourceIsotope,
)
from kashif.services.audit_service import record_audit_event
from kashif.services.session import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain Exceptions
# ---------------------------------------------------------------------------

class RepositoryError(Exception):
    """قاعدة استثناءات مستودعات البيانات."""
    pass


class DuplicateEntityError(RepositoryError):
    """يُرفع عند انتهاك قيد التفرد مع نص عربي مفهوم."""
    pass


class EntityNotFoundError(RepositoryError):
    """يُرفع عندما يكون السجل المطلوب غير موجود في قاعدة البيانات."""
    pass


def _map_integrity_error(error: sqlite3.IntegrityError) -> DuplicateEntityError:
    """تحويل أخطاء سلامة القيود في SQLite إلى رسائل مجال عربية واضحة."""
    msg = str(error)
    if "detectors.code" in msg:
        return DuplicateEntityError("رمز الكاشف مسجل مسبقاً في المنظومة. يرجى اختيار رمز فريد.")
    if "detectors.manufacturer" in msg and "detectors.serial_number" in msg:
        return DuplicateEntityError("الرقم التسلسلي مكرر لنفس الشركة الصانعة للكاشف.")
    if "instruments.manufacturer" in msg and "instruments.serial_number" in msg:
        return DuplicateEntityError("الرقم التسلسلي مكرر لنفس الشركة الصانعة لجهاز الفحص.")
    if "sources.source_code" in msg:
        return DuplicateEntityError("رمز المصدر المشع مسجل مسبقاً في المنظومة.")
    if "sources.nuclide" in msg and "sources.serial_number" in msg:
        return DuplicateEntityError("الرقم التسلسلي مكرر لنفس النظير المشع.")
    if "nuclides.code" in msg or ("nuclides" in msg and "PRIMARY KEY" in msg):
        return DuplicateEntityError("رمز النظير المشع مسجل مسبقاً في المنظومة.")
    if "source_isotopes" in msg and "UNIQUE" in msg:
        return DuplicateEntityError("لا يمكن تكرار نفس النظير المشع أكثر من مرة في نفس المصدر.")
    if "UNIQUE constraint failed" in msg:
        return DuplicateEntityError("تعذر الحفظ لوجود سجل آخر يحمل نفس البيانات الفريدة.")
    return DuplicateEntityError(f"خطأ في قيود البيانات: {msg}")


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Detector Repository
# ---------------------------------------------------------------------------

class DetectorRepository:
    """مستودع إدارة سجلات كواشف الجرمانيوم ويوديد الصوديوم."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, detector: Union[HPGeDetector, NaIDetector], session: Session) -> int:
        """إضافة كاشف جديد إلى السجل وضمان الذرية مع التدقيق."""
        if not session.can("manage_detectors"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة الكواشف (manage_detectors).")

        # التحقق من صحة النموذج عبر Pydantic
        if isinstance(detector, HPGeDetector):
            d_type = "HPGE"
            crystal_type = detector.crystal_type.value if detector.crystal_type else None
            window_type = detector.window_type.value if detector.window_type else None
            window_status = detector.beryllium_window_status.value if detector.beryllium_window_status else None
            preamp_type = detector.preamp_type.value if detector.preamp_type else None
            polarity = detector.voltage_polarity.value if detector.voltage_polarity else None
            fwhm = detector.nominal_fwhm_kev
            eff = detector.nominal_efficiency_pct
            res = None
        elif isinstance(detector, NaIDetector):
            d_type = "NAI"
            crystal_type = None
            window_type = None
            window_status = None
            preamp_type = None
            polarity = "POSITIVE"
            fwhm = None
            eff = None
            res = detector.nominal_resolution_pct
        else:
            raise TypeError("النموذج يجب أن يكون HPGeDetector أو NaIDetector.")

        now = _now_iso()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO detectors (
                    code, detector_type, manufacturer, model, serial_number,
                    crystal_type, window_type, beryllium_window_status, preamp_type,
                    nominal_voltage, voltage_polarity, nominal_fwhm_kev,
                    nominal_efficiency_pct, nominal_resolution_pct,
                    diameter_inch, height_inch, facility_name, status, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?);
                """,
                (
                    detector.code,
                    d_type,
                    detector.manufacturer,
                    detector.model,
                    detector.serial_number,
                    crystal_type,
                    window_type,
                    window_status,
                    preamp_type,
                    detector.nominal_voltage,
                    polarity,
                    fwhm,
                    eff,
                    res,
                    detector.diameter_inch,
                    detector.height_inch,
                    detector.facility_name,
                    detector.notes,
                    now,
                    now,
                ),
            )
            inserted_id = cursor.lastrowid

            # تسجيل التدقيق بنفس المعاملة
            record_audit_event(
                conn=self.conn,
                action="CREATE",
                user_id=session.user_id,
                entity_type="DETECTOR",
                entity_id=inserted_id,
                new_value=detector.model_dump(mode="json"),
                commit=False,
            )

            self.conn.commit()
            return inserted_id
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to create detector: %s", e)
            raise

    def update(self, detector_id: int, detector: Union[HPGeDetector, NaIDetector], session: Session) -> None:
        """تعديل بيانات كاشف قائم وتسجيل القيمة القديمة والجديدة في التدقيق."""
        if not session.can("manage_detectors"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة الكواشف (manage_detectors).")

        old_row = self.get_by_id(detector_id)
        if not old_row:
            raise EntityNotFoundError(f"الكاشف ذو المعرّف {detector_id} غير موجود.")

        if isinstance(detector, HPGeDetector):
            d_type = "HPGE"
            crystal_type = detector.crystal_type.value if detector.crystal_type else None
            window_type = detector.window_type.value if detector.window_type else None
            window_status = detector.beryllium_window_status.value if detector.beryllium_window_status else None
            preamp_type = detector.preamp_type.value if detector.preamp_type else None
            polarity = detector.voltage_polarity.value if detector.voltage_polarity else None
            fwhm = detector.nominal_fwhm_kev
            eff = detector.nominal_efficiency_pct
            res = None
        elif isinstance(detector, NaIDetector):
            d_type = "NAI"
            crystal_type = None
            window_type = None
            window_status = None
            preamp_type = None
            polarity = "POSITIVE"
            fwhm = None
            eff = None
            res = detector.nominal_resolution_pct
        else:
            raise TypeError("النموذج يجب أن يكون HPGeDetector أو NaIDetector.")

        now = _now_iso()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE detectors SET
                    code = ?, detector_type = ?, manufacturer = ?, model = ?,
                    serial_number = ?, crystal_type = ?, window_type = ?,
                    beryllium_window_status = ?, preamp_type = ?,
                    nominal_voltage = ?, voltage_polarity = ?,
                    nominal_fwhm_kev = ?, nominal_efficiency_pct = ?,
                    nominal_resolution_pct = ?, diameter_inch = ?,
                    height_inch = ?, facility_name = ?, notes = ?, updated_at = ?
                WHERE id = ?;
                """,
                (
                    detector.code,
                    d_type,
                    detector.manufacturer,
                    detector.model,
                    detector.serial_number,
                    crystal_type,
                    window_type,
                    window_status,
                    preamp_type,
                    detector.nominal_voltage,
                    polarity,
                    fwhm,
                    eff,
                    res,
                    detector.diameter_inch,
                    detector.height_inch,
                    detector.facility_name,
                    detector.notes,
                    now,
                    detector_id,
                ),
            )

            record_audit_event(
                conn=self.conn,
                action="UPDATE",
                user_id=session.user_id,
                entity_type="DETECTOR",
                entity_id=detector_id,
                old_value=dict(old_row),
                new_value=detector.model_dump(mode="json"),
                commit=False,
            )

            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to update detector %d: %s", detector_id, e)
            raise

    def retire(self, detector_id: int, session: Session, reason: str = "") -> None:
        """إحالة كاشف للتقاعد (حذف ناعم دون DELETE فعلي لضمان سلامة الأرشيف)."""
        if not session.can("manage_detectors"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة الكواشف (manage_detectors).")

        old_row = self.get_by_id(detector_id)
        if not old_row:
            raise EntityNotFoundError(f"الكاشف ذو المعرّف {detector_id} غير موجود.")

        now = _now_iso()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE detectors SET status = 'RETIRED', updated_at = ? WHERE id = ?;",
                (now, detector_id),
            )

            record_audit_event(
                conn=self.conn,
                action="RETIRE",
                user_id=session.user_id,
                entity_type="DETECTOR",
                entity_id=detector_id,
                old_value={"status": old_row["status"]},
                new_value={"status": "RETIRED", "reason": reason, "retired_at": now},
                commit=False,
            )

            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to retire detector %d: %s", detector_id, e)
            raise

    def get_by_id(self, detector_id: int) -> Optional[Dict[str, Any]]:
        """استرجاع كاشف عبر المعرّف الفريد."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM detectors WHERE id = ?;", (detector_id,))
        row = cursor.fetchone()
        cursor.close()
        return dict(row) if row else None

    def get_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """استرجاع كاشف عبر رمزه المعملي."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM detectors WHERE code = ?;", (code,))
        row = cursor.fetchone()
        cursor.close()
        return dict(row) if row else None

    def list_all(
        self,
        facility: Optional[str] = None,
        include_retired: bool = False,
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """استرجاع قائمة الكواشف مع إمكانية التصفية بالمنشأة والبحث النصي."""
        sql = "SELECT * FROM detectors WHERE 1=1"
        params: List[Any] = []

        if not include_retired:
            sql += " AND status != 'RETIRED'"

        if facility:
            sql += " AND facility_name = ?"
            params.append(facility)

        if query:
            q = f"%{query.strip()}%"
            sql += " AND (code LIKE ? OR manufacturer LIKE ? OR model LIKE ? OR serial_number LIKE ?)"
            params.extend([q, q, q, q])

        sql += " ORDER BY id DESC"

        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Instrument Repository
# ---------------------------------------------------------------------------

class InstrumentRepository:
    """مستودع إدارة سجلات أجهزة الفحص المعملية (NIM, HV, Amp, MCA, Scope)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, instrument: Instrument, session: Session) -> int:
        """إضافة جهاز فحص جديد وضمان الذرية وتسجيل التدقيق."""
        if not session.can("manage_instruments"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة أجهزة الفحص (manage_instruments).")

        if not isinstance(instrument, Instrument):
            raise TypeError("النموذج يجب أن يكون Instrument.")

        now = _now_iso()
        cal_date_str = instrument.cal_date.isoformat() if instrument.cal_date else None
        cal_exp_str = instrument.cal_expiry.isoformat() if instrument.cal_expiry else None

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO instruments (
                    instrument_type, manufacturer, model, serial_number,
                    ripple_mv, regulation_pct, channels_count,
                    shaping_range_min_us, shaping_range_max_us,
                    cal_date, cal_expiry, is_in_service, facility_name,
                    status, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?);
                """,
                (
                    instrument.instrument_type.value,
                    instrument.manufacturer,
                    instrument.model,
                    instrument.serial_number,
                    instrument.ripple_mv,
                    instrument.regulation_pct,
                    instrument.channels_count,
                    instrument.shaping_range_min_us,
                    instrument.shaping_range_max_us,
                    cal_date_str,
                    cal_exp_str,
                    1 if instrument.is_in_service else 0,
                    instrument.facility_name,
                    instrument.notes,
                    now,
                    now,
                ),
            )
            inserted_id = cursor.lastrowid

            record_audit_event(
                conn=self.conn,
                action="CREATE",
                user_id=session.user_id,
                entity_type="INSTRUMENT",
                entity_id=inserted_id,
                new_value=instrument.model_dump(mode="json"),
                commit=False,
            )

            self.conn.commit()
            return inserted_id
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to create instrument: %s", e)
            raise

    def update(self, instrument_id: int, instrument: Instrument, session: Session) -> None:
        """تعديل بيانات جهاز فحص وتسجيل التدقيق."""
        if not session.can("manage_instruments"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة أجهزة الفحص (manage_instruments).")

        old_row = self.get_by_id(instrument_id)
        if not old_row:
            raise EntityNotFoundError(f"جهاز الفحص ذو المعرّف {instrument_id} غير موجود.")

        if not isinstance(instrument, Instrument):
            raise TypeError("النموذج يجب أن يكون Instrument.")

        now = _now_iso()
        cal_date_str = instrument.cal_date.isoformat() if instrument.cal_date else None
        cal_exp_str = instrument.cal_expiry.isoformat() if instrument.cal_expiry else None

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE instruments SET
                    instrument_type = ?, manufacturer = ?, model = ?,
                    serial_number = ?, ripple_mv = ?, regulation_pct = ?,
                    channels_count = ?, shaping_range_min_us = ?,
                    shaping_range_max_us = ?, cal_date = ?, cal_expiry = ?,
                    is_in_service = ?, facility_name = ?, notes = ?, updated_at = ?
                WHERE id = ?;
                """,
                (
                    instrument.instrument_type.value,
                    instrument.manufacturer,
                    instrument.model,
                    instrument.serial_number,
                    instrument.ripple_mv,
                    instrument.regulation_pct,
                    instrument.channels_count,
                    instrument.shaping_range_min_us,
                    instrument.shaping_range_max_us,
                    cal_date_str,
                    cal_exp_str,
                    1 if instrument.is_in_service else 0,
                    instrument.facility_name,
                    instrument.notes,
                    now,
                    instrument_id,
                ),
            )

            record_audit_event(
                conn=self.conn,
                action="UPDATE",
                user_id=session.user_id,
                entity_type="INSTRUMENT",
                entity_id=instrument_id,
                old_value=dict(old_row),
                new_value=instrument.model_dump(mode="json"),
                commit=False,
            )

            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to update instrument %d: %s", instrument_id, e)
            raise

    def retire(self, instrument_id: int, session: Session, reason: str = "") -> None:
        """إحالة جهاز فحص للتقاعد وتعطيل خدمته."""
        if not session.can("manage_instruments"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة أجهزة الفحص (manage_instruments).")

        old_row = self.get_by_id(instrument_id)
        if not old_row:
            raise EntityNotFoundError(f"جهاز الفحص ذو المعرّف {instrument_id} غير موجود.")

        now = _now_iso()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE instruments SET status = 'RETIRED', is_in_service = 0, updated_at = ? WHERE id = ?;",
                (now, instrument_id),
            )

            record_audit_event(
                conn=self.conn,
                action="RETIRE",
                user_id=session.user_id,
                entity_type="INSTRUMENT",
                entity_id=instrument_id,
                old_value={"status": old_row["status"], "is_in_service": old_row["is_in_service"]},
                new_value={"status": "RETIRED", "is_in_service": 0, "reason": reason, "retired_at": now},
                commit=False,
            )

            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to retire instrument %d: %s", instrument_id, e)
            raise

    def get_by_id(self, instrument_id: int) -> Optional[Dict[str, Any]]:
        """استرجاع جهاز فحص عبر المعرّف."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM instruments WHERE id = ?;", (instrument_id,))
        row = cursor.fetchone()
        cursor.close()
        return dict(row) if row else None

    def list_all(
        self,
        facility: Optional[str] = None,
        include_retired: bool = False,
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """استرجاع قائمة أجهزة الفحص مع التصفية."""
        sql = "SELECT * FROM instruments WHERE 1=1"
        params: List[Any] = []

        if not include_retired:
            sql += " AND status != 'RETIRED'"

        if facility:
            sql += " AND facility_name = ?"
            params.append(facility)

        if query:
            q = f"%{query.strip()}%"
            sql += " AND (manufacturer LIKE ? OR model LIKE ? OR serial_number LIKE ? OR instrument_type LIKE ?)"
            params.extend([q, q, q, q])

        sql += " ORDER BY id DESC"

        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Nuclide Repository
# ---------------------------------------------------------------------------

NUCLIDE_CODE_REGEX = re.compile(r"^[A-Z][a-z]?-\d{1,3}[m]?$")


class NuclideRepository:
    """مستودع إدارة سجل النظائر المشعة المرجعية القياسية والمخصصة."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, confirmed_only: bool = False) -> List[Dict[str, Any]]:
        """استرجاع قائمة النظائر المشعة المرجعية."""
        sql = "SELECT * FROM nuclides"
        if confirmed_only:
            sql += " WHERE confirmed = 1"
        sql += " ORDER BY code ASC;"
        cursor = self.conn.cursor()
        cursor.execute(sql)
        rows = cursor.fetchall()
        cursor.close()
        return [dict(r) for r in rows]

    def get_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """استرجاع بيانات نظير مرجعي عبر رمزه (مثل 'Co-60' أو 'Am-241')."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM nuclides WHERE code = ?;", (code.strip(),))
        row = cursor.fetchone()
        cursor.close()
        return dict(row) if row else None

    def is_nuclide_in_use(self, code: str) -> bool:
        """التحقق مما إذا كان النظير مستخدماً في أي مصدر مشع مسجل."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM source_isotopes WHERE nuclide_code = ?;", (code.strip(),))
        count = cursor.fetchone()[0]
        cursor.close()
        return count > 0

    def create_custom_nuclide(
        self,
        code: str,
        name_ar: str,
        name_en: str,
        half_life_value: float,
        half_life_unit: str,
        reference: str,
        session: Session,
    ) -> Dict[str, Any]:
        """إضافة نظير مشع مخصص مع التحقق الصارم والتدقيق."""
        if not session.can("manage_sources"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة المصادر والنظائر (manage_sources).")

        parts = code.strip().split("-", 1)
        if len(parts) == 2:
            code_clean = f"{parts[0].capitalize()}-{parts[1]}"
        else:
            code_clean = code.strip()

        if not NUCLIDE_CODE_REGEX.match(code_clean):
            raise ValueError(
                "رمز النظير غير صالح. يجب أن يبدأ برمز العنصر الكيميائي متبوعاً بشرطة والعدد الكتلي (مثل Co-60 أو Tc-99m)."
            )

        # Case-insensitive duplicate check
        cursor_check = self.conn.cursor()
        cursor_check.execute("SELECT code FROM nuclides WHERE LOWER(code) = LOWER(?);", (code_clean,))
        existing_nuc = cursor_check.fetchone()
        cursor_check.close()
        if existing_nuc:
            raise DuplicateEntityError(f"رمز النظير المشع {code_clean} مسجل مسبقاً في المنظومة.")

        name_ar_clean = name_ar.strip()
        name_en_clean = name_en.strip()
        if not name_ar_clean:
            raise ValueError("الاسم العربي للنظير إلزامي.")
        if not name_en_clean:
            raise ValueError("الاسم الإنجليزي للنظير إلزامي.")

        if half_life_unit not in ("s", "m", "h", "d", "y"):
            raise ValueError(f"وحدة عمر النصف غير صالحة: {half_life_unit}. الوحدات المدعومة: s, m, h, d, y.")

        try:
            val_float = float(half_life_value)
            if val_float <= 0 or not math.isfinite(val_float):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("قيمة عمر النصف يجب أن تكون عدداً موجباً وحقيقياً.")

        ref_clean = reference.strip()
        if not ref_clean:
            raise ValueError("مصدر القيمة (المرجع) إلزامي.")

        half_life_days = half_life_to_days(val_float, half_life_unit)
        now = _now_iso()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO nuclides (
                    code, name_ar, name_en, half_life_days, half_life_value,
                    half_life_unit, reference, confirmed, is_custom, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?);
                """,
                (
                    code_clean,
                    name_ar_clean,
                    name_en_clean,
                    half_life_days,
                    val_float,
                    half_life_unit,
                    ref_clean,
                    now,
                    now,
                ),
            )

            new_record = {
                "code": code_clean,
                "name_ar": name_ar_clean,
                "name_en": name_en_clean,
                "half_life_days": half_life_days,
                "half_life_value": val_float,
                "half_life_unit": half_life_unit,
                "reference": ref_clean,
                "confirmed": 0,
                "is_custom": 1,
                "created_at": now,
                "updated_at": now,
            }

            record_audit_event(
                conn=self.conn,
                action="CREATE",
                user_id=session.user_id,
                entity_type="NUCLIDE",
                entity_id=None,
                new_value=new_record,
                commit=False,
            )

            self.conn.commit()
            return new_record
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to create custom nuclide %s: %s", code_clean, e)
            raise

    def update_custom_nuclide(
        self,
        code: str,
        name_ar: str,
        name_en: str,
        half_life_value: float,
        half_life_unit: str,
        reference: str,
        session: Session,
    ) -> Dict[str, Any]:
        """تعديل نظير مشع مخصص (ممنوع للنظائر القياسية وللنظائر المستخدمة في مصادر)."""
        if not session.can("manage_sources"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة المصادر والنظائر (manage_sources).")

        code_clean = code.strip()
        existing = self.get_by_code(code_clean)
        if not existing:
            raise EntityNotFoundError(f"النظير المشع {code_clean} غير موجود.")

        if not existing["is_custom"]:
            raise PermissionError("لا يمكن تعديل النظائر القياسية المحمية في المنظومة.")

        if self.is_nuclide_in_use(code_clean):
            raise RepositoryError("لا يمكن تعديل النظير لأنه مستخدم حالياً في مصادر مسجلة.")

        name_ar_clean = name_ar.strip()
        name_en_clean = name_en.strip()
        if not name_ar_clean:
            raise ValueError("الاسم العربي للنظير إلزامي.")
        if not name_en_clean:
            raise ValueError("الاسم الإنجليزي للنظير إلزامي.")

        if half_life_unit not in ("s", "m", "h", "d", "y"):
            raise ValueError(f"وحدة عمر النصف غير صالحة: {half_life_unit}.")

        try:
            val_float = float(half_life_value)
            if val_float <= 0 or not math.isfinite(val_float):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("قيمة عمر النصف يجب أن تكون عدداً موجباً وحقيقياً.")

        ref_clean = reference.strip()
        if not ref_clean:
            raise ValueError("مصدر القيمة (المرجع) إلزامي.")

        half_life_days = half_life_to_days(val_float, half_life_unit)
        now = _now_iso()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE nuclides SET
                    name_ar = ?, name_en = ?, half_life_days = ?,
                    half_life_value = ?, half_life_unit = ?, reference = ?,
                    updated_at = ?
                WHERE code = ?;
                """,
                (
                    name_ar_clean,
                    name_en_clean,
                    half_life_days,
                    val_float,
                    half_life_unit,
                    ref_clean,
                    now,
                    code_clean,
                ),
            )

            updated_record = {
                **existing,
                "name_ar": name_ar_clean,
                "name_en": name_en_clean,
                "half_life_days": half_life_days,
                "half_life_value": val_float,
                "half_life_unit": half_life_unit,
                "reference": ref_clean,
                "updated_at": now,
            }

            record_audit_event(
                conn=self.conn,
                action="UPDATE",
                user_id=session.user_id,
                entity_type="NUCLIDE",
                entity_id=None,
                old_value=existing,
                new_value=updated_record,
                commit=False,
            )

            self.conn.commit()
            return updated_record
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to update custom nuclide %s: %s", code_clean, e)
            raise


# ---------------------------------------------------------------------------
# Source Repository
# ---------------------------------------------------------------------------

def _check_source_serial_nuclide_uniqueness(
    cursor: sqlite3.Cursor,
    serial_number: str,
    nuclide_codes: List[str],
    exclude_source_id: Optional[int] = None,
) -> None:
    """التحقق من عدم تكرار الرقم التسلسلي لمصدر آخر يشترك في أي نظير."""
    if not nuclide_codes:
        return
    placeholders = ",".join("?" for _ in nuclide_codes)
    sql = f"""
        SELECT s.id, si.nuclide_code
        FROM sources s
        JOIN source_isotopes si ON s.id = si.source_id
        WHERE s.serial_number = ? AND si.nuclide_code IN ({placeholders})
    """
    params: List[Any] = [serial_number, *nuclide_codes]
    if exclude_source_id is not None:
        sql += " AND s.id != ?"
        params.append(exclude_source_id)

    cursor.execute(sql, params)
    row = cursor.fetchone()
    if row:
        conflict_nuclide = row["nuclide_code"]
        raise DuplicateEntityError(
            f"الرقم التسلسلي '{serial_number}' مكرر لمصدر مشع آخر يحمل نفس النظير ({conflict_nuclide})."
        )


class SourceRepository:
    """مستودع إدارة سجلات المصادر المشعة القياسية والمركبة متعددة النظائر."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, source: RadioactiveSource, session: Session) -> int:
        """إضافة مصدر مشع جديد وضمان الذرية وتسجيل التدقيق."""
        if not session.can("manage_sources"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة المصادر المشعة (manage_sources).")

        if not isinstance(source, RadioactiveSource):
            raise TypeError("النموذج يجب أن يكون RadioactiveSource.")

        now = _now_iso()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            # 1. Check composite uniqueness across shared isotopes
            nuclide_codes = [iso.nuclide_code for iso in source.isotopes]
            _check_source_serial_nuclide_uniqueness(cursor, source.serial_number, nuclide_codes)

            # 2. Insert into sources
            cursor.execute(
                """
                INSERT INTO sources (
                    source_code, serial_number, certificate_number, facility_name,
                    is_active, status, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, 'ACTIVE', ?, ?, ?);
                """,
                (
                    source.source_code,
                    source.serial_number,
                    source.certificate_number,
                    source.facility_name,
                    source.notes,
                    now,
                    now,
                ),
            )
            inserted_id = cursor.lastrowid

            # 3. Insert isotopes
            for iso in source.isotopes:
                iso_date = iso.cert_date or source.cert_date or date.today()
                cursor.execute(
                    """
                    INSERT INTO source_isotopes (
                        source_id, nuclide_code, cert_activity_bq, cert_date
                    ) VALUES (?, ?, ?, ?);
                    """,
                    (
                        inserted_id,
                        iso.nuclide_code,
                        iso.cert_activity_bq,
                        iso_date.isoformat(),
                    ),
                )

            record_audit_event(
                conn=self.conn,
                action="CREATE",
                user_id=session.user_id,
                entity_type="SOURCE",
                entity_id=inserted_id,
                new_value=source.model_dump(mode="json"),
                commit=False,
            )

            self.conn.commit()
            return inserted_id
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to create radioactive source: %s", e)
            raise

    def update(self, source_id: int, source: RadioactiveSource, session: Session) -> None:
        """تعديل بيانات مصدر مشع وتسجيل التدقيق."""
        if not session.can("manage_sources"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة المصادر المشعة (manage_sources).")

        old_row = self.get_by_id(source_id)
        if not old_row:
            raise EntityNotFoundError(f"المصدر المشع ذو المعرّف {source_id} غير موجود.")

        if not isinstance(source, RadioactiveSource):
            raise TypeError("النموذج يجب أن يكون RadioactiveSource.")

        now = _now_iso()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            # 1. Check composite uniqueness across shared isotopes
            nuclide_codes = [iso.nuclide_code for iso in source.isotopes]
            _check_source_serial_nuclide_uniqueness(
                cursor, source.serial_number, nuclide_codes, exclude_source_id=source_id
            )

            # 2. Update sources
            cursor.execute(
                """
                UPDATE sources SET
                    source_code = ?, serial_number = ?, certificate_number = ?,
                    facility_name = ?, notes = ?, updated_at = ?
                WHERE id = ?;
                """,
                (
                    source.source_code,
                    source.serial_number,
                    source.certificate_number,
                    source.facility_name,
                    source.notes,
                    now,
                    source_id,
                ),
            )

            # 3. Replace isotopes
            cursor.execute("DELETE FROM source_isotopes WHERE source_id = ?;", (source_id,))
            for iso in source.isotopes:
                iso_date = iso.cert_date or source.cert_date or date.today()
                cursor.execute(
                    """
                    INSERT INTO source_isotopes (
                        source_id, nuclide_code, cert_activity_bq, cert_date
                    ) VALUES (?, ?, ?, ?);
                    """,
                    (
                        source_id,
                        iso.nuclide_code,
                        iso.cert_activity_bq,
                        iso_date.isoformat(),
                    ),
                )

            record_audit_event(
                conn=self.conn,
                action="UPDATE",
                user_id=session.user_id,
                entity_type="SOURCE",
                entity_id=source_id,
                old_value=dict(old_row),
                new_value=source.model_dump(mode="json"),
                commit=False,
            )

            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to update source %d: %s", source_id, e)
            raise

    def retire(self, source_id: int, session: Session, reason: str = "") -> None:
        """إحالة مصدر مشع للتقاعد وتعطيل نشاطه."""
        if not session.can("manage_sources"):
            raise PermissionError("المستخدم لا يملك صلاحية إدارة المصادر المشعة (manage_sources).")

        old_row = self.get_by_id(source_id)
        if not old_row:
            raise EntityNotFoundError(f"المصدر المشع ذو المعرّف {source_id} غير موجود.")

        now = _now_iso()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "UPDATE sources SET status = 'RETIRED', is_active = 0, updated_at = ? WHERE id = ?;",
                (now, source_id),
            )

            record_audit_event(
                conn=self.conn,
                action="RETIRE",
                user_id=session.user_id,
                entity_type="SOURCE",
                entity_id=source_id,
                old_value={"status": old_row["status"], "is_active": old_row["is_active"]},
                new_value={"status": "RETIRED", "is_active": 0, "reason": reason, "retired_at": now},
                commit=False,
            )

            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to retire source %d: %s", source_id, e)
            raise

    def get_by_id(self, source_id: int) -> Optional[Dict[str, Any]]:
        """استرجاع مصدر عبر المعرّف متضمناً قائمة النظائر."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM sources WHERE id = ?;", (source_id,))
        row = cursor.fetchone()
        if not row:
            cursor.close()
            return None

        res = dict(row)
        cursor.execute(
            """
            SELECT si.nuclide_code, si.cert_activity_bq, si.cert_date,
                   n.half_life_days, n.half_life_value, n.half_life_unit,
                   n.name_ar, n.name_en, n.reference, n.confirmed
            FROM source_isotopes si
            LEFT JOIN nuclides n ON si.nuclide_code = n.code
            WHERE si.source_id = ?
            ORDER BY si.id ASC;
            """,
            (source_id,),
        )
        isotopes = [dict(r) for r in cursor.fetchall()]
        cursor.close()

        res["isotopes"] = isotopes
        if isotopes:
            primary = isotopes[0]
            res["nuclide"] = primary["nuclide_code"]
            res["cert_activity_bq"] = primary["cert_activity_bq"]
            res["cert_date"] = primary["cert_date"]
            res["half_life_days"] = primary["half_life_days"] or 0.0
        else:
            res["nuclide"] = ""
            res["cert_activity_bq"] = 0.0
            res["cert_date"] = ""
            res["half_life_days"] = 0.0

        return res

    def get_by_code(self, source_code: str) -> Optional[Dict[str, Any]]:
        """استرجاع مصدر عبر رمزه المعملي متضمناً قائمة النظائر."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM sources WHERE source_code = ?;", (source_code.strip(),))
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return None
        return self.get_by_id(row["id"])

    def list_all(
        self,
        facility: Optional[str] = None,
        include_retired: bool = False,
        query: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """استرجاع قائمة المصادر المشعة مع التصفية وقائمة النظائر لكل مصدر."""
        sql = "SELECT s.* FROM sources s WHERE 1=1"
        params: List[Any] = []

        if not include_retired:
            sql += " AND s.status != 'RETIRED'"

        if facility:
            sql += " AND s.facility_name = ?"
            params.append(facility)

        if query:
            q = f"%{query.strip()}%"
            sql += """ AND (
                s.source_code LIKE ? OR s.serial_number LIKE ?
                OR EXISTS (
                    SELECT 1 FROM source_isotopes si
                    LEFT JOIN nuclides n ON si.nuclide_code = n.code
                    WHERE si.source_id = s.id AND (
                        si.nuclide_code LIKE ? OR n.name_ar LIKE ? OR n.name_en LIKE ?
                    )
                )
            )"""
            params.extend([q, q, q, q, q])

        sql += " ORDER BY s.id DESC"

        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        source_rows = [dict(r) for r in cursor.fetchall()]

        if not source_rows:
            cursor.close()
            return []

        source_ids = [r["id"] for r in source_rows]
        placeholders = ",".join("?" for _ in source_ids)
        cursor.execute(
            f"""
            SELECT si.source_id, si.nuclide_code, si.cert_activity_bq, si.cert_date,
                   n.half_life_days, n.half_life_value, n.half_life_unit,
                   n.name_ar, n.name_en, n.reference, n.confirmed
            FROM source_isotopes si
            LEFT JOIN nuclides n ON si.nuclide_code = n.code
            WHERE si.source_id IN ({placeholders})
            ORDER BY si.id ASC;
            """,
            source_ids,
        )
        iso_rows = cursor.fetchall()
        cursor.close()

        isotopes_by_source: Dict[int, List[Dict[str, Any]]] = {sid: [] for sid in source_ids}
        for ir in iso_rows:
            d = dict(ir)
            sid = d.pop("source_id")
            isotopes_by_source[sid].append(d)

        for s in source_rows:
            isos = isotopes_by_source.get(s["id"], [])
            s["isotopes"] = isos
            if isos:
                primary = isos[0]
                s["nuclide"] = primary["nuclide_code"]
                s["cert_activity_bq"] = primary["cert_activity_bq"]
                s["cert_date"] = primary["cert_date"]
                s["half_life_days"] = primary["half_life_days"] or 0.0
            else:
                s["nuclide"] = ""
                s["cert_activity_bq"] = 0.0
                s["cert_date"] = ""
                s["half_life_days"] = 0.0

        return source_rows


# ---------------------------------------------------------------------------
# Global Quick Search
# ---------------------------------------------------------------------------

def quick_search(conn: sqlite3.Connection, query: str) -> List[Dict[str, Any]]:
    """بحث سريع وشامل عبر الكواشف وأجهزة الفحص والمصادر بالرقم التسلسلي أو الاسم أو الرمز."""
    results: List[Dict[str, Any]] = []
    q_clean = query.strip()
    if not q_clean:
        return results

    param = f"%{q_clean}%"
    cursor = conn.cursor()

    # 1. Detectors
    cursor.execute(
        """
        SELECT id, code, detector_type, manufacturer, model, serial_number, facility_name, status
        FROM detectors
        WHERE code LIKE ? OR serial_number LIKE ? OR model LIKE ? OR manufacturer LIKE ?
        LIMIT 10;
        """,
        (param, param, param, param),
    )
    for row in cursor.fetchall():
        r = dict(row)
        results.append({
            "entity_type": "detector",
            "id": r["id"],
            "title": f"كاشف: {r['code']} ({r['detector_type']})",
            "subtitle": f"{r['manufacturer']} {r['model']} — S/N: {r['serial_number']}",
            "facility": r["facility_name"] or "—",
            "status": r["status"],
            "raw": r,
        })

    # 2. Instruments
    cursor.execute(
        """
        SELECT id, instrument_type, manufacturer, model, serial_number, facility_name, status, cal_expiry
        FROM instruments
        WHERE serial_number LIKE ? OR model LIKE ? OR manufacturer LIKE ? OR instrument_type LIKE ?
        LIMIT 10;
        """,
        (param, param, param, param),
    )
    for row in cursor.fetchall():
        r = dict(row)
        results.append({
            "entity_type": "instrument",
            "id": r["id"],
            "title": f"جهاز: {r['instrument_type']} ({r['model']})",
            "subtitle": f"{r['manufacturer']} — S/N: {r['serial_number']}",
            "facility": r["facility_name"] or "—",
            "status": r["status"],
            "raw": r,
        })

    # 3. Sources
    cursor.execute(
        """
        SELECT s.id, s.source_code, s.serial_number, s.facility_name, s.status
        FROM sources s
        WHERE s.source_code LIKE ? OR s.serial_number LIKE ?
           OR EXISTS (
               SELECT 1 FROM source_isotopes si
               LEFT JOIN nuclides n ON si.nuclide_code = n.code
               WHERE si.source_id = s.id AND (
                   si.nuclide_code LIKE ? OR n.name_ar LIKE ? OR n.name_en LIKE ?
               )
           )
        LIMIT 10;
        """,
        (param, param, param, param, param),
    )
    for row in cursor.fetchall():
        r = dict(row)
        c2 = conn.cursor()
        c2.execute("SELECT nuclide_code FROM source_isotopes WHERE source_id = ? ORDER BY id ASC;", (r["id"],))
        nucs = [n_row[0] for n_row in c2.fetchall()]
        c2.close()
        nuclides_str = " + ".join(nucs) if nucs else "—"
        results.append({
            "entity_type": "source",
            "id": r["id"],
            "title": f"مصدر: {r['source_code']} ({nuclides_str})",
            "subtitle": f"S/N: {r['serial_number']}",
            "facility": r["facility_name"] or "—",
            "status": r["status"],
            "raw": {**r, "nuclides": nuclides_str},
        })

    cursor.close()
    return results

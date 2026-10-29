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
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Union

from kashif.core.decay import half_life_to_days
from kashif.core.models import (
    HPGeDetector,
    HPGeMeasurement,
    InspectionStatus,
    Instrument,
    NaIDetector,
    SemiconductorType,
    RadioactiveSource,
    SourceIsotope,
    Verdict,
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
    if "inspections.report_number" in msg:
        return DuplicateEntityError("رقم تقرير الفحص مسجل مسبقاً في المنظومة.")
    if "inspection_hpge_measurements.inspection_id" in msg:
        return DuplicateEntityError("تم إدخال قياسات كاشف الجرمانيوم لهذا الفحص مسبقاً.")
    if "inspection_instruments" in msg and "UNIQUE" in msg:
        return DuplicateEntityError("لا يمكن تكرار نفس الجهاز بنفس الدور في نفس الفحص.")
    if "inspection_sources" in msg and "UNIQUE" in msg:
        return DuplicateEntityError("لا يمكن تكرار نفس المصدر المشع بنفس الدور في نفس الفحص.")
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
            if detector.semiconductor_type in (None, SemiconductorType.UNSPECIFIED):
                raise ValueError("نوع البلورة (P_TYPE أو N_TYPE) إلزامي ولا يمكن حفظ الكاشف كـ 'غير محدد'")
            d_type = "HPGE"
            crystal_type = detector.crystal_type.value if detector.crystal_type else None
            semiconductor_type = detector.semiconductor_type.value
            window_type = detector.window_type.value if detector.window_type else None
            window_status = detector.beryllium_window_status.value if detector.beryllium_window_status else None
            preamp_type = detector.preamp_type.value if detector.preamp_type else None
            polarity = detector.voltage_polarity.value if detector.voltage_polarity else None
            certified_polarity = detector.certified_polarity.value if detector.certified_polarity else None
            certificate_ref = detector.certificate_ref
            cooling_hours_cert = detector.cooling_hours_cert
            hv_ramp_max_v_per_s = detector.hv_ramp_max_v_per_s
            hv_ramp_reference = detector.hv_ramp_reference
            fwhm = detector.nominal_fwhm_kev
            eff = detector.nominal_efficiency_pct
            res = None
            pmt_tol = None
        elif isinstance(detector, NaIDetector):
            d_type = "NAI"
            crystal_type = None
            semiconductor_type = None
            window_type = None
            window_status = None
            preamp_type = None
            polarity = "POSITIVE"
            certified_polarity = None
            certificate_ref = None
            cooling_hours_cert = None
            hv_ramp_max_v_per_s = 10.0
            hv_ramp_reference = None
            fwhm = None
            eff = None
            res = detector.nominal_resolution_pct
            pmt_tol = detector.pmt_voltage_tolerance_pct
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
                    semiconductor_type, certified_polarity, certificate_ref,
                    cooling_hours_cert, hv_ramp_max_v_per_s, hv_ramp_reference,
                    pmt_voltage_tolerance_pct, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
                    semiconductor_type,
                    certified_polarity,
                    certificate_ref,
                    cooling_hours_cert,
                    hv_ramp_max_v_per_s,
                    hv_ramp_reference,
                    pmt_tol,
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
            if detector.semiconductor_type in (None, SemiconductorType.UNSPECIFIED):
                raise ValueError("نوع البلورة (P_TYPE أو N_TYPE) إلزامي ولا يمكن حفظ الكاشف كـ 'غير محدد'")
            d_type = "HPGE"
            crystal_type = detector.crystal_type.value if detector.crystal_type else None
            semiconductor_type = detector.semiconductor_type.value
            window_type = detector.window_type.value if detector.window_type else None
            window_status = detector.beryllium_window_status.value if detector.beryllium_window_status else None
            preamp_type = detector.preamp_type.value if detector.preamp_type else None
            polarity = detector.voltage_polarity.value if detector.voltage_polarity else None
            certified_polarity = detector.certified_polarity.value if detector.certified_polarity else None
            certificate_ref = detector.certificate_ref
            cooling_hours_cert = detector.cooling_hours_cert
            hv_ramp_max_v_per_s = detector.hv_ramp_max_v_per_s
            hv_ramp_reference = detector.hv_ramp_reference
            fwhm = detector.nominal_fwhm_kev
            eff = detector.nominal_efficiency_pct
            res = None
            pmt_tol = None
        elif isinstance(detector, NaIDetector):
            d_type = "NAI"
            crystal_type = None
            semiconductor_type = None
            window_type = None
            window_status = None
            preamp_type = None
            polarity = "POSITIVE"
            certified_polarity = None
            certificate_ref = None
            cooling_hours_cert = None
            hv_ramp_max_v_per_s = 10.0
            hv_ramp_reference = None
            fwhm = None
            eff = None
            res = detector.nominal_resolution_pct
            pmt_tol = detector.pmt_voltage_tolerance_pct
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
                    height_inch = ?, facility_name = ?, notes = ?,
                    semiconductor_type = ?, certified_polarity = ?,
                    certificate_ref = ?, cooling_hours_cert = ?,
                    hv_ramp_max_v_per_s = ?, hv_ramp_reference = ?,
                    pmt_voltage_tolerance_pct = ?, updated_at = ?
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
                    semiconductor_type,
                    certified_polarity,
                    certificate_ref,
                    cooling_hours_cert,
                    hv_ramp_max_v_per_s,
                    hv_ramp_reference,
                    pmt_tol,
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
        half_life_uncertainty: Optional[float] = None,
        reference_url: Optional[str] = None,
        reference_accessed: Optional[str] = None,
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
                    half_life_unit, reference, confirmed, is_custom,
                    half_life_uncertainty, reference_url, reference_accessed,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?, ?, ?);
                """,
                (
                    code_clean,
                    name_ar_clean,
                    name_en_clean,
                    half_life_days,
                    val_float,
                    half_life_unit,
                    ref_clean,
                    half_life_uncertainty,
                    reference_url,
                    reference_accessed,
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
                "half_life_uncertainty": half_life_uncertainty,
                "reference_url": reference_url,
                "reference_accessed": reference_accessed,
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
        half_life_uncertainty: Optional[float] = None,
        reference_url: Optional[str] = None,
        reference_accessed: Optional[str] = None,
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
            uncert_val = half_life_uncertainty if half_life_uncertainty is not None else existing.get("half_life_uncertainty")
            url_val = reference_url if reference_url is not None else existing.get("reference_url")
            acc_val = reference_accessed if reference_accessed is not None else existing.get("reference_accessed")

            cursor.execute(
                """
                UPDATE nuclides SET
                    name_ar = ?, name_en = ?, half_life_days = ?,
                    half_life_value = ?, half_life_unit = ?, reference = ?,
                    half_life_uncertainty = ?, reference_url = ?, reference_accessed = ?,
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
                    uncert_val,
                    url_val,
                    acc_val,
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
                "half_life_uncertainty": uncert_val,
                "reference_url": url_val,
                "reference_accessed": acc_val,
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


# ---------------------------------------------------------------------------
# Inspection Repository
# ---------------------------------------------------------------------------

class InspectionRepository:
    """مستودع إدارة فحوصات كواشف الجرمانيوم وسجلات القياس والقمم والملحقات."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _get_inspection_row(self, inspection_id: int) -> sqlite3.Row:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM inspections WHERE id = ?;", (inspection_id,))
        row = cursor.fetchone()
        if not row:
            raise EntityNotFoundError(f"تقرير الفحص رقم {inspection_id} غير موجود في المنظومة.")
        return row

    def _check_not_approved(self, inspection_id: int) -> sqlite3.Row:
        row = self._get_inspection_row(inspection_id)
        if row["status"] == "APPROVED":
            raise RepositoryError("لا يمكن تعديل فحص معتمد (السجل مجمد نهائياً وفق القاعدة RULE-SEC-03).")
        return row

    def _check_modifiable_by_owner(self, inspection_id: int, session: Session) -> sqlite3.Row:
        row = self._check_not_approved(inspection_id)
        if row["technician_id"] != session.user_id:
            raise PermissionError("لا يمكن تعديل بيانات الفحص إلا من قبل الفني صاحب الفحص.")
        return row

    def create_draft(self, detector_id: int, session: Session, inspection_date: Optional[str] = None) -> int:
        """إنشاء مسودة فحص فني جديدة لكاشف وضمان تسجيل التدقيق."""
        if not (session.can("conduct_inspection") or session.can("save_draft")):
            raise PermissionError("المستخدم لا يملك صلاحية إنشاء فحص جديد (conduct_inspection).")

        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM detectors WHERE id = ?;", (detector_id,))
        if not cursor.fetchone():
            raise EntityNotFoundError(f"الكاشف رقم {detector_id} غير موجود في المنظومة.")

        insp_date = inspection_date or date.today().isoformat()
        now = _now_iso()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        try:
            cursor.execute(
                """
                INSERT INTO inspections (
                    detector_id, technician_id, inspection_date, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'DRAFT', ?, ?);
                """,
                (detector_id, session.user_id, insp_date, now, now),
            )
            inserted_id = cursor.lastrowid

            record_audit_event(
                conn=self.conn,
                action="CREATE",
                user_id=session.user_id,
                entity_type="INSPECTION",
                entity_id=inserted_id,
                new_value={"detector_id": detector_id, "inspection_date": insp_date, "status": "DRAFT"},
                commit=False,
            )
            self.conn.commit()
            return inserted_id
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to create inspection draft: %s", e)
            raise

    def get_by_id(self, inspection_id: int) -> Optional[Dict[str, Any]]:
        """جلب بيانات الفحص الأساسية بمعرف الفحص."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM inspections WHERE id = ?;", (inspection_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_inspection_full(self, inspection_id: int) -> Dict[str, Any]:
        """جلب تقرير الفحص كاملاً بكافة قياساته وقممه وأجهزته ومصادره ومرفقاته وتاريخ إعادته."""
        insp_row = self._get_inspection_row(inspection_id)
        cursor = self.conn.cursor()

        # الكاشف
        cursor.execute("SELECT * FROM detectors WHERE id = ?;", (insp_row["detector_id"],))
        det_row = cursor.fetchone()

        # قياسات HPGe
        cursor.execute("SELECT * FROM inspection_hpge_measurements WHERE inspection_id = ?;", (inspection_id,))
        hpge_row = cursor.fetchone()

        # القمم
        cursor.execute("SELECT * FROM inspection_peaks WHERE inspection_id = ? ORDER BY id ASC;", (inspection_id,))
        peaks = [dict(r) for r in cursor.fetchall()]

        # الأجهزة المستخدمة
        cursor.execute(
            """
            SELECT ii.*, i.instrument_type, i.manufacturer, i.model, i.serial_number, i.cal_expiry
            FROM inspection_instruments ii
            JOIN instruments i ON ii.instrument_id = i.id
            WHERE ii.inspection_id = ?
            ORDER BY ii.id ASC;
            """,
            (inspection_id,),
        )
        instruments = [dict(r) for r in cursor.fetchall()]

        # المصادر المستخدمة
        cursor.execute(
            """
            SELECT isrc.*, s.source_code, s.serial_number, s.certificate_number, s.facility_name
            FROM inspection_sources isrc
            JOIN sources s ON isrc.source_id = s.id
            WHERE isrc.inspection_id = ?
            ORDER BY isrc.id ASC;
            """,
            (inspection_id,),
        )
        sources = [dict(r) for r in cursor.fetchall()]

        # المرفقات
        cursor.execute("SELECT * FROM attachments WHERE inspection_id = ? ORDER BY id ASC;", (inspection_id,))
        attachments = [dict(r) for r in cursor.fetchall()]

        # محاولات الإعادة
        cursor.execute(
            """
            SELECT * FROM measurement_retries
            WHERE inspection_id = ?
            ORDER BY stage_number ASC, attempt_number ASC;
            """,
            (inspection_id,),
        )
        retries = [dict(r) for r in cursor.fetchall()]

        # الفني القائم بالفحص
        cursor.execute("SELECT id, username, full_name, role FROM users WHERE id = ?;", (insp_row["technician_id"],))
        tech_row = cursor.fetchone()

        # المعتمد
        app_row = None
        if insp_row["approver_id"]:
            cursor.execute("SELECT id, username, full_name, role FROM users WHERE id = ?;", (insp_row["approver_id"],))
            app_row = cursor.fetchone()

        return {
            "inspection": dict(insp_row),
            "detector": dict(det_row) if det_row else None,
            "hpge_measurements": dict(hpge_row) if hpge_row else None,
            "peaks": peaks,
            "instruments": instruments,
            "sources": sources,
            "attachments": attachments,
            "retries": retries,
            "technician": dict(tech_row) if tech_row else None,
            "approver": dict(app_row) if app_row else None,
        }

    def save_hpge_measurements(
        self,
        inspection_id: int,
        measurements: Union[HPGeMeasurement, Dict[str, Any]],
        session: Session,
    ) -> None:
        """حفظ أو تحديث قياسات فحص كاشف الجرمانيوم مع التحقق من الصلاحيات والتجميد والتدقيق."""
        if not (session.can("conduct_inspection") or session.can("input_measurements") or session.can("save_draft")):
            raise PermissionError("المستخدم لا يملك صلاحية حفظ قياسات الفحص.")

        self._check_modifiable_by_owner(inspection_id, session)

        if isinstance(measurements, HPGeMeasurement):
            data = measurements.model_dump(mode="json")
        else:
            data = dict(measurements)

        cooling_hours = data.get("cooling_hours")
        cryostat_intact = int(bool(data["cryostat_intact"])) if data.get("cryostat_intact") is not None else None

        beryllium_window_status = data.get("beryllium_window_status")
        if hasattr(beryllium_window_status, "value"):
            beryllium_window_status = beryllium_window_status.value

        vacuum_intact = int(bool(data["vacuum_intact"])) if data.get("vacuum_intact") is not None else None
        cryostat_wet_or_frozen = int(bool(data["cryostat_wet_or_frozen"])) if data.get("cryostat_wet_or_frozen") is not None else None

        v_pos_24 = data.get("v_pos_24")
        v_neg_24 = data.get("v_neg_24")
        v_pos_12 = data.get("v_pos_12")
        v_neg_12 = data.get("v_neg_12")
        v_pos_6 = data.get("v_pos_6")
        v_neg_6 = data.get("v_neg_6")

        applied_voltage = data.get("applied_voltage")
        applied_polarity = data.get("applied_polarity")
        if hasattr(applied_polarity, "value"):
            applied_polarity = applied_polarity.value

        hv_ramp_source = data.get("hv_ramp_source") or "MANUAL"
        hv_ramp_rate = data.get("hv_ramp_rate_v_per_s")
        hv_ramp_rate_v_per_s = float(hv_ramp_rate) if hv_ramp_rate is not None else 10.0

        preamp_type = data.get("preamp_type")
        if hasattr(preamp_type, "value"):
            preamp_type = preamp_type.value

        has_preamp_signal = int(bool(data["has_preamp_signal"])) if data.get("has_preamp_signal") is not None else None
        shaping_time_us = data.get("shaping_time_us")
        coarse_gain = data.get("coarse_gain")
        fine_gain = data.get("fine_gain")
        baseline_restorer = data.get("baseline_restorer")
        pole_zero_setting = data.get("pole_zero_setting")
        has_pz_distortion = int(bool(data["has_pz_distortion"])) if data.get("has_pz_distortion") is not None else None
        amp_output_amplitude = data.get("amp_output_amplitude")

        total_count_rate_cps = data.get("total_count_rate_cps")
        if total_count_rate_cps is None:
            total_count_rate_cps = data.get("total_cps")

        dead_time_pct = data.get("dead_time_pct")
        calibration_slope_m = data.get("calibration_slope_m")
        calibration_intercept_b = data.get("calibration_intercept_b")

        measured_fwhm_kev = data.get("measured_fwhm_kev")
        if measured_fwhm_kev is None:
            measured_fwhm_kev = data.get("fwhm_measured_kev")

        measured_fwtm_kev = data.get("measured_fwtm_kev")
        if measured_fwtm_kev is None:
            measured_fwtm_kev = data.get("fwtm_measured_kev")

        form_factor = data.get("form_factor")
        if form_factor is None:
            form_factor = data.get("form_factor_measured")

        net_counts_1332 = data.get("net_counts_1332")
        efficiency_method = data.get("efficiency_method")
        live_time_sec = data.get("live_time_sec")

        relative_efficiency_pct = data.get("relative_efficiency_pct")
        if relative_efficiency_pct is None:
            relative_efficiency_pct = data.get("eff_measured_pct")

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            now = _now_iso()
            cursor.execute("SELECT id FROM inspection_hpge_measurements WHERE inspection_id = ?;", (inspection_id,))
            existing = cursor.fetchone()
            if existing:
                cursor.execute(
                    """
                    UPDATE inspection_hpge_measurements SET
                        cooling_hours = ?, cryostat_intact = ?, beryllium_window_status = ?,
                        vacuum_intact = ?, cryostat_wet_or_frozen = ?,
                        v_pos_24 = ?, v_neg_24 = ?, v_pos_12 = ?, v_neg_12 = ?,
                        v_pos_6 = ?, v_neg_6 = ?, applied_voltage = ?, applied_polarity = ?,
                        hv_ramp_source = ?, hv_ramp_rate_v_per_s = ?, preamp_type = ?,
                        has_preamp_signal = ?, shaping_time_us = ?, coarse_gain = ?, fine_gain = ?,
                        baseline_restorer = ?, pole_zero_setting = ?, has_pz_distortion = ?,
                        amp_output_amplitude = ?, total_count_rate_cps = ?, dead_time_pct = ?,
                        calibration_slope_m = ?, calibration_intercept_b = ?,
                        measured_fwhm_kev = ?, measured_fwtm_kev = ?, form_factor = ?,
                        net_counts_1332 = ?, efficiency_method = ?, live_time_sec = ?,
                        relative_efficiency_pct = ?, updated_at = ?
                    WHERE inspection_id = ?;
                    """,
                    (
                        cooling_hours, cryostat_intact, beryllium_window_status,
                        vacuum_intact, cryostat_wet_or_frozen,
                        v_pos_24, v_neg_24, v_pos_12, v_neg_12,
                        v_pos_6, v_neg_6, applied_voltage, applied_polarity,
                        hv_ramp_source, hv_ramp_rate_v_per_s, preamp_type,
                        has_preamp_signal, shaping_time_us, coarse_gain, fine_gain,
                        baseline_restorer, pole_zero_setting, has_pz_distortion,
                        amp_output_amplitude, total_count_rate_cps, dead_time_pct,
                        calibration_slope_m, calibration_intercept_b,
                        measured_fwhm_kev, measured_fwtm_kev, form_factor,
                        net_counts_1332, efficiency_method, live_time_sec,
                        relative_efficiency_pct, now, inspection_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO inspection_hpge_measurements (
                        inspection_id, cooling_hours, cryostat_intact, beryllium_window_status,
                        vacuum_intact, cryostat_wet_or_frozen,
                        v_pos_24, v_neg_24, v_pos_12, v_neg_12,
                        v_pos_6, v_neg_6, applied_voltage, applied_polarity,
                        hv_ramp_source, hv_ramp_rate_v_per_s, preamp_type,
                        has_preamp_signal, shaping_time_us, coarse_gain, fine_gain,
                        baseline_restorer, pole_zero_setting, has_pz_distortion,
                        amp_output_amplitude, total_count_rate_cps, dead_time_pct,
                        calibration_slope_m, calibration_intercept_b,
                        measured_fwhm_kev, measured_fwtm_kev, form_factor,
                        net_counts_1332, efficiency_method, live_time_sec,
                        relative_efficiency_pct, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?
                    );
                    """,
                    (
                        inspection_id, cooling_hours, cryostat_intact, beryllium_window_status,
                        vacuum_intact, cryostat_wet_or_frozen,
                        v_pos_24, v_neg_24, v_pos_12, v_neg_12,
                        v_pos_6, v_neg_6, applied_voltage, applied_polarity,
                        hv_ramp_source, hv_ramp_rate_v_per_s, preamp_type,
                        has_preamp_signal, shaping_time_us, coarse_gain, fine_gain,
                        baseline_restorer, pole_zero_setting, has_pz_distortion,
                        amp_output_amplitude, total_count_rate_cps, dead_time_pct,
                        calibration_slope_m, calibration_intercept_b,
                        measured_fwhm_kev, measured_fwtm_kev, form_factor,
                        net_counts_1332, efficiency_method, live_time_sec,
                        relative_efficiency_pct, now, now,
                    ),
                )

            cursor.execute("UPDATE inspections SET updated_at = ? WHERE id = ?;", (now, inspection_id))

            record_audit_event(
                conn=self.conn,
                action="UPDATE",
                user_id=session.user_id,
                entity_type="INSPECTION_HPGE_MEASUREMENT",
                entity_id=inspection_id,
                new_value=data,
                commit=False,
            )
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to save HPGe measurements for inspection %s: %s", inspection_id, e)
            raise

    def save_peaks(self, inspection_id: int, peaks: List[Dict[str, Any]], session: Session) -> None:
        """حفظ أو تحديث قائمة القمم لطيف الفحص الفني."""
        if not (session.can("conduct_inspection") or session.can("input_measurements") or session.can("save_draft")):
            raise PermissionError("المستخدم لا يملك صلاحية حفظ قمم الفحص.")

        self._check_modifiable_by_owner(inspection_id, session)

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            now = _now_iso()
            cursor.execute("SELECT * FROM inspection_peaks WHERE inspection_id = ?;", (inspection_id,))
            old_peaks = [dict(r) for r in cursor.fetchall()]

            cursor.execute("DELETE FROM inspection_peaks WHERE inspection_id = ?;", (inspection_id,))

            for p in peaks:
                cursor.execute(
                    """
                    INSERT INTO inspection_peaks (
                        inspection_id, detector_type, peak_name, energy_reference_kev,
                        centroid_channel, fwhm_channels, fwhm_kev, fwtm_channels, fwtm_kev,
                        net_area_counts, live_time_sec, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        inspection_id,
                        p.get("detector_type", "HPGE"),
                        p["peak_name"],
                        p["energy_reference_kev"],
                        p.get("centroid_channel"),
                        p.get("fwhm_channels"),
                        p.get("fwhm_kev"),
                        p.get("fwtm_channels"),
                        p.get("fwtm_kev"),
                        p.get("net_area_counts"),
                        p.get("live_time_sec"),
                        now,
                    ),
                )

            cursor.execute("UPDATE inspections SET updated_at = ? WHERE id = ?;", (now, inspection_id))

            record_audit_event(
                conn=self.conn,
                action="UPDATE",
                user_id=session.user_id,
                entity_type="INSPECTION_PEAKS",
                entity_id=inspection_id,
                old_value=old_peaks,
                new_value=peaks,
                commit=False,
            )
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to save peaks for inspection %s: %s", inspection_id, e)
            raise

    def link_instruments(self, inspection_id: int, instruments: List[Dict[str, Any]], session: Session) -> None:
        """ربط أجهزة القياس المستخدمة في الفحص (NIM Bin, HV Supply, Preamp, Amp, MCA)."""
        if not (session.can("conduct_inspection") or session.can("save_draft")):
            raise PermissionError("المستخدم لا يملك صلاحية ربط الأجهزة بالفحص.")

        self._check_modifiable_by_owner(inspection_id, session)

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            now = _now_iso()
            cursor.execute("SELECT * FROM inspection_instruments WHERE inspection_id = ?;", (inspection_id,))
            old_links = [dict(r) for r in cursor.fetchall()]

            cursor.execute("DELETE FROM inspection_instruments WHERE inspection_id = ?;", (inspection_id,))

            for inst in instruments:
                cursor.execute(
                    """
                    INSERT INTO inspection_instruments (
                        inspection_id, instrument_id, instrument_role, notes, created_at
                    ) VALUES (?, ?, ?, ?, ?);
                    """,
                    (
                        inspection_id,
                        inst["instrument_id"],
                        inst["instrument_role"],
                        inst.get("notes"),
                        now,
                    ),
                )

            cursor.execute("UPDATE inspections SET updated_at = ? WHERE id = ?;", (now, inspection_id))

            record_audit_event(
                conn=self.conn,
                action="UPDATE",
                user_id=session.user_id,
                entity_type="INSPECTION_INSTRUMENTS",
                entity_id=inspection_id,
                old_value=old_links,
                new_value=instruments,
                commit=False,
            )
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to link instruments for inspection %s: %s", inspection_id, e)
            raise

    def link_sources(self, inspection_id: int, sources: List[Dict[str, Any]], session: Session) -> None:
        """ربط المصادر المشعة المستخدمة في الفحص (Co-60, Cs-137)."""
        if not (session.can("conduct_inspection") or session.can("save_draft")):
            raise PermissionError("المستخدم لا يملك صلاحية ربط المصادر بالفحص.")

        self._check_modifiable_by_owner(inspection_id, session)

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            now = _now_iso()
            cursor.execute("SELECT * FROM inspection_sources WHERE inspection_id = ?;", (inspection_id,))
            old_links = [dict(r) for r in cursor.fetchall()]

            cursor.execute("DELETE FROM inspection_sources WHERE inspection_id = ?;", (inspection_id,))

            for src in sources:
                cursor.execute(
                    """
                    INSERT INTO inspection_sources (
                        inspection_id, source_id, source_role, distance_cm, decay_corrected_activity_bq, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        inspection_id,
                        src["source_id"],
                        src["source_role"],
                        src.get("distance_cm"),
                        src.get("decay_corrected_activity_bq"),
                        now,
                    ),
                )

            cursor.execute("UPDATE inspections SET updated_at = ? WHERE id = ?;", (now, inspection_id))

            record_audit_event(
                conn=self.conn,
                action="UPDATE",
                user_id=session.user_id,
                entity_type="INSPECTION_SOURCES",
                entity_id=inspection_id,
                old_value=old_links,
                new_value=sources,
                commit=False,
            )
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to link sources for inspection %s: %s", inspection_id, e)
            raise

    def add_attachment(
        self,
        inspection_id: int,
        file_path: str,
        file_type: str,
        caption: Optional[str],
        session: Session,
    ) -> int:
        """إضافة مرفق تقني للفحص وحساب بصمة SHA-256 للملف."""
        if not (session.can("conduct_inspection") or session.can("save_draft")):
            raise PermissionError("المستخدم لا يملك صلاحية إضافة مرفقات للفحص.")

        self._check_modifiable_by_owner(inspection_id, session)

        valid_types = ("PREAMP_WAVEFORM", "AMP_WAVEFORM", "SPECTRUM_PLOT", "OTHER")
        if file_type not in valid_types:
            raise ValueError(f"نوع المرفق غير صالح ({file_type}). الأنواع المتاحة: {valid_types}")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"الملف المرفق غير موجود على القرص: {file_path}")

        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha.update(chunk)
        file_hash = sha.hexdigest()

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            now = _now_iso()
            cursor.execute(
                """
                INSERT INTO attachments (
                    inspection_id, file_path, file_hash_sha256, file_type, caption, created_at
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (inspection_id, file_path, file_hash, file_type, caption, now),
            )
            att_id = cursor.lastrowid
            cursor.execute("UPDATE inspections SET updated_at = ? WHERE id = ?;", (now, inspection_id))

            record_audit_event(
                conn=self.conn,
                action="CREATE",
                user_id=session.user_id,
                entity_type="ATTACHMENT",
                entity_id=att_id,
                new_value={
                    "inspection_id": inspection_id,
                    "file_path": file_path,
                    "file_hash_sha256": file_hash,
                    "file_type": file_type,
                    "caption": caption,
                },
                commit=False,
            )
            self.conn.commit()
            return att_id
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to add attachment for inspection %s: %s", inspection_id, e)
            raise

    def record_retry(
        self,
        inspection_id: int,
        stage_number: int,
        reason_for_retry: str,
        corrective_action_taken: str,
        previous_values: Optional[Any],
        new_values: Optional[Any],
        session: Session,
    ) -> int:
        """تسجيل محاولة إعادة قياس مرحلة مع حفظ القيم السابقة والجديدة."""
        if not (session.can("conduct_inspection") or session.can("save_draft")):
            raise PermissionError("المستخدم لا يملك صلاحية تسجيل محاولة إعادة قياس.")

        self._check_modifiable_by_owner(inspection_id, session)

        prev_json = json.dumps(previous_values, ensure_ascii=False) if previous_values is not None else "{}"
        new_json = json.dumps(new_values, ensure_ascii=False) if new_values is not None else "{}"

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            now = _now_iso()
            cursor.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM measurement_retries WHERE inspection_id = ? AND stage_number = ?;",
                (inspection_id, stage_number),
            )
            attempt_number = cursor.fetchone()[0]

            cursor.execute(
                """
                INSERT INTO measurement_retries (
                    inspection_id, stage_number, attempt_number, reason_for_retry,
                    corrective_action_taken, previous_values_json, new_values_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    inspection_id,
                    stage_number,
                    attempt_number,
                    reason_for_retry,
                    corrective_action_taken,
                    prev_json,
                    new_json,
                    now,
                ),
            )
            retry_id = cursor.lastrowid
            cursor.execute("UPDATE inspections SET updated_at = ? WHERE id = ?;", (now, inspection_id))

            record_audit_event(
                conn=self.conn,
                action="CREATE",
                user_id=session.user_id,
                entity_type="MEASUREMENT_RETRY",
                entity_id=retry_id,
                new_value={
                    "inspection_id": inspection_id,
                    "stage_number": stage_number,
                    "attempt_number": attempt_number,
                    "reason_for_retry": reason_for_retry,
                },
                commit=False,
            )
            self.conn.commit()
            return retry_id
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to record retry for inspection %s: %s", inspection_id, e)
            raise

    def list_by_detector(self, detector_id: int) -> List[Dict[str, Any]]:
        """جلب جميع الفحوصات الفنية المرتبطة بكاشف محدد، مرتبة من الأحدث إلى الأقدم."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM inspections
            WHERE detector_id = ?
            ORDER BY inspection_date DESC, id DESC;
            """,
            (detector_id,),
        )
        return [dict(r) for r in cursor.fetchall()]

    def update_inspection_record(
        self,
        inspection_id: int,
        updates: Dict[str, Any],
        session: Session,
        action: str = "UPDATE",
    ) -> None:
        """تحديث حقول الفحص الأساسية وتسجيل الحدث في التدقيق (مخصص لخدمة إدارة دورة الحياة)."""
        current = self._get_inspection_row(inspection_id)
        if current["status"] == "APPROVED" and action != "APPROVE":
            raise RepositoryError("لا يمكن تعديل فحص معتمد (السجل مجمد نهائياً وفق القاعدة RULE-SEC-03).")

        allowed_fields = {
            "status", "verdict", "report_number", "approver_id",
            "inspection_date", "rejection_reason", "manager_notes",
            "technician_signature_token", "manager_signature_token",
            "approved_at", "superseded_by_id", "snapshot_json",
        }
        filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}
        if not filtered_updates:
            return

        now = _now_iso()
        filtered_updates["updated_at"] = now

        set_clauses = [f"{k} = ?" for k in filtered_updates.keys()]
        values = list(filtered_updates.values())
        values.append(inspection_id)

        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        cursor = self.conn.cursor()
        try:
            sql = f"UPDATE inspections SET {', '.join(set_clauses)} WHERE id = ?;"
            cursor.execute(sql, values)

            record_audit_event(
                conn=self.conn,
                action=action,
                user_id=session.user_id,
                entity_type="INSPECTION",
                entity_id=inspection_id,
                old_value=dict(current),
                new_value=filtered_updates,
                commit=False,
            )
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise _map_integrity_error(e) from e
        except Exception as e:
            self.conn.rollback()
            logger.error("Failed to update inspection %s: %s", inspection_id, e)
            raise

"""Inspection workflow, governance rules enforcement, and HPGe evaluation service for KASHIF-QA.

Implements:
- Inspection lifecycle: DRAFT -> PENDING_APPROVAL -> APPROVED / REJECTED
- Separation of duties and governance enforcement:
  * RULE-SEC-01: Technician cannot approve own inspection
  * RULE-SEC-02: Technical ADMIN cannot approve inspections
  * RULE-SEC-03: Approved records are permanently immutable
- Sequential report number generation (KASHIF-HPGE-YYYY-NNNN)
- HPGe rules evaluation (R-HPGE) and verdict aggregation (IAEA MRNI-507)
- Canonical frozen JSON snapshot on approval
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional, Union

from kashif.core.models import (
    HPGeMeasurement,
    InspectionStatus,
    Verdict,
    VoltagePolarity,
    WindowStatus,
)
from kashif.core.rules import (
    RuleResult,
    SystemThresholds,
    aggregate_verdicts,
    eval_r_hpge_01_window_integrity,
    eval_r_hpge_02_window_scratch,
    eval_r_hpge_03_cooling_time,
    eval_r_hpge_04_cryostat_vacuum,
    eval_r_hpge_05_nim_voltages,
    eval_r_hpge_06_hv_polarity,
    eval_r_hpge_07_hv_ramp_rate,
    eval_r_hpge_08_preamp_signal,
    eval_r_hpge_09_pole_zero,
    eval_r_hpge_10_mca_rates,
    eval_r_hpge_11_peak_counts,
    eval_r_hpge_12_fwhm,
    eval_r_hpge_13_14_form_factor,
    eval_r_hpge_15_efficiency,
    eval_rule_sec_01_separation_of_duties,
    eval_rule_sec_02_admin_approval_prohibition,
    eval_rule_sec_03_immutable_approved_record,
)
from kashif.db.repositories import EntityNotFoundError, InspectionRepository
from kashif.services.session import Session

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InspectionWorkflowError(Exception):
    """قاعدة استثناءات مسار عمل الفحوصات الفنية."""
    pass


class InvalidStateTransitionError(InspectionWorkflowError):
    """يُرفع عند محاولة انتقال غير نظامي بين حالات الفحص."""
    pass


class SecurityViolationError(InspectionWorkflowError):
    """يُرفع عند انتهاك قواعد الأمان وفصل المهام (SEC-01, SEC-02, SEC-03)."""
    pass


# ---------------------------------------------------------------------------
# Report Number Generator
# ---------------------------------------------------------------------------

def generate_report_number(
    conn: sqlite3.Connection,
    detector_type: str = "HPGE",
    year: Optional[int] = None,
) -> str:
    """توليد رقم تقرير فني تسلسلي فريد وفق النمط الرسمي: KASHIF-HPGE-YYYY-NNNN.
    
    يضمن التسلسل الصارم والفرادة داخل نطاق السنة ونوع الكاشف.
    """
    if year is None:
        year = datetime.now(timezone.utc).year

    prefix = f"KASHIF-{detector_type.upper()}-{year}-"
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT report_number FROM inspections
        WHERE report_number LIKE ?
        ORDER BY report_number DESC
        LIMIT 1;
        """,
        (f"{prefix}%",),
    )
    row = cursor.fetchone()
    if row and row[0]:
        last_str = str(row[0]).strip()
        try:
            seq = int(last_str.split("-")[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    else:
        seq = 1

    return f"{prefix}{seq:04d}"


# ---------------------------------------------------------------------------
# Frozen Canonical Snapshot
# ---------------------------------------------------------------------------

def build_inspection_snapshot(
    inspection_full: Dict[str, Any],
    approver_session: Session,
    verdict: str,
    report_number: str,
) -> str:
    """توليد لقطة JSON حتمية الترتيب ومجمدة لكافة بيانات الفحص الفني عند الاعتماد.
    
    تتضمن بيانات الكاشف، الفني، المعتمد، كافة القياسات، القمم، الأجهزة،
    المصادر، المرفقات، وسجل الإعادات.
    """
    snapshot_data = {
        "report_number": report_number,
        "verdict": verdict,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approver": {
            "user_id": approver_session.user_id,
            "username": approver_session.username,
            "full_name": approver_session.full_name,
            "role": approver_session.role,
        },
        "inspection": inspection_full.get("inspection"),
        "detector": inspection_full.get("detector"),
        "hpge_measurements": inspection_full.get("hpge_measurements"),
        "peaks": inspection_full.get("peaks", []),
        "instruments": inspection_full.get("instruments", []),
        "sources": inspection_full.get("sources", []),
        "attachments": inspection_full.get("attachments", []),
        "retries": inspection_full.get("retries", []),
    }
    return json.dumps(snapshot_data, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Mandatory HPGe Fields & Validation
# ---------------------------------------------------------------------------

MANDATORY_HPGE_FIELDS: List[str] = [
    "beryllium_window_status",
    "cooling_hours",
    "vacuum_intact",
    "cryostat_wet_or_frozen",
    "v_pos_24",
    "v_neg_24",
    "v_pos_12",
    "v_neg_12",
    "applied_polarity",
    "hv_ramp_source",
    "hv_ramp_rate_v_per_s",
    "has_preamp_signal",
    "has_pz_distortion",
    "total_count_rate_cps",
    "dead_time_pct",
    "net_counts_1332",
    "measured_fwhm_kev",
    "form_factor",
    "relative_efficiency_pct",
]


def missing_mandatory_fields(inspection_full: Dict[str, Any]) -> List[str]:
    """تحديد قائمة بأسماء القياسات الإلزامية غير المسجلة (NULL أو غير موجودة)."""
    meas = inspection_full.get("hpge_measurements")
    if not meas:
        return list(MANDATORY_HPGE_FIELDS)

    missing: List[str] = []
    for field in MANDATORY_HPGE_FIELDS:
        val = meas.get(field)
        if val is None or val == "":
            missing.append(field)
    return missing


def check_early_stage_failure(
    inspection_full: Dict[str, Any],
    thresholds: Optional[SystemThresholds] = None,
) -> Optional[str]:
    """فحص ما إذا كان هناك إخفاق حاسم في المرحلتين 1 و 2 يبرر التوقف المبكر وفق FR-2.

    الإخفاقات الحاسمة:
    - R-HPGE-01: نافذة البريليوم مكسورة (BROKEN)
    - R-HPGE-04: رطوبة/تجمد الحاوية أو تسريب الفراغ (vacuum_intact == 0 أو cryostat_wet_or_frozen == 1)
    - R-HPGE-06: قطبية الجهد المطبق مخالفة للقطبية الاسمية أو المعتمدة
    - R-HPGE-08: انعدام إشارة المضخم الأولي (has_preamp_signal == 0)

    تُرجع رسالة توضيحية لسبب الإخفاق المبكر إذا وُجد، أو None إذا لم يوجد إخفاق حاسم.
    """
    meas = inspection_full.get("hpge_measurements") or {}
    detector = inspection_full.get("detector") or {}

    # R-HPGE-01: نافذة مكسورة
    ws_val = meas.get("beryllium_window_status")
    if ws_val is not None:
        val_str = ws_val.value if hasattr(ws_val, "value") else str(ws_val)
        if val_str == "BROKEN":
            return "R-HPGE-01: نافذة البريليوم مكسورة (BROKEN)"

    # R-HPGE-04: تجمد/رطوبة أو تسريب فراغ
    wet_frozen = meas.get("cryostat_wet_or_frozen")
    vac_intact = meas.get("vacuum_intact")
    if wet_frozen == 1 or (wet_frozen is True):
        return "R-HPGE-04: رصد رطوبة أو تجمد على الحاوية المفرغة"
    if vac_intact == 0 or (vac_intact is False):
        return "R-HPGE-04: فقدان فراغ الحاوية المفرغة (تسريب)"

    # R-HPGE-06: قطبية مخالفة
    app_pol = meas.get("applied_polarity")
    if app_pol is not None:
        try:
            applied = VoltagePolarity(app_pol.value if hasattr(app_pol, "value") else str(app_pol))
            nom_pol = None
            if detector.get("voltage_polarity"):
                nom_pol = VoltagePolarity(detector["voltage_polarity"])
            cert_pol = None
            if detector.get("certified_polarity"):
                cert_pol = VoltagePolarity(detector["certified_polarity"])
            res_pol = eval_r_hpge_06_hv_polarity(applied, nom_pol, cert_pol, thresholds=thresholds)
            if res_pol.verdict in (Verdict.FAILURE, Verdict.BLOCK):
                return f"R-HPGE-06: {res_pol.message_ar}"
        except Exception:
            pass

    # R-HPGE-08: انعدام إشارة المضخم الأولي
    has_sig = meas.get("has_preamp_signal")
    if has_sig == 0 or (has_sig is False):
        return "R-HPGE-08: انعدام إشارة المضخم الأولي (NO PREAMP SIGNAL)"

    return None


# ---------------------------------------------------------------------------
# Technical Evaluation Helper
# ---------------------------------------------------------------------------

def evaluate_hpge_rules_for_inspection(
    inspection_full: Dict[str, Any],
    thresholds: Optional[SystemThresholds] = None,
) -> List[RuleResult]:
    """تقييم قواعد فحص كاشف الجرمانيوم R-HPGE وفق البيانات الفنية المسجلة حصراً دون أي افتراضات آمنة."""
    meas = inspection_full.get("hpge_measurements")
    if not meas:
        return []

    detector = inspection_full.get("detector") or {}
    results: List[RuleResult] = []

    # 1. R-HPGE-01 & R-HPGE-02: سلامة نافذة البريليوم (فقط إذا كانت مسجلة)
    ws_val = meas.get("beryllium_window_status")
    if ws_val is not None:
        try:
            window_status = WindowStatus(ws_val.value if hasattr(ws_val, "value") else ws_val)
            results.append(eval_r_hpge_01_window_integrity(
                window_status=window_status,
                cryostat_struck=False,
                thresholds=thresholds,
            ))
            results.append(eval_r_hpge_02_window_scratch(
                window_status=window_status,
                thresholds=thresholds,
            ))
        except ValueError:
            pass

    # 2. R-HPGE-03: مدة التبريد بالنيتروجين السائل
    cooling_val = meas.get("cooling_hours")
    if cooling_val is not None:
        cert_cooling = detector.get("cooling_hours_cert")
        results.append(eval_r_hpge_03_cooling_time(
            cooling_hours=float(cooling_val),
            cooling_hours_cert=float(cert_cooling) if cert_cooling is not None else None,
            thresholds=thresholds,
        ))

    # 3. R-HPGE-04: فراغ الحاوية وتكثف الرطوبة/الجليد
    wet_frozen = meas.get("cryostat_wet_or_frozen")
    vac_intact = meas.get("vacuum_intact")
    if wet_frozen is not None or vac_intact is not None:
        results.append(eval_r_hpge_04_cryostat_vacuum(
            cryostat_wet_or_frozen=bool(wet_frozen) if wet_frozen is not None else False,
            vacuum_leak_detected=not bool(vac_intact) if vac_intact is not None else False,
            thresholds=thresholds,
        ))

    # 4. R-HPGE-05: جهود سلة NIM القياسية
    v_pos_24 = meas.get("v_pos_24")
    v_neg_24 = meas.get("v_neg_24")
    v_pos_12 = meas.get("v_pos_12")
    v_neg_12 = meas.get("v_neg_12")
    if all(v is not None for v in (v_pos_24, v_neg_24, v_pos_12, v_neg_12)):
        results.append(eval_r_hpge_05_nim_voltages(
            v_pos_24=float(v_pos_24),
            v_neg_24=float(v_neg_24),
            v_pos_12=float(v_pos_12),
            v_neg_12=float(v_neg_12),
            thresholds=thresholds,
        ))

    # 5. R-HPGE-06: قطبية الجهد العالي المطبق (فقط إذا كانت مسجلة صراحة)
    applied_pol_val = meas.get("applied_polarity")
    if applied_pol_val is not None:
        try:
            applied_pol = VoltagePolarity(applied_pol_val.value if hasattr(applied_pol_val, "value") else applied_pol_val)
            nom_pol = None
            if detector.get("voltage_polarity"):
                try:
                    nom_pol = VoltagePolarity(detector["voltage_polarity"])
                except ValueError:
                    pass

            cert_pol = None
            if detector.get("certified_polarity"):
                try:
                    cert_pol = VoltagePolarity(detector["certified_polarity"])
                except ValueError:
                    pass

            results.append(eval_r_hpge_06_hv_polarity(
                applied_polarity=applied_pol,
                nominal_polarity=nom_pol,
                certified_polarity=cert_pol,
                thresholds=thresholds,
            ))
        except ValueError:
            pass

    # 6. R-HPGE-07: معدل رفع الجهد العالي
    ramp_rate = meas.get("hv_ramp_rate_v_per_s")
    if ramp_rate is not None:
        max_limit = detector.get("hv_ramp_max_v_per_s")
        ref = detector.get("hv_ramp_reference")
        results.append(eval_r_hpge_07_hv_ramp_rate(
            ramp_rate_v_per_s=float(ramp_rate),
            max_rate_limit=float(max_limit) if max_limit is not None else None,
            reference=ref,
            thresholds=thresholds,
        ))

    # 7. R-HPGE-08: إشارة المضخم الأولي
    has_sig = meas.get("has_preamp_signal")
    if has_sig is not None:
        results.append(eval_r_hpge_08_preamp_signal(
            has_preamp_signal=bool(has_sig),
            thresholds=thresholds,
        ))

    # 8. R-HPGE-09: ضبط القطب/الصفر (P/Z)
    pz_dist = meas.get("has_pz_distortion")
    if pz_dist is not None:
        results.append(eval_r_hpge_09_pole_zero(
            has_pz_distortion=bool(pz_dist),
            thresholds=thresholds,
        ))

    # 9. R-HPGE-10: معدل العد والزمن الميت
    total_cps = meas.get("total_count_rate_cps")
    dead_time = meas.get("dead_time_pct")
    if total_cps is not None and dead_time is not None:
        results.append(eval_r_hpge_10_mca_rates(
            total_cps=float(total_cps),
            dead_time_pct=float(dead_time),
            thresholds=thresholds,
        ))

    # 10. R-HPGE-11: صافي عدات قمة 1332.5 keV
    net_counts = meas.get("net_counts_1332")
    if net_counts is not None:
        results.append(eval_r_hpge_11_peak_counts(
            net_counts=float(net_counts),
            thresholds=thresholds,
        ))

    # 11. R-HPGE-12: عرض نصف القمة (FWHM)
    measured_fwhm = meas.get("measured_fwhm_kev")
    if measured_fwhm is not None:
        nom_fwhm = detector.get("nominal_fwhm_kev")
        results.append(eval_r_hpge_12_fwhm(
            measured_fwhm_kev=float(measured_fwhm),
            nominal_fwhm_kev=float(nom_fwhm) if nom_fwhm is not None else None,
            thresholds=thresholds,
        ))

    # 12. R-HPGE-13/14: معامل الشكل (FWTM/FWHM)
    ff = meas.get("form_factor")
    if ff is not None:
        results.append(eval_r_hpge_13_14_form_factor(
            form_factor_value=float(ff),
            thresholds=thresholds,
        ))

    # 13. R-HPGE-15: الكفاءة النسبية
    rel_eff = meas.get("relative_efficiency_pct")
    nom_eff = detector.get("nominal_efficiency_pct")
    if rel_eff is not None and nom_eff is not None:
        results.append(eval_r_hpge_15_efficiency(
            measured_eff_pct=float(rel_eff),
            nominal_eff_pct=float(nom_eff),
            thresholds=thresholds,
        ))

    return results


# ---------------------------------------------------------------------------
# Inspection Service
# ---------------------------------------------------------------------------

class InspectionService:
    """خدمة إدارة دورة حياة فحوصات الكواشف وتطبيق قواعد الحوكمة الفنية والأمنية."""

    def __init__(self, conn: sqlite3.Connection, repository: Optional[InspectionRepository] = None):
        self.conn = conn
        self.repo = repository or InspectionRepository(conn)

    def create_draft(self, detector_id: int, session: Session, inspection_date: Optional[str] = None) -> int:
        """إنشاء مسودة فحص فني جديدة لكاشف محدد."""
        return self.repo.create_draft(detector_id, session, inspection_date=inspection_date)

    def save_hpge_measurements(
        self,
        inspection_id: int,
        measurements: Union[HPGeMeasurement, Dict[str, Any]],
        session: Session,
    ) -> None:
        """حفظ أو تحديث قياسات فحص كاشف الجرمانيوم."""
        return self.repo.save_hpge_measurements(inspection_id, measurements, session)

    def save_peaks(self, inspection_id: int, peaks: List[Dict[str, Any]], session: Session) -> None:
        """حفظ أو تحديث قائمة قمم طيف الفحص."""
        return self.repo.save_peaks(inspection_id, peaks, session)

    def link_instruments(self, inspection_id: int, instruments: List[Dict[str, Any]], session: Session) -> None:
        """ربط أجهزة القياس المستخدمة في الفحص."""
        return self.repo.link_instruments(inspection_id, instruments, session)

    def link_sources(self, inspection_id: int, sources: List[Dict[str, Any]], session: Session) -> None:
        """ربط المصادر المشعة المستخدمة في الفحص."""
        return self.repo.link_sources(inspection_id, sources, session)

    def add_attachment(
        self,
        inspection_id: int,
        file_path: str,
        file_type: str,
        caption: Optional[str],
        session: Session,
    ) -> int:
        """إضافة مرفق تقني للفحص مع التحقق من وجوده وبصمته."""
        return self.repo.add_attachment(inspection_id, file_path, file_type, caption, session)

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
        """تسجيل محاولة إعادة قياس لمرحلة معينة."""
        return self.repo.record_retry(
            inspection_id=inspection_id,
            stage_number=stage_number,
            reason_for_retry=reason_for_retry,
            corrective_action_taken=corrective_action_taken,
            previous_values=previous_values,
            new_values=new_values,
            session=session,
        )

    def get_inspection_full(self, inspection_id: int) -> Dict[str, Any]:
        """جلب تفاصيل الفحص كاملة مع كافة العلاقات واللقطات."""
        return self.repo.get_inspection_full(inspection_id)

    def list_by_detector(self, detector_id: int) -> List[Dict[str, Any]]:
        """عرض قائمة الفحوصات لكاشف معين مرتبة زمنياً."""
        return self.repo.list_by_detector(detector_id)

    def submit_for_approval(
        self,
        inspection_id: int,
        session: Session,
        technician_signature: Optional[str] = None,
    ) -> None:
        """تقديم الفحص للمراجعة والاعتماد (DRAFT / REJECTED -> PENDING_APPROVAL)."""
        insp = self.repo.get_by_id(inspection_id)
        if not insp:
            raise EntityNotFoundError(f"تقرير الفحص رقم {inspection_id} غير موجود.")

        # التحقق من أن المستخدم هو الفني صاحب الفحص
        if insp["technician_id"] != session.user_id:
            raise PermissionError("لا يمكن تقديم الفحص للمراجعة إلا من قبل الفني صاحب الفحص.")

        # التحقق من قاعدة التجميد SEC-03
        res_sec_03 = eval_rule_sec_03_immutable_approved_record(insp["status"])
        if res_sec_03.verdict == Verdict.BLOCK:
            raise SecurityViolationError(res_sec_03.message_ar)

        if insp["status"] == "PENDING_APPROVAL":
            raise InvalidStateTransitionError("الفحص قيد المراجعة والاعتماد مسبقاً.")

        if insp["status"] not in ("DRAFT", "REJECTED"):
            raise InvalidStateTransitionError(f"لا يمكن تقديم الفحص في حالته الحالية ({insp['status']}).")

        if not (session.can("conduct_inspection") or session.can("save_draft")):
            raise PermissionError("المستخدم لا يملك صلاحية تقديم الفحص للمراجعة والاعتماد.")

        # التحقق من وجود قياسات مسجلة
        full = self.repo.get_inspection_full(inspection_id)
        if not full.get("hpge_measurements"):
            raise InspectionWorkflowError("لا يمكن تقديم الفحص للمراجعة دون تسجيل قياسات الفحص الفنية.")

        # التحقق من اكتمال القياسات أو التوقف المبكر لإخفاق حاسم (FR-2)
        early_failure = check_early_stage_failure(full)
        if not early_failure:
            missing = missing_mandatory_fields(full)
            if missing:
                raise InspectionWorkflowError(
                    f"لا يمكن تقديم الفحص للمراجعة والاعتماد لوجود حقول إلزامية غير مسجلة: {', '.join(missing)}"
                )

        now = datetime.now(timezone.utc).isoformat()
        sig = technician_signature or f"SIG-TECH-{session.user_id}-{now}"

        self.repo.update_inspection_record(
            inspection_id=inspection_id,
            updates={
                "status": "PENDING_APPROVAL",
                "technician_signature_token": sig,
            },
            session=session,
            action="SUBMIT",
        )

    def approve_inspection(
        self,
        inspection_id: int,
        session: Session,
        manager_notes: Optional[str] = None,
        manager_signature: Optional[str] = None,
        evaluated_rule_results: Optional[List[RuleResult]] = None,
    ) -> Dict[str, Any]:
        """اعتماد تقرير الفحص الفني وتوليد رقم التقرير وتجميد السجل بلقطة كانونية."""
        insp = self.repo.get_by_id(inspection_id)
        if not insp:
            raise EntityNotFoundError(f"تقرير الفحص رقم {inspection_id} غير موجود.")

        # التحقق من قاعدة التجميد SEC-03
        if insp["status"] == "APPROVED":
            res_sec_03 = eval_rule_sec_03_immutable_approved_record(insp["status"])
            raise SecurityViolationError(res_sec_03.message_ar)

        if insp["status"] != "PENDING_APPROVAL":
            raise InvalidStateTransitionError(
                f"لا يمكن اعتماد الفحص في حالته الحالية ({insp['status']}). يجب تقديم الفحص للمراجعة أولاً."
            )

        # التحقق من قاعدة حظر اعتماد مدير النظام التقني SEC-02
        res_sec_02 = eval_rule_sec_02_admin_approval_prohibition(session.role)
        if res_sec_02.verdict == Verdict.BLOCK:
            raise SecurityViolationError(res_sec_02.message_ar)

        # التحقق من قاعدة فصل المهام SEC-01 (الفني لا يعتمد فحصه بنفسه)
        res_sec_01 = eval_rule_sec_01_separation_of_duties(
            technician_id=insp["technician_id"],
            approver_id=session.user_id,
        )
        if res_sec_01.verdict == Verdict.BLOCK:
            raise SecurityViolationError(res_sec_01.message_ar)

        # التحقق من صلاحية الاعتماد
        if not session.can("approve_inspection"):
            raise PermissionError("المستخدم لا يملك صلاحية الاعتماد الفني للفحوصات (approve_inspection).")

        full = self.repo.get_inspection_full(inspection_id)

        # التحقق من اكتمال القياسات أو التوقف المبكر لإخفاق حاسم (FR-2)
        early_failure = check_early_stage_failure(full)
        if not early_failure:
            missing = missing_mandatory_fields(full)
            if missing:
                raise InspectionWorkflowError(
                    f"لا يمكن اعتماد الفحص لوجود حقول إلزامية غير مسجلة: {', '.join(missing)}"
                )

        if early_failure:
            final_verdict = Verdict.FAILURE.value
        else:
            # تقييم القواعد وحساب الحكم النهائي
            if evaluated_rule_results is None:
                rule_results = evaluate_hpge_rules_for_inspection(full)
            else:
                rule_results = evaluated_rule_results

            agg = aggregate_verdicts(rule_results)
            if agg == Verdict.BLOCK:
                raise SecurityViolationError("لا يمكن اعتماد الفحص لوجود قاعدة أمان مانعة (BLOCK).")
            if agg == Verdict.WAIT:
                raise InspectionWorkflowError("لا يمكن اعتماد الفحص لوجود حالة فنية تتطلب الانتظار (WAIT).")

            if agg == Verdict.FAILURE:
                final_verdict = Verdict.FAILURE.value
            elif agg == Verdict.OUT_OF_SPEC:
                final_verdict = Verdict.OUT_OF_SPEC.value
            else:
                # تأكيد إضافي لمنع إصدار حكم PASS على أي فحص به حقول ناقصة
                if missing_mandatory_fields(full):
                    raise InspectionWorkflowError("لا يمكن إصدار حكم PASS على فحص ناقص القياسات الإلزامية.")
                final_verdict = Verdict.PASS.value

        now = datetime.now(timezone.utc).isoformat()
        sig = manager_signature or f"SIG-MGR-{session.user_id}-{now}"

        # توليد رقم التقرير وحفظ الاعتماد داخل معاملة فورية ذرية واحدة منعاً للتكرار
        if not self.conn.in_transaction:
            try:
                self.conn.execute("BEGIN IMMEDIATE;")
            except sqlite3.OperationalError:
                pass

        try:
            report_num = generate_report_number(self.conn, detector_type="HPGE")
            snapshot_str = build_inspection_snapshot(
                inspection_full=full,
                approver_session=session,
                verdict=final_verdict,
                report_number=report_num,
            )

            self.repo.update_inspection_record(
                inspection_id=inspection_id,
                updates={
                    "status": "APPROVED",
                    "verdict": final_verdict,
                    "report_number": report_num,
                    "approver_id": session.user_id,
                    "approved_at": now,
                    "manager_notes": manager_notes,
                    "manager_signature_token": sig,
                    "snapshot_json": snapshot_str,
                },
                session=session,
                action="APPROVE",
            )
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise

        return self.repo.get_inspection_full(inspection_id)

    def reject_inspection(
        self,
        inspection_id: int,
        rejection_reason: str,
        session: Session,
        manager_notes: Optional[str] = None,
        return_to_draft: bool = True,
    ) -> Dict[str, Any]:
        """رفض تقرير الفحص الفني وإعادته للمسودة مع توثيق سبب الرفض وملاحظات المدير."""
        if not rejection_reason or not rejection_reason.strip():
            raise ValueError("سبب الرفض إلزامي عند رفض تقرير الفحص الفني.")

        insp = self.repo.get_by_id(inspection_id)
        if not insp:
            raise EntityNotFoundError(f"تقرير الفحص رقم {inspection_id} غير موجود.")

        # التحقق من قاعدة التجميد SEC-03
        if insp["status"] == "APPROVED":
            res_sec_03 = eval_rule_sec_03_immutable_approved_record(insp["status"])
            raise SecurityViolationError(res_sec_03.message_ar)

        if insp["status"] != "PENDING_APPROVAL":
            raise InvalidStateTransitionError(
                f"لا يمكن رفض الفحص وهو في حالة ({insp['status']}). يجب أن يكون قيد المراجعة."
            )

        # التحقق من قاعدة SEC-02
        res_sec_02 = eval_rule_sec_02_admin_approval_prohibition(session.role)
        if res_sec_02.verdict == Verdict.BLOCK:
            raise SecurityViolationError(res_sec_02.message_ar)

        # التحقق من قاعدة SEC-01
        res_sec_01 = eval_rule_sec_01_separation_of_duties(
            technician_id=insp["technician_id"],
            approver_id=session.user_id,
        )
        if res_sec_01.verdict == Verdict.BLOCK:
            raise SecurityViolationError(res_sec_01.message_ar)

        # التحقق من صلاحية الرفض
        if not session.can("reject_inspection"):
            raise PermissionError("المستخدم لا يملك صلاحية رفض الفحص (reject_inspection).")

        target_status = "DRAFT" if return_to_draft else "REJECTED"

        self.repo.update_inspection_record(
            inspection_id=inspection_id,
            updates={
                "status": target_status,
                "rejection_reason": rejection_reason.strip(),
                "manager_notes": manager_notes,
            },
            session=session,
            action="REJECT",
        )

        return self.repo.get_inspection_full(inspection_id)

    def return_to_draft(self, inspection_id: int, session: Session) -> Dict[str, Any]:
        """إعادة الفحص المرفوض إلى مسودة (REJECTED -> DRAFT)."""
        insp = self.repo.get_by_id(inspection_id)
        if not insp:
            raise EntityNotFoundError(f"تقرير الفحص رقم {inspection_id} غير موجود.")

        # التحقق من أن المستخدم هو الفني صاحب الفحص
        if insp["technician_id"] != session.user_id:
            raise PermissionError("لا يمكن إعادة الفحص إلى مسودة إلا من قبل الفني صاحب الفحص.")

        if insp["status"] == "APPROVED":
            res_sec_03 = eval_rule_sec_03_immutable_approved_record(insp["status"])
            raise SecurityViolationError(res_sec_03.message_ar)

        if insp["status"] == "DRAFT":
            return self.repo.get_inspection_full(inspection_id)

        if insp["status"] != "REJECTED":
            raise InvalidStateTransitionError(
                f"لا يمكن إعادة الفحص إلى مسودة من حالة ({insp['status']})."
            )

        if not (session.can("conduct_inspection") or session.can("save_draft")):
            raise PermissionError("المستخدم لا يملك صلاحية إعادة الفحص إلى مسودة.")

        self.repo.update_inspection_record(
            inspection_id=inspection_id,
            updates={"status": "DRAFT"},
            session=session,
            action="UPDATE",
        )

        return self.repo.get_inspection_full(inspection_id)

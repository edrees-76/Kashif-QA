"""نماذج البيانات والتحقق الصارم عبر Pydantic لمنظومة كاشف.

Data models and strict validation using Pydantic v2.
Compliant with Python 3.11+ and IAEA standards.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
import json
from typing import Any, Dict, List, Optional, Set

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class DetectorType(str, Enum):
    """نوع الكاشف الإشعاعي المدعوم."""
    HPGE = "HPGE"
    NAI = "NAI"


class CrystalType(str, Enum):
    """نوع البلورة.
    
    ملاحظة معمارية: PLANAR مضمن في التعريف لتوافق v1.1 المستقبلي،
    ولكنه يُرفض بشكل صارم في كواشف v1.0 وفق القرار المعماري ADR-0003.
    """
    COAXIAL = "COAXIAL"
    PLANAR = "PLANAR"


class WindowType(str, Enum):
    """نوع نافذة دخول الإشعاع."""
    BERYLLIUM = "BERYLLIUM"
    ALUMINUM = "ALUMINUM"
    CARBON_COMPOSITE = "CARBON_COMPOSITE"
    OTHER = "OTHER"


class WindowStatus(str, Enum):
    """الحالة الفنية لنافذة البريليوم وفق IAEA MRNI-507 ص 7."""
    INTACT = "INTACT"
    SCRATCHED = "SCRATCHED"
    BROKEN = "BROKEN"


class PreampType(str, Enum):
    """نوع المضخم الأولي لكاشف الجرمانيوم وفق IAEA MRNI-507 ص 9."""
    RESISTIVE = "RESISTIVE"
    TRP = "TRP"
    OTHER = "OTHER"


class VoltagePolarity(str, Enum):
    """قطبية الجهد العالي للانحياز."""
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class InstrumentType(str, Enum):
    """نوع أجهزة الفحص المعملية المرتبطة."""
    NIM_BIN = "NIM_BIN"
    HV_SUPPLY = "HV_SUPPLY"
    AMPLIFIER = "AMPLIFIER"
    MCA = "MCA"
    SCOPE = "SCOPE"


class Nuclide(str, Enum):
    """النظائر المشعة المعتمدة للمعايرة."""
    CO_60 = "Co-60"
    CS_137 = "Cs-137"


class Verdict(str, Enum):
    """الأحكام الفنية الصريحة للفحص وفق IAEA MRNI-507 البند 5.2."""
    PASS = "PASS"
    OUT_OF_SPEC = "OUT_OF_SPEC"
    FAILURE = "FAILURE"
    WAIT = "WAIT"
    BLOCK = "BLOCK"
    WARNING = "WARNING"


class InspectionStatus(str, Enum):
    """حالات دورة حياة تقرير الفحص الفني."""
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class UserRole(str, Enum):
    """أدوار وصلاحيات مستخدمي المنظومة."""
    TECHNICIAN = "TECHNICIAN"
    LAB_MANAGER = "LAB_MANAGER"
    ADMIN = "ADMIN"


# ---------------------------------------------------------------------------
# نماذج الكواشف والأجهزة والمصادر
# ---------------------------------------------------------------------------

class HPGeDetector(BaseModel):
    """نموذج كاشف الجرمانيوم عالي النقاء (Coaxial HPGe)."""
    model_config = ConfigDict(strict=True, extra="forbid")

    id: Optional[int] = None
    code: str = Field(..., min_length=1, description="الرمز التعريفي الفريد للكاشف في المعمل")
    detector_type: DetectorType = Field(default=DetectorType.HPGE)
    manufacturer: str = Field(..., min_length=1, description="الشركة الصانعة (إلزامي)")
    model: str = Field(..., min_length=1, description="طراز الكاشف")
    serial_number: str = Field(..., min_length=1, description="الرقم التسلسلي (إلزامي)")
    crystal_type: CrystalType = Field(default=CrystalType.COAXIAL, description="نوع البلورة")
    window_type: WindowType = Field(default=WindowType.BERYLLIUM)
    beryllium_window_status: WindowStatus = Field(default=WindowStatus.INTACT)
    preamp_type: PreampType = Field(default=PreampType.RESISTIVE)
    nominal_voltage: float = Field(..., gt=0.0, description="جهد التشغيل الاسمي الموجب (فولت)")
    voltage_polarity: VoltagePolarity = Field(default=VoltagePolarity.POSITIVE)
    nominal_fwhm_kev: Optional[float] = Field(default=None, gt=0.0, description="FWHM الاسمي عند 1332.5 keV")
    nominal_efficiency_pct: Optional[float] = Field(default=None, gt=0.0, description="الكفاءة النسبية الاسمية %")
    diameter_inch: Optional[float] = Field(default=None, gt=0.0, description="قطر البلورة (بوصة)")
    height_inch: Optional[float] = Field(default=None, gt=0.0, description="طول البلورة (بوصة)")
    facility_name: Optional[str] = Field(default=None, description="اسم المنشأة أو المعمل")
    notes: Optional[str] = None

    @field_validator("crystal_type")
    @classmethod
    def validate_crystal_type_v1(cls, v: CrystalType) -> CrystalType:
        """رفض كواشف الجرمانيوم المستوية في v1.0 وفق ADR-0003."""
        if v == CrystalType.PLANAR:
            raise ValueError(
                "كواشف الجرمانيوم المستوية (Planar HPGe) غير مدعومة في الإصدار v1.0 "
                "ومؤجلة إلى v1.1 وفق القرار المعماري ADR-0003."
            )
        return v

    @field_validator("code", "manufacturer", "model", "serial_number")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("الحقل النصي إلزامي ولا يمكن أن يكون فارغاً")
        return s


class NaIDetector(BaseModel):
    """نموذج كاشف يوديد الصوديوم الوميضي NaI(Tl)."""
    model_config = ConfigDict(strict=True, extra="forbid")

    id: Optional[int] = None
    code: str = Field(..., min_length=1)
    detector_type: DetectorType = Field(default=DetectorType.NAI)
    manufacturer: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    serial_number: str = Field(..., min_length=1)
    diameter_inch: float = Field(..., gt=0.0, description="قطر البلورة بالبوصة (مثال: 3.0)")
    height_inch: float = Field(..., gt=0.0, description="ارتفاع البلورة بالبوصة (مثال: 3.0)")
    nominal_voltage: float = Field(..., gt=0.0, description="جهد التشغيل الموصى به (فولت)")
    nominal_resolution_pct: Optional[float] = Field(default=None, gt=0.0, description="الدقة الاسمية %R")
    facility_name: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("code", "manufacturer", "model", "serial_number")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("الحقل النصي إلزامي ولا يمكن أن يكون فارغاً")
        return s


class Instrument(BaseModel):
    """نموذج جهاز فحص معملي (NIM, HV, Amp, MCA, Scope)."""
    model_config = ConfigDict(strict=True, extra="forbid")

    id: Optional[int] = None
    instrument_type: InstrumentType
    manufacturer: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    serial_number: str = Field(..., min_length=1)
    ripple_mv: Optional[float] = Field(default=None, ge=0.0, description="التموج بالمللي فولت")
    regulation_pct: Optional[float] = Field(default=None, ge=0.0, description="نسبة تنظيم الجهد %")
    channels_count: Optional[int] = Field(default=None, gt=0, description="عدد قنوات MCA")
    shaping_range_min_us: Optional[float] = Field(default=None, ge=0.0, description="الحد الأدنى لزمن التشكيل")
    shaping_range_max_us: Optional[float] = Field(default=None, ge=0.0, description="الحد الأقصى لزمن التشكيل")
    cal_date: Optional[date] = None
    cal_expiry: Optional[date] = None
    is_in_service: bool = True
    facility_name: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("manufacturer", "model", "serial_number")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("الحقل النصي إلزامي ولا يمكن أن يكون فارغاً")
        return s

    @model_validator(mode="after")
    def validate_shaping_range(self) -> Instrument:
        if self.shaping_range_min_us is not None and self.shaping_range_max_us is not None:
            if self.shaping_range_min_us > self.shaping_range_max_us:
                raise ValueError("الحد الأدنى لزمن التشكيل لا يجوز أن يتجاوز الحد الأقصى")
        return self


class RadioactiveSource(BaseModel):
    """نموذج مصدر إشعاعي للمعايرة والفحص."""
    model_config = ConfigDict(strict=True, extra="forbid")

    id: Optional[int] = None
    nuclide: Nuclide
    source_code: str = Field(..., min_length=1)
    serial_number: str = Field(..., min_length=1)
    cert_activity_bq: float = Field(..., gt=0.0, description="الفعالية الإشعاعية المعتمدة بالبيكرل")
    cert_date: date = Field(..., description="تاريخ شهادة المعايرة")
    half_life_days: float = Field(..., gt=0.0, description="عمر النصف بالأيام")
    certificate_number: Optional[str] = None
    facility_name: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("source_code", "serial_number")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("الحقل النصي إلزامي ولا يمكن أن يكون فارغاً")
        return s


# ---------------------------------------------------------------------------
# نماذج القياسات واللقطات المجمدة
# ---------------------------------------------------------------------------

class HPGeMeasurement(BaseModel):
    """بيانات قياسات فحص كاشف الجرمانيوم."""
    model_config = ConfigDict(strict=True, extra="forbid")

    cooling_hours: float = Field(..., ge=0.0, description="مدة التبريد بالنيتروجين السائل بالساعات")
    cryostat_intact: bool = Field(default=True)
    beryllium_window_status: WindowStatus = Field(default=WindowStatus.INTACT)
    vacuum_intact: bool = Field(default=True)
    cryostat_wet_or_frozen: bool = Field(default=False)
    
    # جهود سلة NIM (فولت)
    v_pos_24: Optional[float] = None
    v_neg_24: Optional[float] = None
    v_pos_12: Optional[float] = None
    v_neg_12: Optional[float] = None
    v_pos_6: Optional[float] = None
    v_neg_6: Optional[float] = None

    # الجهد العالي المطبق
    applied_voltage: float = Field(..., ge=0.0)
    applied_polarity: VoltagePolarity = Field(default=VoltagePolarity.POSITIVE)
    hv_ramp_rate_v_per_s: Optional[float] = Field(default=None, ge=0.0)

    # المضخم الأولي والرئيسي
    has_preamp_signal: bool = Field(default=True)
    shaping_time_us: Optional[float] = Field(default=None, gt=0.0)
    has_pz_distortion: bool = Field(default=False)

    # قياسات MCA والطيف
    total_cps: Optional[float] = Field(default=None, ge=0.0)
    dead_time_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    net_counts_1332: Optional[float] = Field(default=None, ge=0.0)
    fwhm_measured_kev: Optional[float] = Field(default=None, gt=0.0)
    fwtm_measured_kev: Optional[float] = Field(default=None, gt=0.0)
    form_factor_measured: Optional[float] = Field(default=None, gt=0.0)
    eff_measured_pct: Optional[float] = Field(default=None, ge=0.0)


class NaIMeasurement(BaseModel):
    """بيانات قياسات فحص كاشف يوديد الصوديوم."""
    model_config = ConfigDict(strict=True, extra="forbid")

    applied_voltage: float = Field(..., ge=0.0)
    v_initial: Optional[float] = Field(default=None, ge=0.0)
    v_max: Optional[float] = Field(default=None, ge=0.0)
    pmt_voltage_drift_pct: Optional[float] = Field(default=None)
    resolution_pct: Optional[float] = Field(default=None, ge=0.0)
    fwhm_kev: Optional[float] = Field(default=None, gt=0.0)
    peak_centroid_ch: Optional[float] = Field(default=None, ge=0.0)
    centroid_drift_channels: Optional[float] = Field(default=None)
    drift_duration_hours: Optional[float] = Field(default=None, ge=0.0)


class Measurement(BaseModel):
    """نموذج القياس العام التجميعي القابل للتجميد والتخزين."""
    model_config = ConfigDict(strict=True, extra="forbid")

    detector_code: str = Field(..., min_length=1)
    detector_type: DetectorType
    serial_number: str = Field(..., min_length=1)
    inspection_date: date
    technician_id: int
    approver_id: Optional[int] = None
    hpge_data: Optional[HPGeMeasurement] = None
    nai_data: Optional[NaIMeasurement] = None
    verdict: Optional[Verdict] = None
    created_at: Optional[datetime] = None

    def snapshot_json(self) -> str:
        """توليد لقطة JSON حتمية الترتيب ومجمدة للقياس.
        
        Canonical JSON representation:
        - Sorted keys
        - UTF-8 compatible with ensure_ascii=False
        - Deterministic formatting
        """
        data = self.model_dump(mode="json")
        return json.dumps(data, sort_keys=True, ensure_ascii=False)

"""اختبارات نماذج البيانات والتحقق الصارم عبر Pydantic v2 (TASK-1.1 & 1.8).

Tests for kashif.core.models:
- Strict validation of HPGe and NaI detectors
- Rejection of PLANAR crystal type in v1.0 (ADR-0003)
- Rejection of negative/zero voltages, dimensions, activities
- Mandatory serial numbers and manufacturers
- Deterministic snapshot_json generation
"""

from __future__ import annotations

from datetime import date, datetime
import json
import pytest
from pydantic import ValidationError

from kashif.core.models import (
    CrystalType,
    DetectorType,
    HPGeDetector,
    HPGeMeasurement,
    InspectionStatus,
    Instrument,
    InstrumentType,
    Measurement,
    NaIDetector,
    NaIMeasurement,
    Nuclide,
    PreampType,
    RadioactiveSource,
    SourceIsotope,
    UserRole,
    Verdict,
    VoltagePolarity,
    WindowStatus,
    WindowType,
)


def test_hpge_detector_valid():
    """كاشف HPGe محوري صحيح وفق المتطلبات القياسية."""
    det = HPGeDetector(
        code="HPGE-01",
        manufacturer="Canberra",
        model="GC2018",
        serial_number="SN-78421",
        crystal_type=CrystalType.COAXIAL,
        window_type=WindowType.BERYLLIUM,
        beryllium_window_status=WindowStatus.INTACT,
        preamp_type=PreampType.RESISTIVE,
        nominal_voltage=3000.0,
        voltage_polarity=VoltagePolarity.POSITIVE,
        nominal_fwhm_kev=1.85,
        nominal_efficiency_pct=25.0,
        diameter_inch=2.0,
        height_inch=2.0,
        facility_name="معمل القياسات الإشعاعية",
    )
    assert det.code == "HPGE-01"
    assert det.crystal_type == CrystalType.COAXIAL
    assert det.nominal_voltage == 3000.0


def test_hpge_detector_planar_rejection_v1():
    """التحقق من الرفض الصارم لكواشف PLANAR في v1.0 وفق ADR-0003."""
    with pytest.raises(ValidationError) as exc_info:
        HPGeDetector(
            code="HPGE-PLANAR-01",
            manufacturer="ORTEC",
            model="GLP",
            serial_number="SN-9999",
            crystal_type=CrystalType.PLANAR,  # يجب أن يُرفض
            nominal_voltage=1500.0,
        )
    err_str = str(exc_info.value)
    assert "كواشف الجرمانيوم المستوية (Planar HPGe) غير مدعومة في الإصدار v1.0" in err_str
    assert "ADR-0003" in err_str


@pytest.mark.parametrize("invalid_voltage", [0.0, -100.0, -3000.0])
def test_hpge_detector_invalid_voltage(invalid_voltage: float):
    """رفض أي جهد اسمي سالب أو صفري."""
    with pytest.raises(ValidationError):
        HPGeDetector(
            code="HPGE-INV-V",
            manufacturer="Mirion",
            model="GC2018",
            serial_number="SN-123",
            nominal_voltage=invalid_voltage,
        )


@pytest.mark.parametrize("field_name, empty_val", [
    ("manufacturer", "   "),
    ("serial_number", ""),
    ("code", " "),
    ("model", ""),
])
def test_hpge_detector_mandatory_non_empty_strings(field_name: str, empty_val: str):
    """الرقم التسلسلي والشركة الصانعة والرمز والطراز حقول إلزامية لا تقبل الفراغ."""
    data = {
        "code": "DET-01",
        "manufacturer": "Mirion",
        "model": "Model-X",
        "serial_number": "SN-001",
        "nominal_voltage": 2500.0,
    }
    data[field_name] = empty_val
    with pytest.raises(ValidationError):
        HPGeDetector(**data)


def test_nai_detector_valid_and_validation():
    """كاشف NaI(Tl) 3x3 بوصة والتحقق من صحة أبعاده وجهده."""
    det = NaIDetector(
        code="NAI-01",
        manufacturer="Bicron",
        model="3M3/3",
        serial_number="SN-NAI-441",
        diameter_inch=3.0,
        height_inch=3.0,
        nominal_voltage=950.0,
        nominal_resolution_pct=7.2,
    )
    assert det.diameter_inch == 3.0
    assert det.nominal_voltage == 950.0

    # رفض الأبعاد السالبة أو الصفرية
    with pytest.raises(ValidationError):
        NaIDetector(
            code="NAI-ERR",
            manufacturer="Bicron",
            model="3M3",
            serial_number="SN-1",
            diameter_inch=0.0,
            height_inch=3.0,
            nominal_voltage=900.0,
        )


def test_instrument_model_and_shaping_range():
    """نموذج جهاز الفحص المعملي والتحقق من سلامة نطاق زمن التشكيل."""
    inst = Instrument(
        instrument_type=InstrumentType.AMPLIFIER,
        manufacturer="ORTEC",
        model="672",
        serial_number="SN-AMP-882",
        shaping_range_min_us=0.5,
        shaping_range_max_us=12.0,
        cal_date=date(2025, 1, 1),
        cal_expiry=date(2026, 1, 1),
    )
    assert inst.shaping_range_max_us == 12.0

    # رفض النطاق غير المنطقي (الحد الأدنى أكبر من الأقصى)
    with pytest.raises(ValidationError):
        Instrument(
            instrument_type=InstrumentType.AMPLIFIER,
            manufacturer="ORTEC",
            model="672",
            serial_number="SN-AMP-882",
            shaping_range_min_us=10.0,
            shaping_range_max_us=2.0,
        )

    # رفض التموج السالب
    with pytest.raises(ValidationError):
        Instrument(
            instrument_type=InstrumentType.HV_SUPPLY,
            manufacturer="Canberra",
            model="3106D",
            serial_number="SN-HV-1",
            ripple_mv=-5.0,
        )


def test_radioactive_source_valid_and_validation():
    """نموذج المصدر المشع والنظائر المشعة والتحقق من الفعالية وقائمة النظائر."""
    iso1 = SourceIsotope(nuclide_code="Co-60", cert_activity_bq=37000.0, cert_date=date(2024, 1, 1))
    iso2 = SourceIsotope(nuclide_code="Cs-137", cert_activity_bq=18500.0)

    src = RadioactiveSource(
        source_code="SRC-MULTI-01",
        serial_number="CS-60-2024-01",
        cert_date=date(2024, 1, 1),
        isotopes=[iso1, iso2],
    )
    assert src.total_activity_bq == 55500.0
    assert src.primary_nuclide == "Co-60"
    assert len(src.isotopes) == 2

    # رفض قائمة نظائر فارغة
    with pytest.raises(ValidationError):
        RadioactiveSource(
            source_code="SRC-EMPTY-01",
            serial_number="SN-01",
            cert_date=date(2024, 1, 1),
            isotopes=[],
        )

    # رفض فعالية نظير غير موجبة
    with pytest.raises(ValidationError):
        SourceIsotope(nuclide_code="Cs-137", cert_activity_bq=0.0)

    # رفض رمز نظير فارغ
    with pytest.raises(ValidationError):
        SourceIsotope(nuclide_code="  ", cert_activity_bq=100.0)


def test_measurement_snapshot_json_determinism():
    """التحقق من أن توليد snapshot_json حتمي، مرتب المفاتيح، وثابت دائماً."""
    meas = Measurement(
        detector_code="HPGE-01",
        detector_type=DetectorType.HPGE,
        serial_number="SN-78421",
        inspection_date=date(2026, 10, 27),
        technician_id=2,
        approver_id=1,
        hpge_data=HPGeMeasurement(
            cooling_hours=7.5,
            cryostat_intact=True,
            beryllium_window_status=WindowStatus.INTACT,
            applied_voltage=3000.0,
            applied_polarity=VoltagePolarity.POSITIVE,
            fwhm_measured_kev=1.82,
            fwtm_measured_kev=3.35,
            form_factor_measured=1.84,
            net_counts_1332=12500.0,
            total_cps=1200.0,
            dead_time_pct=2.4,
            eff_measured_pct=24.8,
        ),
        verdict=Verdict.PASS,
        created_at=datetime(2026, 10, 27, 10, 0, 0),
    )

    json_str_1 = meas.snapshot_json()
    json_str_2 = meas.snapshot_json()

    # حتمية 100%
    assert json_str_1 == json_str_2

    # التحقق من ترتيب المفاتيح تصاعدياً
    data = json.loads(json_str_1)
    keys = list(data.keys())
    assert keys == sorted(keys)
    assert data["detector_code"] == "HPGE-01"
    assert data["verdict"] == "PASS"


def test_models_additional_validations():
    """التحقق من رفض السلاسل الفارغة في Instrument و RadioactiveSource و NaIDetector."""
    # Instrument empty manufacturer
    with pytest.raises(ValidationError):
        Instrument(
            instrument_type=InstrumentType.MCA,
            manufacturer="  ",
            model="Inspire",
            serial_number="SN-123",
        )

    # RadioactiveSource empty code
    with pytest.raises(ValidationError):
        RadioactiveSource(
            source_code="  ",
            serial_number="SN-1",
            cert_date=date(2025, 1, 1),
            isotopes=[SourceIsotope(nuclide_code="Co-60", cert_activity_bq=1000.0)],
        )

    # NaIDetector empty model
    with pytest.raises(ValidationError):
        NaIDetector(
            code="NAI-01",
            manufacturer="Bicron",
            model="   ",
            serial_number="SN-1",
            diameter_inch=3.0,
            height_inch=3.0,
            nominal_voltage=1000.0,
        )

    # NaI Measurement snapshot
    nai_meas = Measurement(
        detector_code="NAI-01",
        detector_type=DetectorType.NAI,
        serial_number="SN-123",
        inspection_date=date(2026, 10, 27),
        technician_id=3,
        nai_data=NaIMeasurement(
            applied_voltage=950.0,
            resolution_pct=7.26,
            fwhm_kev=48.06,
        ),
        verdict=Verdict.PASS,
    )
    s = nai_meas.snapshot_json()
    assert "NAI-01" in s
    assert "7.26" in s


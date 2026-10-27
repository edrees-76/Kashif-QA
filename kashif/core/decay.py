"""حاسبة الاضمحلال الإشعاعي والتحويل الموحد للوحدات النووية.

Radioactive decay calculator and unit conversions for calibration sources.
Supports Co-60 and Cs-137 according to IAEA and nuclear data standards.
Pure functions with strict input validation.
"""

from __future__ import annotations

from datetime import date
import math
from typing import Union

# ---------------------------------------------------------------------------
# الثوابت النووية المعتمدة
# ---------------------------------------------------------------------------

DAYS_PER_YEAR: float = 365.25
"""عدد الأيام في السنة المعتمد في الحسابات النووية (سنة يوليانية).
حالة التأكيد: [TBC: يحتاج تأكيد المهندس - اقتراح: 365.25 يوم/سنة كما في tasks.md TASK-1.3]
"""

HALF_LIFE_CO60_DAYS: float = 1925.38
"""نصف عمر الكوبالت-60 بالأيام (5.2714 سنة * 365.25 يوم/سنة = 1925.38 يوماً).
المصدر: IAEA MRNI-507 / docs/tasks.md TASK-1.3.
حالة التأكيد: [TBC: يحتاج تأكيد المهندس]
"""

HALF_LIFE_CS137_DAYS: float = 10986.72
"""نصف عمر السيزيوم-137 بالأيام (30.08 سنة * 365.25 يوم/سنة = 10986.72 يوماً).
المصدر: IAEA MRNI-504 / docs/tasks.md TASK-1.3.
حالة التأكيد: [TBC: يحتاج تأكيد المهندس]
"""

BQ_PER_UCI: float = 37000.0
"""معامل التحويل الدقيق من ميكروكوري إلى بيكرل (1 uCi = 3.7e4 Bq).
المصدر: النظام الدولي للوحدات (SI) وتعريف الكوري القياسي المعتمد في BIPM وNIST.
حالة التأكيد: CONFIRMED
"""

BQ_PER_MCI: float = 37000000.0
"""معامل التحويل من ميللي كوري إلى بيكرل (1 mCi = 3.7e7 Bq).
حالة التأكيد: CONFIRMED
"""

BQ_PER_CI: float = 37000000000.0
"""معامل التحويل من كوري إلى بيكرل (1 Ci = 3.7e10 Bq).
حالة التأكيد: CONFIRMED
"""


# ---------------------------------------------------------------------------
# دوال حساب الاضمحلال الإشعاعي
# ---------------------------------------------------------------------------

def decay_activity(activity_0: float, elapsed_days: float, half_life_days: float) -> float:
    """حساب الفعالية الإشعاعية اللحظية A(t) بناءً على قانون الاضمحلال الأسي.
    
    المعادلة:
        A(t) = A_0 * exp(-lambda * t) = A_0 * (0.5)^(t / T_1/2)
        حيث:
        lambda = ln(2) / T_1/2
    
    المعاملات:
        activity_0: الفعالية الابتدائية (A_0) عند تاريخ المعايرة (>= 0).
        elapsed_days: الزمن المنقضي بالأيام (t >= 0).
        half_life_days: عمر النصف للنظير بالأيام (T_1/2 > 0).
    
    العائد:
        الفعالية الإشعاعية اللحظية بنفس وحدة الفعالية الابتدائية.
    
    استثناءات:
        ValueError: في حال تمرير زمن سالب، فعالية سالبة، أو عمر نصف غير موجب.
    """
    if activity_0 < 0:
        raise ValueError("الفعالية الإشعاعية الابتدائية لا يجوز أن تكون سالبة")
    if elapsed_days < 0:
        raise ValueError("الزمن المنقضي لا يجوز أن يكون سالباً")
    if half_life_days <= 0:
        raise ValueError("عمر النصف يجب أن يكون قيمة موجبة تماماً")
    
    if activity_0 == 0.0:
        return 0.0

    decay_constant = math.log(2.0) / half_life_days
    return float(activity_0 * math.exp(-decay_constant * elapsed_days))


def decay_activity_co60(activity_0: float, elapsed_days: float) -> float:
    """حساب الفعالية اللحظية لمصدر الكوبالت-60 (Co-60).
    
    المعاملات:
        activity_0: الفعالية الابتدائية (>= 0).
        elapsed_days: الزمن المنقضي بالأيام (>= 0).
    """
    return decay_activity(activity_0, elapsed_days, HALF_LIFE_CO60_DAYS)


def decay_activity_cs137(activity_0: float, elapsed_days: float) -> float:
    """حساب الفعالية اللحظية لمصدر السيزيوم-137 (Cs-137).
    
    المعاملات:
        activity_0: الفعالية الابتدائية (>= 0).
        elapsed_days: الزمن المنقضي بالأيام (>= 0).
    """
    return decay_activity(activity_0, elapsed_days, HALF_LIFE_CS137_DAYS)


def decay_activity_between_dates(
    activity_0: float,
    cert_date: date,
    target_date: date,
    half_life_days: float,
) -> float:
    """حساب الفعالية اللحظية بين تاريخين محددين (تاريخ الشهادة وتاريخ الفحص).
    
    المعاملات:
        activity_0: الفعالية الابتدائية المدونة بالشهادة (>= 0).
        cert_date: تاريخ شهادة المعايرة.
        target_date: تاريخ القياس الحالي (يجب أن يكون >= cert_date).
        half_life_days: عمر النصف للنظير بالأيام (> 0).
    """
    if target_date < cert_date:
        raise ValueError("تاريخ القياس لا يجوز أن يسبق تاريخ شهادة المعايرة (زمن منقضٍ سالب)")
    
    delta_days = float((target_date - cert_date).days)
    return decay_activity(activity_0, delta_days, half_life_days)


# ---------------------------------------------------------------------------
# دوال تحويل وحدات النشاط الإشعاعي (Curie <-> Becquerel)
# ---------------------------------------------------------------------------

def uci_to_bq(uci: float) -> float:
    """تحويل الفعالية من ميكروكوري إلى بيكرل (1 uCi = 37,000 Bq)."""
    if uci < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(uci * BQ_PER_UCI)


def bq_to_uci(bq: float) -> float:
    """تحويل الفعالية من بيكرل إلى ميكروكوري."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / BQ_PER_UCI)


def mci_to_bq(mci: float) -> float:
    """تحويل الفعالية من ميللي كوري إلى بيكرل (1 mCi = 3.7e7 Bq)."""
    if mci < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(mci * BQ_PER_MCI)


def bq_to_mci(bq: float) -> float:
    """تحويل الفعالية من بيكرل إلى ميللي كوري."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / BQ_PER_MCI)


def ci_to_bq(ci: float) -> float:
    """تحويل الفعالية من كوري إلى بيكرل (1 Ci = 3.7e10 Bq)."""
    if ci < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(ci * BQ_PER_CI)


def bq_to_ci(bq: float) -> float:
    """تحويل الفعالية من بيكرل إلى كوري."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / BQ_PER_CI)


def bq_to_kbq(bq: float) -> float:
    """تحويل من بيكرل إلى كيلو بيكرل (1 kBq = 1,000 Bq)."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / 1000.0)


def kbq_to_bq(kbq: float) -> float:
    """تحويل من كيلو بيكرل إلى بيكرل."""
    if kbq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(kbq * 1000.0)


def bq_to_mbq(bq: float) -> float:
    """تحويل من بيكرل إلى ميغا بيكرل (1 MBq = 1,000,000 Bq)."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / 1000000.0)


def mbq_to_bq(mbq: float) -> float:
    """تحويل من ميغا بيكرل إلى بيكرل."""
    if mbq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(mbq * 1000000.0)


def bq_to_gbq(bq: float) -> float:
    """تحويل من بيكرل إلى غيغا بيكرل (1 GBq = 1e9 Bq)."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / 1000000000.0)


def gbq_to_bq(gbq: float) -> float:
    """تحويل من غيغا بيكرل إلى بيكرل."""
    if gbq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(gbq * 1000000000.0)

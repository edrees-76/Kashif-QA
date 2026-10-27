"""المكتبة الفيزيائية ومعادلات المعايير الدولية (IAEA MRNI Series).

Physics calculations library for radiation detectors quality assurance.
All equations, parameters, and references strictly adhere to IAEA documents.
Pure functions with strict input validation and no external side effects.
"""

from __future__ import annotations

from typing import Union


def fwhm_kev(fwhm_channels: float, slope_m: float) -> float:
    """حساب العرض الكامل عند منتصف الارتفاع FWHM بوحدة keV.
    
    المعادلة:
        FWHM(keV) = FWHM(channels) * m
    
    المرجع:
        IAEA Procedure Nº: MRNI-507 (Rev. D0), Section 3.7.6, Page 15:
        "if you made the calibration manually and you know the energy per channel, m,
         get the number of channels at the FWHM and then, convert them to an energy value."
    
    المعاملات:
        fwhm_channels: عرض القمة عند نصف الارتفاع بالقنوات (>= 0).
        slope_m: معامل المعايرة الطاقية (keV/channel)، ويجب أن يكون موجباً (> 0).
    
    العائد:
        قيمة FWHM بوحدة keV.
    
    استثناءات:
        ValueError: إذا كان عرض القنوات سالباً أو الميل <= 0.
    """
    if fwhm_channels < 0:
        raise ValueError("عرض القمة بالقنوات لا يجوز أن يكون سالباً")
    if slope_m <= 0:
        raise ValueError("ميل المعايرة الطاقية (keV/channel) يجب أن يكون قيمة موجبة تماماً")
    return float(fwhm_channels * slope_m)


def fwtm_kev(fwtm_channels: float, slope_m: float) -> float:
    """حساب العرض الكامل عند عشر الارتفاع FWTM بوحدة keV.
    
    المعادلة:
        FWTM(keV) = FWTM(channels) * m
    
    المرجع:
        IAEA Procedure Nº: MRNI-507 (Rev. D0), Section 3.7.8, Page 15.
    
    المعاملات:
        fwtm_channels: عرض القمة عند عُشر الارتفاع بالقنوات (>= 0).
        slope_m: معامل المعايرة الطاقية (keV/channel)، (> 0).
    
    العائد:
        قيمة FWTM بوحدة keV.
    """
    if fwtm_channels < 0:
        raise ValueError("عرض القمة عند عُشر الارتفاع لا يجوز أن يكون سالباً")
    if slope_m <= 0:
        raise ValueError("ميل المعايرة الطاقية (keV/channel) يجب أن يكون قيمة موجبة تماماً")
    return float(fwtm_channels * slope_m)


def form_factor(fwtm: float, fwhm: float) -> float:
    """حساب معامل شكل النبضة (نسبة FWTM إلى FWHM).
    
    المعادلة:
        Form Factor = FWTM / FWHM
    
    المرجع:
        IAEA Procedure Nº: MRNI-507 (Rev. D0), Section 3.7.8, Page 15:
        "Calculate the form factor (FWTM/FWHM) and register it in the test report,
         Annex II, the ideal value should be less than 1.9."
    
    المعاملات:
        fwtm: العرض عند عُشر الارتفاع (سواء بالقنوات أو بـ keV) (>= 0).
        fwhm: العرض عند نصف الارتفاع (بنفس وحدة FWTM) (> 0).
    
    العائد:
        نسبة معامل الشكل (Form Factor).
    
    استثناءات:
        ValueError: إذا كانت fwhm <= 0 أو fwtm < 0.
    """
    if fwhm <= 0:
        raise ValueError("قيمة FWHM يجب أن تكون موجبة تماماً لتفادي القسمة على صفر")
    if fwtm < 0:
        raise ValueError("قيمة FWTM لا يجوز أن تكون سالبة")
    return float(fwtm / fwhm)


def energy_resolution_pct(fwhm_kev: float, peak_energy_kev: float = 661.5) -> float:
    """حساب الدقة الطاقية النسبية %R لكواشف الوميض NaI(Tl).
    
    المعادلة:
        %R = (FWHM / E_peak) * 100
    
    المرجع:
        IAEA Procedure Nº: MRNI-504 (Rev. D0), Section 3.5.3, Page 8, Fig. 4:
        "From the Fig.4, the resolution is:
         R(%) = (48.06 keV / 661.5 keV) * 100
         R = 7.26 %"
    
    المعاملات:
        fwhm_kev: عرض القمة عند نصف الارتفاع بوحدة keV (>= 0).
        peak_energy_kev: طاقة القمة بوحدة keV (الافتراضي 661.5 لقمة Cs-137).
    
    العائد:
        نسبة الدقة الطاقية المئوية %R.
    """
    if peak_energy_kev <= 0:
        raise ValueError("طاقة القمة يجب أن تكون قيمة موجبة تماماً")
    if fwhm_kev < 0:
        raise ValueError("قيمة FWHM لا يجوز أن تكون سالبة")
    return float((fwhm_kev / peak_energy_kev) * 100.0)


def relative_efficiency_comparison(net_counts_hpge: float, net_counts_nai: float) -> float:
    """حساب الكفاءة النسبية لكاشف الجرمانيوم بمقارنته بكاشف NaI(Tl) قياسي 3x3 بوصة.
    
    المعادلة:
        Eff(%) = (C1 / C2) * 100
    
    المرجع:
        IAEA Procedure Nº: MRNI-507 (Rev. D0), Section 3.8.4, Page 16:
        "Eff = (C1 / C2) * 100
         where C1 is the net area in the 1332.5 keV peak for HPGe,
         and C2 is the net area obtained with a calibrated spectroscopy system
         with a 3\"x3\" NaI(Tl) scintillation detector for the same time T1."
    
    المعاملات:
        net_counts_hpge: صافي نبضات قمة الكوبالت 1332.5 keV لكاشف HPGe (C1 >= 0).
        net_counts_nai: صافي نبضات القمة لكاشف NaI القياسي (C2 > 0).
    
    العائد:
        الكفاءة النسبية المئوية Eff(%).
    """
    if net_counts_nai <= 0:
        raise ValueError("نبضات كاشف NaI القياسي (C2) يجب أن تكون أكبر من الصفر")
    if net_counts_hpge < 0:
        raise ValueError("صافي نبضات كاشف الجرمانيوم (C1) لا يجوز أن يكون سالباً")
    return float((net_counts_hpge / net_counts_nai) * 100.0)


def relative_efficiency_calibrated_source(
    net_counts_hpge: float,
    activity_bq: float,
    live_time_sec: float,
) -> float:
    """حساب الكفاءة النسبية لكاشف الجرمانيوم باستخدام مصدر كوبالت-60 معاير ومعتمد.
    
    المعادلة:
        Eff(%) = (C1 / (1.2e-3 * A * T1)) * 100
    
    المرجع:
        IAEA Procedure Nº: MRNI-507 (Rev. D0), Section 3.8.3 [labelled under (b)], Page 16:
        "Eff = (C1 / (1.2 * 10^-3 * A * T1)) * 100
         where:
         C1 = net area around 1332.5 keV peak
         A = certified activity in disintegrations per second (Bq)
         T1 = live time in seconds
         1.2 * 10^-3 = factor representing the peak efficiency of a 3\"x3\" NaI(Tl)
                       detector at 25 cm for the 1332.5 keV gamma-ray of Co-60 (IEEE 325 standard)."
    
    المعاملات:
        net_counts_hpge: صافي نبضات قمة 1332.5 keV (C1 >= 0).
        activity_bq: الفعالية المعتمدة للمصدر اللحظية بالبيكرل (A > 0).
        live_time_sec: زمن القياس الحي بالثواني (T1 > 0).
    
    العائد:
        الكفاءة النسبية المئوية Eff(%).
    """
    if activity_bq <= 0:
        raise ValueError("فعالية المصدر الإشعاعي (A) يجب أن تكون أكبر من الصفر")
    if live_time_sec <= 0:
        raise ValueError("الزمن الحي للقياس (T1) يجب أن يكون أكبر من الصفر")
    if net_counts_hpge < 0:
        raise ValueError("صافي نبضات القمة (C1) لا يجوز أن يكون سالباً")
    
    denominator = 1.2e-3 * activity_bq * live_time_sec
    return float((net_counts_hpge / denominator) * 100.0)


def count_rate(total_counts: float, time_seconds: float) -> float:
    """حساب معدل النبضات بالثانية (cps).
    
    المعادلة:
        cps = Counts / Time(s)
    
    المرجع:
        IAEA Procedure Nº: MRNI-507 (Rev. D0), Section 3.7.2, Page 14:
        "obtain a pulse repetition rate not greater than 2 000 cps to minimize dead time effects."
    
    المعاملات:
        total_counts: إجمالي عدد النبضات (>= 0).
        time_seconds: الزمن المستغرق بالثواني (> 0).
    
    العائد:
        معدل النبضات (نبضة/ثانية).
    """
    if time_seconds <= 0:
        raise ValueError("زمن القياس يجب أن يكون أكبر من الصفر")
    if total_counts < 0:
        raise ValueError("عدد النبضات لا يجوز أن يكون سالباً")
    return float(total_counts / time_seconds)


def dead_time_pct(live_time_seconds: float, real_time_seconds: float) -> float:
    """حساب النسبة المئوية لزمن الموت (Dead Time %).
    
    المعادلة:
        Dead Time (%) = (1 - (Live Time / Real Time)) * 100
    
    المرجع:
        IAEA Procedure Nº: MRNI-507, Section 3.7.2, Page 14;
        IAEA-TECDOC-602, Section 2.2;
        Canberra Germanium Detectors Manual.
    
    المعاملات:
        live_time_seconds: الزمن الحي للقياس بالثواني (>= 0).
        real_time_seconds: الزمن الحقيقي الإجمالي (Clock Time) بالثواني (> 0).
    
    العائد:
        النسبة المئوية لزمن الموت (0% إلى 100%).
    """
    if real_time_seconds <= 0:
        raise ValueError("الزمن الحقيقي (Real Time) يجب أن يكون أكبر من الصفر")
    if live_time_seconds < 0:
        raise ValueError("الزمن الحي (Live Time) لا يجوز أن يكون سالباً")
    if live_time_seconds > real_time_seconds:
        raise ValueError("الزمن الحي لا يجوز منطقياً أن يتجاوز الزمن الحقيقي")
    
    return float((1.0 - (live_time_seconds / real_time_seconds)) * 100.0)


def operating_voltage_nai(v_initial: float, v_max: float) -> float:
    """حساب جهد التشغيل الأمثل لكاشف الوميض NaI(Tl).
    
    المعادلة:
        V_0 = (V_i + V_M) / 2
    
    المرجع:
        IAEA Procedure Nº: MRNI-504 (Rev. D0), Section 3.5.1, Page 7:
        "The operating voltage Vo, is around the value:
         Vo = (VM + Vi) / 2
         where Vi is initial voltage at which pulses appear,
         and VM is maximum applied voltage from datasheet."
    
    المعاملات:
        v_initial: الجهد الأولي الذي تظهر عنده نبضات الكاشف (Vi >= 0).
        v_max: أقصى جهد تشغيل مسموح وفق ورقة بيانات الكاشف (VM > Vi).
    
    العائد:
        جهد التشغيل المقترح V_0 (فولت).
    """
    if v_initial < 0:
        raise ValueError("الجهد الأولي Vi لا يجوز أن يكون سالباً")
    if v_max <= v_initial:
        raise ValueError("الجهد الأقصى VM يجب أن يكون أكبر من الجهد الأولي Vi")
    return float((v_initial + v_max) / 2.0)

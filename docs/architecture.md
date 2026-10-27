# وثيقة المعمارية الفنية لمنظومة «كاشف» (KASHIF-QA)
## Architecture & Technical Design Document

- **الإصدار:** 1.1.0 (محدث وفق مراجعة ما قبل بوابة G1)
- **الحالة:** معروض للاعتماد النهائي (Gate G1)
- **المنصة المستهدفة:** Python 3.10+ / PyQt6 (تطبيق مكتبي متكامل Desktop Offline-First)
- **نمط التصميم:** معمارية متعددة الطبقات منفصلة الاهتمامات (Clean Layered Architecture) مستوحاة من منظومة RadRiskMap
- **المرجعية القياسية:** وثيقة الوكالة الدولية للطاقة الذرية IAEA Procedure Nº: MRNI-507 (Rev. D0)

---

## 1. القرارات التقنية وحزم الاعتماديات (Tech Stack & Dependencies)

تم حسم الاعتماديات التقنية بدقة لمنع أي تعارض، وتوحيدها كما يلي:

| المكون / الحزمة | الإصدار المعتمد | الدور الفني والسبب الهندسي |
| :--- | :--- | :--- |
| **Python** | `3.10+` | اللغة الأساسية للحسابات العلمية والفيزيائية ومعالجة البيانات الطيفية. |
| **PyQt6** | `>=6.5.0` | إطار واجهات المستخدم الرسومية المكتبي (دعم ممتاز للعربية RTL والسمات المعملية). |
| **pyqtgraph** | `>=0.13.0` | رسم تفاعلي فائق السرعة للأطياف متعددة القنوات ورسم منحنيات النبضات اللحظية. |
| **matplotlib** | `>=3.7.0` | توليد وتصدير المخططات والرسوم البيانية الثابتة لإدراجها في تقارير PDF الرسمية. |
| **pydantic** | `>=2.0.0` | التحقق الصارم من صحة النماذج والبيانات وتحويلها إلى JSON للقطات الفحص (Snapshots). |
| **argon2-cffi** | `>=23.1.0` | تجزئة وتشفير كلمات المرور (مع توفير بديل داخلي آمن عبر `hashlib.pbkdf2_hmac` من مكتبة بايثون القياسية). |
| **ReportLab** | `>=4.0.0` | محرك بناء وتصدير وثائق وتقارير الفحص المعتمدة بصيغة PDF. |
| **arabic-reshaper** | `>=3.0.0` | إعادة تشكيل الحروف والكلمات العربية لتجهيزها لمحرك ReportLab. |
| **python-bidi** | `>=0.4.2` | ضبط اتجاه النصوص ثنائية اللغة من اليمين إلى اليسار (Bi-directional RTL). |
| **Amiri Font** | `OFL License` | خط عربي طباعي رصين مفتوح المصدر يُضمن داخل مجلد الأصول `assets/fonts/` لطباعة التقارير. |
| **openpyxl** | `>=3.1.0` | تصدير واستيراد سجلات الكواشف والفحوصات إلى جداول Excel (`.xlsx`). |
| **pytest** | `>=7.4.0` | إطار الاختبارات الآلية لكافة دوال الحسابات الفيزيائية وقواعد القرار. |
| **pytest-qt** | `>=4.2.0` | اختبار عناصر وتفاعلات واجهة المستخدم الرسومية لـ PyQt6 آلياً. |
| **pytest-cov** | `>=4.1.0` | قياس نسبة تغطية الاختبارات الآلية (تحقيق مستهدف $\ge 90\%$ لحزمة `core/`). |

---

## 2. الهيكل الهرمي لحزم المنظومة (Package Hierarchy)

تم توحيد جذر الحزمة حصرياً كـ `kashif/`، مع نقل خدمات الأمان خارج طبقة قاعدة البيانات إلى `kashif/services/`:

```text
kashif/
├── __init__.py
│
├── core/                        # النواة الفيزيائية والمنطق الحسابي المجرد (نقي 100% دون اعتماد على UI أو DB)
│   ├── __init__.py
│   ├── models.py                # نماذج البيانات المهيكلة والتحقق الصارم عبر Pydantic
│   ├── physics.py               # معادلات IAEA: حساب FWHM، معامل الشكل FWTM/FWHM، الكفاءة النسبية
│   ├── decay.py                 # حاسبة الاضمحلال الإشعاعي (Co-60 و Cs-137) وتوحيد وحدات التحويل
│   ├── rules.py                 # محرك قواعد القرار الرقمية الصريحة (Conformity Rules Engine)
│   └── spectrum.py              # حساب معاملي المعايرة الخطية (m و b) والتحقق من القمم غاوسياً
│
├── services/                    # طبقة الخدمات والمنطق التطبيقي والأمان
│   ├── __init__.py
│   ├── security.py              # التوثيق، إدارة الجلسات، تجزئة كلمات المرور عبر Argon2id/PBKDF2
│   ├── audit_service.py         # خدمة تسجيل العمليات وحساب الهاش السلسلي التشفيري
│   └── snapshot_service.py      # خدمة توليد لقطات التجميد التاريخية للفحص المعتمد
│
├── db/                          # طبقة التخزين وإدارة قاعدة بيانات SQLite
│   ├── __init__.py
│   ├── connection.py            # إدارة الاتصال بقاعدة بيانات SQLite وتفعيل نمط WAL والقيود الخارجية
│   ├── schema.py                # تعريف الجداول ومخطط البيانات الأساسي والـ Triggers
│   ├── migrations.py            # محرك إدارة ترقيات المخطط (Schema Migrations) وجدول التاريخ
│   ├── repositories.py          # فئات مستودعات التخزين للكيانات المختلفة (Repository Pattern)
│   └── backup.py                # محرك إنشاء واستعادة النسخ الاحتياطية المشفرة
│
├── reports/                     # محرك التقارير والطباعة الرسمية
│   ├── __init__.py
│   ├── pdf_generator.py         # بناء تقرير الفحص الفني المطابق لأقسام وحقول IAEA Annex II
│   ├── excel_exporter.py        # تصدير البيانات والسجلات إلى ملفات Excel
│   └── labels_generator.py      # توليد بطاقات وملصقات الحالة الفنية الملونة للأجهزة
│
├── gui/                         # طبقة واجهة المستخدم (PyQt6)
│   ├── __init__.py
│   ├── app.py                   # تهيئة التطبيق والـ Event Loop وإدارة الاستثناءات العامة
│   ├── main_window.py           # النافذة الرئيسية وشريط الأدوات والتنقل بين الشاشات
│   ├── i18n.py                  # محرك الترجمة والتبديل الحي بين العربية والإنجليزية
│   ├── theme.py                 # إدارة السمات المعملية (Dark/Light QSS) وألوان الهوية المؤسسية
│   ├── idle_lock.py             # آلية قفل الشاشة عند الخمول لحماية البيانات
│   │
│   ├── views/                   # الشاشات الرئيسية
│   │   ├── dashboard_view.py    # لوحة المتابعة الإحصائية وحالة الكواشف والتنبيهات
│   │   ├── detectors_view.py    # شاشة إدارة سجل الكواشف (HPGe و NaI)
│   │   ├── instruments_view.py  # شاشة إدارة أجهزة الفحص مع تنبيهات انتهاء صلاحية المعايرة
│   │   ├── sources_view.py      # شاشة سجل المصادر المشعة مع حاسبة الاضمحلال اللحظية
│   │   ├── history_view.py      # شاشة استعراض وأرشفة الفحوصات والتقارير المعتمدة
│   │   └── audit_view.py        # شاشة سجل التدقيق الأمني وإدارة المستخدمين
│   │
│   ├── wizards/                 # معالجات الفحص الإرشادية خطوة بخطوة
│   │   ├── hpge_wizard.py       # معالج فحص كاشف الجرمانيوم ذو المراحل الخمس
│   │   └── nai_wizard.py        # معالج فحص كاشف يوديد الصوديوم الوميضي
│   │
│   ├── widgets/                 # المكونات الرسومية المخصصة
│   │   ├── spectrum_plot.py     # ويدجت رسم الطيف الطاقي والقمم وحساب FWHM
│   │   ├── pulse_scope.py       # ويدجت العرض التفاعلي للنبضات ومطابقة الإشارة المرجعية
│   │   └── status_badge.py      # شارات الحالة الفنية الملونة (In Specs, Out of Specs, Failure)
│   │
│   └── assets/                  # الأصول والموارد المضمنة
│       ├── kashif_logo.png      # شعار المنظومة الرسمي المعتمد
│       ├── fonts/               # الخطوط المضمنة (Amiri-Regular.ttf, Amiri-Bold.ttf)
│       └── diagrams/            # مخططات التوصيلات الهندسية
│
├── tests/                       # اختبارات الوحدة والتكامل الآلية
│   ├── __init__.py
│   ├── test_physics.py          # اختبارات دقة معادلات IAEA (FWHM، الكفاءة، معامل الشكل)
│   ├── test_decay.py            # اختبارات دقة حسابات الاضمحلال الإشعاعي وثوابت نصف العمر
│   ├── test_rules.py            # اختبارات مصفوفة قواعد القرار والتحقق الفني
│   ├── test_spectrum.py         # اختبارات معايرة الطاقة الخطية وتحليل القمم
│   ├── test_db.py               # اختبارات قاعدة البيانات والـ Migrations ومستودعات التخزين
│   ├── test_security.py         # اختبارات التوثيق والتشفير وسلسلة الهاش لسجل التدقيق
│   └── test_reports.py          # اختبارات سلامة توليد تقارير PDF ودعم اللغة العربية
│
├── docs/                        # حزمة التوثيق والتصميم
│   ├── requirements.md          # وثيقة المتطلبات المعتمدة (المرجع الأعلى)
│   ├── architecture.md          # وثيقة المعمارية ومخطط البيانات
│   ├── tasks.md                 # خطة المراحل والمهام التفصيلية
│   ├── decision-rules.md        # مصفوفة قواعد القرار الصريحة
│   ├── gates.md                 # ميثاق بوابات الجودة (G0..G5)
│   ├── DECISIONS.md        # سجل الأسئلة المفتوحة والقرارات المعلّقة
│   ├── CHANGELOG_G1.md          # سجل تعديلات ما قبل بوابة G1
│   └── adr/                     # سجل القرارات المعمارية المعتمدة (ADR-0001..0004)
│
├── run.py                       # نقطة التشغيل الرئيسية للتطبيق
├── requirements.txt             # ملف متطلبات بيئة بايثون
└── PROJECT_STATE.md             # ملف حالة المشروع المتجدد
```

---

## 3. مخطط قاعدة البيانات المصحح (Corrected Database Schema & ER Diagram)

أعيد تصميم المخطط بالكامل ليفصل بين سجل الفحص العام والحالة واللقطات المجمدة، وبين تفاصيل القياسات المتخصصة، مع دعم التتبع وإعادة القياس وسجل التدقيق المنيع:

```mermaid
erDiagram
    USERS ||--o{ INSPECTIONS : conducts
    USERS ||--o{ AUDIT_LOG : triggers
    DETECTORS ||--o{ INSPECTIONS : tested_in
    INSPECTIONS ||--o| INSPECTION_HPGE_MEASUREMENTS : has_hpge_data
    INSPECTIONS ||--o| INSPECTION_NAI_MEASUREMENTS : has_nai_data
    INSPECTIONS ||--o{ INSPECTION_PEAKS : analyzes
    INSPECTIONS ||--o{ INSPECTION_INSTRUMENTS : utilizes
    INSPECTIONS ||--o{ INSPECTION_SOURCES : calibrates_with
    INSPECTIONS ||--o{ ATTACHMENTS : includes
    INSPECTIONS ||--o{ MEASUREMENT_RETRIES : records_history
    INSTRUMENTS ||--o{ INSPECTION_INSTRUMENTS : assigned_in
    SOURCES ||--o{ INSPECTION_SOURCES : applied_in

    USERS {
        int id PK
        string username UK
        string password_hash
        string full_name
        string role "TECHNICIAN | LAB_MANAGER | ADMIN"
        boolean is_active
        boolean must_change_password
        datetime last_login_at
        datetime created_at
    }

    DETECTORS {
        int id PK
        string code UK
        string detector_type "HPGE | NAI"
        string manufacturer
        string model
        string serial_number
        string crystal_type "COAXIAL | PLANAR"
        string window_type "BERYLLIUM | ALUMINUM | CARBON_COMPOSITE"
        string beryllium_window_status "INTACT | SCRATCHED | BROKEN"
        string preamp_type "RESISTIVE | TRP | OTHER"
        float nominal_voltage
        string voltage_polarity "POSITIVE | NEGATIVE"
        float nominal_fwhm_kev
        float nominal_efficiency_pct
        float diameter_inch
        float height_inch
        string status "ACTIVE | MAINTENANCE | DECOMMISSIONED"
        text notes
        datetime created_at
    }

    INSTRUMENTS {
        int id PK
        string instrument_type "NIM_BIN | HV_SUPPLY | AMPLIFIER | MCA | SCOPE"
        string manufacturer
        string model
        string serial_number
        float ripple_mv
        float regulation_pct
        int channels_count
        float shaping_range_min_us
        float shaping_range_max_us
        date cal_date
        date cal_expiry
        boolean is_in_service
        text notes
        datetime created_at
    }

    SOURCES {
        int id PK
        string nuclide "Co-60 | Cs-137"
        string source_code UK
        string serial_number
        float cert_activity_bq
        date cert_date
        float half_life_days
        string certificate_number
        text notes
        datetime created_at
    }

    INSPECTIONS {
        int id PK
        string report_number UK "Issued on approval only"
        int detector_id FK
        int technician_id FK
        int approver_id FK "Nullable until approved"
        date inspection_date
        string status "DRAFT | PENDING_APPROVAL | APPROVED | REJECTED"
        string verdict "PASS | OUT_OF_SPEC | FAILURE"
        text rejection_reason
        text manager_notes
        string technician_signature_token
        string manager_signature_token
        datetime approved_at
        int superseded_by_id FK "Nullable: if corrected later"
        text snapshot_json "Frozen master data & calculations snapshot"
        datetime created_at
        datetime updated_at
    }

    INSPECTION_HPGE_MEASUREMENTS {
        int id PK
        int inspection_id FK UK
        float cooling_hours
        boolean cryostat_intact
        string beryllium_window_status "INTACT | SCRATCHED | BROKEN"
        boolean vacuum_intact
        boolean cryostat_wet_or_frozen
        
        float v_pos_24
        float v_neg_24
        float v_pos_12
        float v_neg_12
        float v_pos_6
        float v_neg_6
        float applied_voltage
        string applied_polarity
        string preamp_type "RESISTIVE | TRP"
        
        float shaping_time_us
        float coarse_gain
        float fine_gain
        string baseline_restorer
        string pole_zero_setting
        float amp_output_amplitude
        
        float total_count_rate_cps
        float dead_time_pct
        float calibration_slope_m
        float calibration_intercept_b
        float measured_fwhm_kev
        float measured_fwtm_kev
        float form_factor
        
        string efficiency_method "COMPARISON_NAI | CALIBRATED_SOURCE"
        float live_time_sec
        float relative_efficiency_pct
    }

    INSPECTION_NAI_MEASUREMENTS {
        int id PK
        int inspection_id FK UK
        float operating_voltage
        float cs137_centroid_channel
        float fwhm_channels
        float fwhm_kev
        float resolution_r_pct
        float gain_drift_channels
        float pmt_leakage_current_ua
    }

    INSPECTION_PEAKS {
        int id PK
        int inspection_id FK
        string detector_type "HPGE | NAI"
        string peak_name "Co-60 Peak 1 | Co-60 Peak 2 | Cs-137 Peak"
        float energy_reference_kev
        float centroid_channel
        float fwhm_channels
        float fwhm_kev
        float fwtm_channels
        float fwtm_kev
        float net_area_counts
        float live_time_sec
    }

    INSPECTION_INSTRUMENTS {
        int id PK
        int inspection_id FK
        int instrument_id FK
        string instrument_role "NIM_BIN | HV_SUPPLY | AMPLIFIER | MCA | SCOPE"
        text notes
    }

    INSPECTION_SOURCES {
        int id PK
        int inspection_id FK
        int source_id FK
        string source_role "CALIBRATION | EFFICIENCY"
        float distance_cm
        float decay_corrected_activity_bq
    }

    ATTACHMENTS {
        int id PK
        int inspection_id FK
        string file_path
        string file_hash_sha256
        string file_type "PREAMP_WAVEFORM | AMP_WAVEFORM | SPECTRUM_PLOT | OTHER"
        string caption
        datetime created_at
    }

    MEASUREMENT_RETRIES {
        int id PK
        int inspection_id FK
        int stage_number
        int attempt_number
        string reason_for_retry
        string corrective_action_taken
        text previous_values_json
        text new_values_json
        datetime created_at
    }

    AUDIT_LOG {
        int id PK
        datetime timestamp
        int user_id FK
        string action "LOGIN | FAILED_LOGIN | CREATE | UPDATE | SUBMIT | APPROVE | REJECT | BACKUP | RESTORE | EXPORT"
        string entity_type
        int entity_id
        text old_value_json
        text new_value_json
        string prev_hash
        string row_hash "SHA256(prev_hash + row_data)"
    }

    SCHEMA_MIGRATIONS {
        int version PK
        datetime applied_at
        text description
    }
```

### 3.1 قواعد النزاهة والتفرد المطبقة في قاعدة البيانات
1. **قيود التفرد المركبة (Composite Uniqueness):**
   - في جدول `DETECTORS`: `UNIQUE(manufacturer, serial_number)`.
   - في جدول `INSTRUMENTS`: `UNIQUE(manufacturer, serial_number)`.
   - في جدول `SOURCES`: `UNIQUE(source_code)` و `UNIQUE(nuclide, serial_number)`.
2. **سياسة ترقيم التقارير (Report Numbering):**
   - حقل `report_number` في جدول `INSPECTIONS` يظل فارغاً (`NULL`) أثناء مرحلة المسودة `DRAFT` و `PENDING_APPROVAL`.
   - يُولّد الرقم التسلسلي تلقائياً فقط عند تنفيذ إجراء الاعتماد (`APPROVED`) لضمان عدم حدوث أي فجوات تسلسلية في السجل الرسمي للمعمل.
3. **لقطات التجميد التاريخية (Snapshots):**
   - عند الاعتماد، يقوم النظام بتوليد كائن JSON كامل في حقل `snapshot_json` يحوي نسخة طبق الأصل من بيانات الكاشف والأجهزة والمصادر ومصفوفة الحسابات كما كانت لحظة الاعتماد، لضمان عدم تأثر الفحوصات القديمة بأي تعديل مستقبلي في بطاقة الكاشف أو معايرة الأجهزة.
4. **سجل التدقيق المنيع (Tamper-Evident Audit Ledger):**
   - يتضمن كل صف في `audit_log` حقل `prev_hash` وحقل `row_hash` محسوبين باستخدام خوارزمية SHA-256، مما ينشئ سلسلة غير قابلة للتزوير (Cryptographic Hash Chain).
   - تفعيل Triggers في SQLite تمنع حذف أو تعديل أي سجل في جدول التدقيق:
     ```sql
     CREATE TRIGGER IF NOT EXISTS prevent_audit_update
     BEFORE UPDATE ON audit_log
     BEGIN
         SELECT RAISE(FAIL, 'Updates are strictly prohibited on audit_log table.');
     END;

     CREATE TRIGGER IF NOT EXISTS prevent_audit_delete
     BEFORE DELETE ON audit_log
     BEGIN
         SELECT RAISE(FAIL, 'Deletions are strictly prohibited on audit_log table.');
     END;
     ```
5. **جدول ترقيات المخطط (`schema_migrations`):**
   - جدول مخصص لتتبع إصدارات مخطط قاعدة البيانات المطبقة تاريخياً وضمان انتقال البيانات بسلاسة بين إصدارات البرنامج المستقبلية.

---

## 4. تدفق البيانات والعمليات الفنية (Data Flow & Technical Workflow)

1. **التهيئة والتحقق الأولي:**
   - يختار الفني كاشف الجرمانيوم أو يوديد الصوديوم، ويقوم النظام تلقائياً بملء البيانات الاسمية والتأكد من سريان معايرة الأجهزة والمصادر المختارة (مع إطلاق تحذير إن كانت منتهية الصلاحية).
2. **مسار الخطوات الخمس:**
   - **المرحلة 1 (الحاوية):** تسجيل فحص النافذة والتبريد. إذا كان التبريد $<6\text{ h}$، يُحظر رفع الجهد وتُسجل حالة انتظار `WAIT`. إذا كان هناك ثلج أو كسر في النافذة، يُصدر حكم `FAILURE` ويُقفل الفحص.
   - **المرحلة 2 (المضخم الأولي):** تسجيل قياسات سلة NIM، وتأكيد القطبية، والرفع التدريجي للجهد (100V كل 10 ثوانٍ) مع استعراض الإشارة المرجعية وحفظ صورة الإشارة الملتقطة.
   - **المرحلة 3 (المضخم الرئيسي):** تسجيل أزمنة التشكيل والكسب وفحص الـ Pole-Zero.
   - **المرحلة 4 (MCA ومعايرة الطاقة):** إدخال مواقع قمتي الكوبالت-60 وقيم FWHM بالقنوات والـ keV. يقوم `spectrum.py` بحساب الميل $m$ ونقطة التقاطع $b$ تلقائياً والتحقق من أن معامل الشكل $\text{FWTM}/\text{FWHM} < 1.9$ ومن نسبة FWHM إلى القيمة الاسمية للكاشف.
   - **المرحلة 5 (الكفاءة النسبية):** حساب الكفاءة بطريقة المقارنة مع NaI أو المصدر المعاير بعد خصم الاضمحلال التلقائي.
3. **إعادة القياس عند الحاجة (Retry Loop):**
   - إذا ظهر حكم `OUT_OF_SPEC`، ينفذ الفني الإجراء التصحيحي (تجفيف، ضبط P/Z) ويسجل محاولة قياس جديدة تُحفظ في `measurement_retries`.
4. **الاعتماد وتجميد السجل:**
   - يراجع مدير المعمل الفحص في شاشة `history_view.py`. عند الضغط على "اعتماد":
     - يُفحص قيد فصل المهام (`technician_id != approver_id`).
     - يُولد رقم التقرير التسلسلي الرسمي التالي.
     - تُجمد البيانات في `snapshot_json` وتتحول الحالة إلى `APPROVED`.
     - يُسجل الحدث التشفيري في `audit_log`.
     - يُبنى ملف الـ PDF المطابق لـ IAEA Annex II ويُوقع رقمياً.

---

## 5. نطاق الوحدات المتخصصة (Spectrum & Scope Modules Scope)

- **وحدة `kashif/core/spectrum.py`:**
  - *نطاق العمل في v1.0:* وحدة حسابات ومعالجة رياضية مجردة. تستقبل مواقع القنوات الاسمية والمرجعية المدخلة يدوياً من الفني، وتقوم بإجراء الانحدار الخطي البسيط لحساب معاملي المعايرة ($m$ و $b$) وتحويل القنوات لطاقة، والتحقق الحسابي من اتساق FWHM مع مقياس الطاقة. كما توفر دوالاً لتوليد منحنى غاوسي تمثيلي للقمم لعرضه في الواجهة والتقارير.
  - *خارج النطاق:* لا تشمل قراءة أو فك تشفير ملفات الأطياف الخام التابعة لشركات الأجهزة (مثل `.spe`, `.chn`).
- **وحدة `kashif/gui/widgets/pulse_scope.py`:**
  - *نطاق العمل في v1.0:* أداة مساعدة بصرية تفاعلية (Interactive Visual Reference). تعرض رسوماً بيانية تفاعلية دقيقة للأشكال الموجية النموذجية المذكورة في وثيقة IAEA (الشكل 6 أ، 6 ب، والشكل 7 لمضخمات TRP، والشكل 9 و 10 للـ Pole-Zero) لمساعدة الفني على مقارنة ما يراه على شاشة راسم الذبذبات الواقعي ومطابقته واختيار النمط المناسب أو إرفاق لقطة شاشة حقيقية.
  - *خارج النطاق:* ليست برنامج راسم إشارات رقمي متصل بعتاد قياس فيزيائي عبر USB/GPIB.

---

## 6. المخاطر والقرارات المعمارية المفتوحة (Risks & Open Architectural Decisions)

تم حصر وتوثيق المخاطر المعمارية المحتملة وطرق التعامل معها في الإصدار الأول:

### 6.1 نمط النشر ومخاطر تشغيل SQLite على الشبكة
- **الخطر:** إذا حاول المعمل وضع ملف قاعدة بيانات SQLite على مجلد شبكي مشترك (Windows Network SMB Share) ليعمل عليه أكثر من جهاز في نفس الوقت، فإن تفعيل نمط `WAL` يسبب تلفاً فورياً في ملفات القفل المشتركة (`.shm` و `.wal`) لأن بروتوكول SMB لا يدعم تقنيات الذاكرة المشتركة المطلوبة لـ SQLite WAL.
- **إجراء التخفيف:** تقييد المنظومة في الإصدار v1.0 لتعمل على **جهاز معملي واحد مستقل (Single-Workstation Offline)** مع توفير أدوات النسخ الاحتياطي التلقائي (انظر السؤال المفتوح 1 في `docs/DECISIONS.md`).

### 6.2 حقوق الملكية الفكرية لرسوم وتوضيحات وثيقة IAEA
- **الخطر:** استخدام الصور الفوتوغرافية الأصلية والمخططات التوضيحية المأخوذة مباشرة من وثيقة IAEA MRNI-507 (الأشكال 1 إلى 11) داخل واجهة البرنامج وتوزيعها تجارياً أو مؤسسياً قد يثير مسائل حقوق ملكية فكرية.
- **إجراء التخفيف:** إعادة رسم جميع المخططات التوصيلية والكتلية (Schematic Wiring Diagrams) هندسياً كرسوم متجهة خاصة بالمنظومة (Clean Vector SVGs) مستوحاة من الوثيقة ومطابقة للمواصفة القياسية دون نسخ صور الوثيقة حرفياً (تم إدراج مهمة خاصة بذلك في `docs/tasks.md`).

### 6.3 دقة المطابقة البصرية لملحق IAEA Annex II مع دعم اللغة العربية
- **الخطر:** محاولة استنساخ استمارة الوكالة الدولية بدقة بكسلية جامدة قد يؤدي إلى تشوه التنسيق الطباعي عند دعم اللغة العربية والنصوص ثنائية الاتجاه (RTL/LTR) في صفحات A4.
- **إجراء التخفيف:** تم تعديل المتطلب المعماري صراحة ليصبح: "تطابق دقيق ومحكم في الحقول، وترتيبها، وأقسامها المعتمدة في وثيقة IAEA Annex II مع دعم كامل للغة العربية (RTL) وتنسيق طباعة A4 أنيق ومرن" عبر مكتبة ReportLab وخط Amiri المضمن (انظر ADR-0004).

### 6.4 تجميد السجلات التاريخية وتعديل بيانات الكواشف
- **الخطر:** تعديل بيانات الكاشف الاسمية في السجل مستقبلاً (مثل تعديل جهد التشغيل أو الكفاءة) قد يؤثر بأثر رجعي على التقارير القديمة المعتمدة إذا كانت التقارير تقرأ مباشرة من جدول الكواشف عبر المفاتيح الخارجية.
- **إجراء التخفيف:** تم تطبيق آلية لقطات التجميد التامة (`snapshot_json`)؛ حيث يُعزل التقرير المعتمد كلياً في كبسولة مجمدة غير قابلة للتعديل تعكس الحالة الواقعية للأجهزة والكاشف يوم الفحص فقط.

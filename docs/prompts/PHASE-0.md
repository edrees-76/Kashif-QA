# برومبت تنفيذ Phase 0 — الهيكل العظمي ونواة الأمان (لـ Antigravity)

أنت **المنفّذ** لمنظومة «كاشف» (KASHIF-QA). المراجع الخارجي سيدقق عملك. نفّذ Phase 0 فقط.

## اقرأ أولاً (إلزامي، بالترتيب)
`PROJECT_STATE.md` ← `docs/requirements.md` ← `docs/architecture.md` ← `docs/tasks.md` (قسم Phase 0) ← `docs/gates.md` ← `docs/DECISIONS.md` ← `docs/adr/`.
عند التعارض: requirements ثم architecture ثم tasks.

## قيود صارمة
1. اعمل على فرع `phase-0-skeleton` المنشأ من `main`. ممنوع `git commit` و`git push` و`git merge` إلا بطلب صريح من المدير في نفس الدورة.
2. نفّذ **TASK-0.1 إلى TASK-0.6 فقط**. لا ميزات إضافية، لا جداول غير Migration 001 (`users`, `audit_log`, `schema_migrations`)، ولا شاشات غير النافذة الرئيسية.
3. لا تخترع قيماً فيزيائية أو عتبات. لا حاجة لها في Phase 0.
4. لا تدّعِ نجاح اختبار أو تشغيل لم تشغّله فعلياً. الصق مخرجات الأوامر الحقيقية (`pip check`, `pytest`).
5. لا بيانات إنتاج. بيانات اختبارية فقط.
6. الصور يلتقطها المدير يدوياً.
7. لا تُعدّل ملفات `docs/` إلا `PROJECT_STATE.md` عند طلب المدير.

## متطلبات أمنية محددة (سيُدقّق عليها)
- كلمات المرور: Argon2id (بديل PBKDF2) ولا تُسجَّل أو تُطبع في أي سجل.
- أول مستخدم `admin` بـ `must_change_password = TRUE`، وكلمته الابتدائية لا تُكتب ثابتة في الكود ولا في الـ Git.
- Master Recovery Key: يُولَّد عشوائياً بـ `secrets`، يُعرض مرة واحدة فقط، ويُخزَّن مجزَّأً (hash) لا نصاً صريحاً، واستخدامه يُسجَّل في `audit_log`.
- `audit_log`: `prev_hash` و`row_hash` (SHA-256) وTriggers تمنع UPDATE وDELETE، وأحداث LOGIN وFAILED_LOGIN.
- كل استعلام SQL بمعاملات (parameterized) دون دمج نصوص.
- `kashif_error.log` لا يحتوي كلمات مرور أو مفاتيح.
- `.gitignore` يستثني قاعدة البيانات والسجلات والبيئة الافتراضية.

## البنية
حزمة جذرية `kashif/` فيها `core/ services/ db/ reports/ gui/` كما في architecture.md. `security.py` داخل `services/`، وليس `db/`.

## التسليم في نهاية الدورة
قدّم تقريراً يذكر: الملفات المنشأة، ناتج `pip check` و`pytest tests/test_db.py -v` كما ظهر فعلاً، وما لم يكتمل أو انحرف عن المواصفة، ثم **توقف وانتظر المراجعة**.

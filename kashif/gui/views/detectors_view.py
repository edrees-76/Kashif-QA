"""Detectors management view for KASHIF-QA.

Handles registry of Coaxial HPGe and NaI(Tl) detectors:
- Table listing with search, facility filtering, and sorting
- Add/Edit dialog with strict validation on submit
- Detail inspection dialog
- Safe retirement with confirmation dialog
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from kashif.gui.dialog_utils import (
    enable_dialog_window_controls,
    adjust_dialog_size_to_screen,
)

from kashif.core.models import (
    CrystalType,
    DetectorType,
    HPGeDetector,
    NaIDetector,
    PreampType,
    SemiconductorType,
    VoltagePolarity,
    WindowStatus,
    WindowType,
)
from kashif.db.repositories import (
    DetectorRepository,
    DuplicateEntityError,
    EntityNotFoundError,
)
from kashif.gui.bidi import format_mixed_text, isolate_ltr
from kashif.gui.theme import get_stylesheet
from kashif.services.session import Session


def _show_msg(parent: QWidget, level: str, title: str, text: str) -> None:
    """Display message box in UI."""
    if level == "info":
        QMessageBox.information(parent, title, text)
    elif level == "warn":
        QMessageBox.warning(parent, title, text)
    elif level == "error":
        QMessageBox.critical(parent, title, text)


def _ask_confirm(parent: QWidget, title: str, text: str) -> bool:
    """Prompt confirmation in UI via QMessageBox.question."""
    reply = QMessageBox.question(
        parent,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return reply == QMessageBox.StandardButton.Yes


def _exec_dlg(dlg: QDialog) -> int:
    """Execute dialog."""
    return dlg.exec()


class AddEditDetectorDialog(QDialog):
    """Dialog for creating or updating a detector record."""

    def __init__(
        self,
        repo: DetectorRepository,
        session: Session,
        detector_id: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.session = session
        self.detector_id = detector_id

        title = "تعديل بيانات كاشف" if detector_id else "إضافة كاشف جديد — منظومة كاشف"
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())
        enable_dialog_window_controls(self)

        self._init_ui()
        if self.detector_id:
            self._load_existing_data()
        adjust_dialog_size_to_screen(self, preferred_width=620, preferred_height=750)

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 16, 20, 16)
        root_layout.setSpacing(12)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        # 1. Detector Type Selection (HPGe vs NaI - No Planar!)
        type_group = QGroupBox("نوع الكاشف الإشعاعي")
        type_layout = QVBoxLayout(type_group)
        type_layout.setSpacing(8)

        self.radio_hpge = QRadioButton(f"كاشف الجرمانيوم عالي النقاء {isolate_ltr('(Coaxial HPGe)')}")
        self.radio_nai = QRadioButton(f"كاشف يوديد الصوديوم الوميضي {isolate_ltr('(NaI(Tl))')}")
        self.radio_hpge.setChecked(True)

        self.radio_hpge.toggled.connect(self._on_type_changed)

        type_layout.addWidget(self.radio_hpge)
        type_layout.addWidget(self.radio_nai)
        layout.addWidget(type_group)

        # 2. Common Fields
        common_group = QGroupBox("البيانات الأساسية")
        form_common = QFormLayout(common_group)
        form_common.setSpacing(10)

        self.edit_code = QLineEdit()
        self.edit_manufacturer = QLineEdit()
        self.edit_model = QLineEdit()
        self.edit_serial = QLineEdit()
        self.edit_facility = QLineEdit()

        form_common.addRow("الرمز المعملي الفريد (*):", self.edit_code)
        form_common.addRow("الشركة الصانعة (*):", self.edit_manufacturer)
        form_common.addRow("طراز الكاشف (*):", self.edit_model)
        form_common.addRow("الرقم التسلسلي (*):", self.edit_serial)
        form_common.addRow("اسم المعمل / المنشأة:", self.edit_facility)
        layout.addWidget(common_group)

        # 3. HPGe Specific Group
        self.hpge_group = QGroupBox("المواصفات الفنية لكاشف HPGe")
        form_hpge = QFormLayout(self.hpge_group)
        form_hpge.setSpacing(10)

        self.combo_crystal = QComboBox()
        self.combo_crystal.addItem("محوري (Coaxial)", CrystalType.COAXIAL.value)
        # Planar is intentionally not added for v1.0

        self.combo_semiconductor = QComboBox()
        self.combo_semiconductor.addItem("— اختر نوع شبه الموصل (*) —", "")
        self.combo_semiconductor.addItem(f"شبه موصل موجب ({isolate_ltr('P-Type')})", SemiconductorType.P_TYPE.value)
        self.combo_semiconductor.addItem(f"شبه موصل سالب ({isolate_ltr('N-Type')})", SemiconductorType.N_TYPE.value)

        self.combo_cert_polarity = QComboBox()
        self.combo_cert_polarity.addItem("— غير موثقة بالشهادة —", "")
        self.combo_cert_polarity.addItem(f"موجبة (+) ({isolate_ltr('Positive')})", VoltagePolarity.POSITIVE.value)
        self.combo_cert_polarity.addItem(f"سالبة (-) ({isolate_ltr('Negative')})", VoltagePolarity.NEGATIVE.value)

        self.edit_cert_ref = QLineEdit()
        self.edit_cert_ref.setPlaceholderText("رقم أو اسم شهادة المصنّع...")

        self.edit_cooling_hours = QLineEdit()
        self.edit_cooling_hours.setPlaceholderText("ساعات التبريد من الشهادة (الافتراضي 6.0)")

        self.edit_hv_ramp = QLineEdit()
        self.edit_hv_ramp.setText("10.0")
        self.edit_hv_ramp.setPlaceholderText("الحد الأقصى لمعدل رفع الجهد (V/s) - الافتراضي 10.0")

        self.edit_hv_ramp_ref = QLineEdit()
        self.edit_hv_ramp_ref.setPlaceholderText("مرجع دليل المصنّع (إلزامي إذا اختلف المعدل عن 10.0 V/s)")

        self.combo_window_type = QComboBox()
        for wt in WindowType:
            self.combo_window_type.addItem(wt.value, wt.value)

        self.combo_window_status = QComboBox()
        self.combo_window_status.addItem("سليمة (INTACT)", WindowStatus.INTACT.value)
        self.combo_window_status.addItem("مخدوشة (SCRATCHED)", WindowStatus.SCRATCHED.value)
        self.combo_window_status.addItem("مكسورة (BROKEN)", WindowStatus.BROKEN.value)

        self.combo_preamp = QComboBox()
        self.combo_preamp.addItem("مقاومة وتفريغ أسي (RESISTIVE)", PreampType.RESISTIVE.value)
        self.combo_preamp.addItem("تفريغ ترانزستوري نبضي (TRP)", PreampType.TRP.value)
        self.combo_preamp.addItem("أخرى (OTHER)", PreampType.OTHER.value)

        self.edit_voltage = QLineEdit()
        self.edit_voltage.setPlaceholderText("مثال: 3000")

        self.combo_polarity = QComboBox()
        self.combo_polarity.addItem("موجبة (+)", VoltagePolarity.POSITIVE.value)
        self.combo_polarity.addItem("سالبة (-)", VoltagePolarity.NEGATIVE.value)

        self.edit_fwhm = QLineEdit()
        self.edit_fwhm.setPlaceholderText("FWHM الاسمي عند 1332.5 keV (مثال: 1.85)")

        self.edit_eff = QLineEdit()
        self.edit_eff.setPlaceholderText("الكفاءة النسبية الاسمية % (مثال: 20.0)")

        self.edit_hpge_diam = QLineEdit()
        self.edit_hpge_diam.setPlaceholderText("القطر بالبوصة (مثال: 2.0)")

        self.edit_hpge_height = QLineEdit()
        self.edit_hpge_height.setPlaceholderText("الطول بالبوصة (مثال: 2.0)")

        form_hpge.addRow("نوع البلورة:", self.combo_crystal)
        form_hpge.addRow("نوع شبه الموصل (*):", self.combo_semiconductor)
        form_hpge.addRow("قطبية الشهادة المعتمدة:", self.combo_cert_polarity)
        form_hpge.addRow("مرجع شهادة المصنّع:", self.edit_cert_ref)
        form_hpge.addRow("زمن التبريد الأدنى (ساعة):", self.edit_cooling_hours)
        form_hpge.addRow("أقصى معدل رفع جهد (V/s):", self.edit_hv_ramp)
        form_hpge.addRow("مرجع معدل رفع الجهد:", self.edit_hv_ramp_ref)
        form_hpge.addRow("نوع النافذة:", self.combo_window_type)
        form_hpge.addRow("حالة نافذة البريليوم:", self.combo_window_status)
        form_hpge.addRow("نوع المضخم الأولي:", self.combo_preamp)
        form_hpge.addRow("جهد التشغيل الاسمي (V) (*):", self.edit_voltage)
        form_hpge.addRow("قطبية الجهد الاسمي:", self.combo_polarity)
        form_hpge.addRow("FWHM الاسمي (keV):", self.edit_fwhm)
        form_hpge.addRow("الكفاءة النسبية الاسمية (%):", self.edit_eff)
        form_hpge.addRow("قطر البلورة (بوصة):", self.edit_hpge_diam)
        form_hpge.addRow("طول البلورة (بوصة):", self.edit_hpge_height)
        layout.addWidget(self.hpge_group)

        # 4. NaI Specific Group
        self.nai_group = QGroupBox("المواصفات الفنية لكاشف NaI(Tl)")
        form_nai = QFormLayout(self.nai_group)
        form_nai.setSpacing(10)

        self.edit_nai_diam = QLineEdit()
        self.edit_nai_diam.setPlaceholderText("مثال: 3.0")

        self.edit_nai_height = QLineEdit()
        self.edit_nai_height.setPlaceholderText("مثال: 3.0")

        self.edit_nai_voltage = QLineEdit()
        self.edit_nai_voltage.setPlaceholderText("جهد تشغيل PMT الموصى به (مثال: 950)")

        self.edit_nai_res = QLineEdit()
        self.edit_nai_res.setPlaceholderText("الدقة الاسمية %R (مثال: 7.5)")

        form_nai.addRow("قطر البلورة (بوصة) (*):", self.edit_nai_diam)
        form_nai.addRow("ارتفاع البلورة (بوصة) (*):", self.edit_nai_height)
        form_nai.addRow("جهد التشغيل (V) (*):", self.edit_nai_voltage)
        form_nai.addRow("الدقة الاسمية %R:", self.edit_nai_res)

        self.nai_group.setVisible(False)
        layout.addWidget(self.nai_group)

        # 5. Notes
        self.edit_notes = QTextEdit()
        self.edit_notes.setMaximumHeight(60)
        self.edit_notes.setPlaceholderText("ملاحظات فنية إضافية...")
        layout.addWidget(QLabel("ملاحظات:"))
        layout.addWidget(self.edit_notes)
        layout.addStretch()

        self.scroll_area.setWidget(scroll_content)
        root_layout.addWidget(self.scroll_area, 1)

        # Error label (errors shown ONLY after pressing Save, outside scroll area)
        self.lbl_error = QLabel()
        self.lbl_error.setProperty("class", "error-text")
        self.lbl_error.setWordWrap(True)
        root_layout.addWidget(self.lbl_error)

        # Action Buttons (fixed at bottom outside scroll area)
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("حفظ الكاشف")
        self.btn_save.clicked.connect(self._on_save)

        self.btn_cancel = QPushButton("إلغاء")
        self.btn_cancel.setProperty("class", "secondary")
        self.btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_cancel)
        root_layout.addLayout(btn_row)

    def _on_type_changed(self) -> None:
        is_hpge = self.radio_hpge.isChecked()
        self.hpge_group.setVisible(is_hpge)
        self.nai_group.setVisible(not is_hpge)
        self.adjustSize()
        adjust_dialog_size_to_screen(self, preferred_width=620, preferred_height=750)

    def _load_existing_data(self) -> None:
        if not self.detector_id:
            return
        data = self.repo.get_by_id(self.detector_id)
        if not data:
            return

        self.edit_code.setText(data.get("code") or "")
        self.edit_manufacturer.setText(data.get("manufacturer") or "")
        self.edit_model.setText(data.get("model") or "")
        self.edit_serial.setText(data.get("serial_number") or "")
        self.edit_facility.setText(data.get("facility_name") or "")
        self.edit_notes.setText(data.get("notes") or "")

        is_hpge = data.get("detector_type") == "HPGE"
        self.radio_hpge.setChecked(is_hpge)
        self.radio_nai.setChecked(not is_hpge)
        self.radio_hpge.setEnabled(False)
        self.radio_nai.setEnabled(False)

        if is_hpge:
            sem = data.get("semiconductor_type")
            if sem in (None, "", "UNSPECIFIED", SemiconductorType.UNSPECIFIED.value):
                if self.combo_semiconductor.findData(SemiconductorType.UNSPECIFIED.value) == -1:
                    self.combo_semiconductor.addItem("نوع البلورة غير محدد — يلزم التعبئة", SemiconductorType.UNSPECIFIED.value)
                idx = self.combo_semiconductor.findData(SemiconductorType.UNSPECIFIED.value)
                self.combo_semiconductor.setCurrentIndex(idx)
            else:
                idx = self.combo_semiconductor.findData(sem)
                if idx >= 0:
                    self.combo_semiconductor.setCurrentIndex(idx)

            cert_pol = data.get("certified_polarity")
            idx_pol = self.combo_cert_polarity.findData(cert_pol or "")
            if idx_pol >= 0:
                self.combo_cert_polarity.setCurrentIndex(idx_pol)

            self.edit_cert_ref.setText(data.get("certificate_ref") or "")
            if data.get("cooling_hours_cert") is not None:
                self.edit_cooling_hours.setText(str(data["cooling_hours_cert"]))

            if data.get("hv_ramp_max_v_per_s") is not None:
                self.edit_hv_ramp.setText(str(data["hv_ramp_max_v_per_s"]))
            else:
                self.edit_hv_ramp.setText("10.0")

            self.edit_hv_ramp_ref.setText(data.get("hv_ramp_reference") or "")

            self.edit_voltage.setText(str(data.get("nominal_voltage") or ""))
            if data.get("nominal_fwhm_kev"):
                self.edit_fwhm.setText(str(data["nominal_fwhm_kev"]))
            if data.get("nominal_efficiency_pct"):
                self.edit_eff.setText(str(data["nominal_efficiency_pct"]))
            if data.get("diameter_inch"):
                self.edit_hpge_diam.setText(str(data["diameter_inch"]))
            if data.get("height_inch"):
                self.edit_hpge_height.setText(str(data["height_inch"]))
        else:
            self.edit_nai_voltage.setText(str(data.get("nominal_voltage") or ""))
            if data.get("diameter_inch"):
                self.edit_nai_diam.setText(str(data["diameter_inch"]))
            if data.get("height_inch"):
                self.edit_nai_height.setText(str(data["height_inch"]))
            if data.get("nominal_resolution_pct"):
                self.edit_nai_res.setText(str(data["nominal_resolution_pct"]))

    def _on_save(self) -> None:
        """Validate on submit and commit to repository."""
        self.lbl_error.setText("")

        is_hpge = self.radio_hpge.isChecked()
        code = self.edit_code.text().strip()
        mfr = self.edit_manufacturer.text().strip()
        model = self.edit_model.text().strip()
        serial = self.edit_serial.text().strip()
        facility = self.edit_facility.text().strip() or None
        notes = self.edit_notes.toPlainText().strip() or None

        if not code or not mfr or not model or not serial:
            self.lbl_error.setText("يرجى ملء جميع الحقول الأساسية الإلزامية المؤشرة بـ (*).")
            return

        try:
            if is_hpge:
                v_str = self.edit_voltage.text().strip()
                if not v_str:
                    self.lbl_error.setText("جهد التشغيل الاسمي إلزامي لكاشف HPGe.")
                    return
                voltage = float(v_str)

                sem_val = self.combo_semiconductor.currentData()
                if not sem_val or sem_val == SemiconductorType.UNSPECIFIED.value:
                    self.lbl_error.setText("نوع شبه الموصل (P-Type أو N-Type) إلزامي لكواشف HPGe ولا يمكن تركه غير محدد.")
                    return
                sem_type = SemiconductorType(sem_val)

                cert_pol_val = self.combo_cert_polarity.currentData()
                cert_pol = VoltagePolarity(cert_pol_val) if cert_pol_val else None
                cert_ref = self.edit_cert_ref.text().strip() or None

                # Non-blocking warning on incomplete documentation (allowed to save)
                if not cert_pol or not cert_ref:
                    _show_msg(
                        self,
                        "warn",
                        "تنبيه توثيق ناقص",
                        "تنبيه: لم يتم توثيق قطبية الشهادة أو مرجعها كاملاً لكاشف HPGe.\nيُسمح بالحفظ ولكن ستسجّل نتائج الفحص «توثيق ناقص».",
                    )

                cooling_str = self.edit_cooling_hours.text().strip()
                cooling_hours = float(cooling_str) if cooling_str else None

                ramp_str = self.edit_hv_ramp.text().strip()
                ramp_rate = float(ramp_str) if ramp_str else 10.0
                ramp_ref = self.edit_hv_ramp_ref.text().strip() or None

                if abs(ramp_rate - 10.0) > 1e-6 and not ramp_ref:
                    self.lbl_error.setText("مرجع دليل المصنّع لمعدل رفع الجهد إلزامي إذا اختلف المعدل عن 10.0 V/s.")
                    return

                fwhm = float(self.edit_fwhm.text().strip()) if self.edit_fwhm.text().strip() else None
                eff = float(self.edit_eff.text().strip()) if self.edit_eff.text().strip() else None
                diam = float(self.edit_hpge_diam.text().strip()) if self.edit_hpge_diam.text().strip() else None
                height = float(self.edit_hpge_height.text().strip()) if self.edit_hpge_height.text().strip() else None

                detector_obj = HPGeDetector(
                    code=code,
                    manufacturer=mfr,
                    model=model,
                    serial_number=serial,
                    crystal_type=CrystalType.COAXIAL,
                    semiconductor_type=sem_type,
                    certified_polarity=cert_pol,
                    certificate_ref=cert_ref,
                    cooling_hours_cert=cooling_hours,
                    hv_ramp_max_v_per_s=ramp_rate,
                    hv_ramp_reference=ramp_ref,
                    window_type=WindowType(self.combo_window_type.currentData()),
                    beryllium_window_status=WindowStatus(self.combo_window_status.currentData()),
                    preamp_type=PreampType(self.combo_preamp.currentData()),
                    nominal_voltage=voltage,
                    voltage_polarity=VoltagePolarity(self.combo_polarity.currentData()),
                    nominal_fwhm_kev=fwhm,
                    nominal_efficiency_pct=eff,
                    diameter_inch=diam,
                    height_inch=height,
                    facility_name=facility,
                    notes=notes,
                )
            else:
                v_str = self.edit_nai_voltage.text().strip()
                d_str = self.edit_nai_diam.text().strip()
                h_str = self.edit_nai_height.text().strip()

                if not v_str or not d_str or not h_str:
                    self.lbl_error.setText("أبعاد البلورة وجهد التشغيل إلزامية لكاشف NaI.")
                    return

                voltage = float(v_str)
                diam = float(d_str)
                height = float(h_str)
                res = float(self.edit_nai_res.text().strip()) if self.edit_nai_res.text().strip() else None

                detector_obj = NaIDetector(
                    code=code,
                    manufacturer=mfr,
                    model=model,
                    serial_number=serial,
                    diameter_inch=diam,
                    height_inch=height,
                    nominal_voltage=voltage,
                    nominal_resolution_pct=res,
                    facility_name=facility,
                    notes=notes,
                )

            if self.detector_id:
                self.repo.update(self.detector_id, detector_obj, self.session)
            else:
                self.repo.create(detector_obj, self.session)

            self.accept()
        except DuplicateEntityError as e:
            self.lbl_error.setText(str(e))
        except ValueError as e:
            self.lbl_error.setText(f"خطأ في قيم الحقول: {str(e)}")
        except Exception as e:
            self.lbl_error.setText(f"تعذر الحفظ: {str(e)}")


class DetectorDetailDialog(QDialog):
    """Dialog displaying complete detector specifications."""

    def __init__(self, data: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.data = data
        self.setWindowTitle(f"بطاقة الكاشف الفنية — {data.get('code')}")
        self.setMinimumWidth(500)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())
        enable_dialog_window_controls(self)
        self._init_ui()
        adjust_dialog_size_to_screen(self, preferred_width=540, preferred_height=650)

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(20, 16, 20, 16)
        root_layout.setSpacing(12)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        card = QFrame()
        card.setProperty("class", "card")
        form = QFormLayout(card)
        form.setSpacing(10)

        d = self.data
        form.addRow("الرمز المعملي:", QLabel(format_mixed_text(str(d.get("code") or "—"))))
        form.addRow("النوع:", QLabel(format_mixed_text(str(d.get("detector_type") or "—"))))
        form.addRow("الشركة الصانعة:", QLabel(format_mixed_text(str(d.get("manufacturer") or "—"))))
        form.addRow("الطراز:", QLabel(format_mixed_text(str(d.get("model") or "—"))))
        form.addRow("الرقم التسلسلي:", QLabel(format_mixed_text(str(d.get("serial_number") or "—"))))
        form.addRow("جهد التشغيل:", QLabel(format_mixed_text(f"{d.get('nominal_voltage')} V ({d.get('voltage_polarity') or '—'})")))
        form.addRow("المنشأة / المعمل:", QLabel(format_mixed_text(str(d.get("facility_name") or "—"))))
        form.addRow("الحالة التشغيلية:", QLabel(format_mixed_text(str(d.get("status") or "—"))))

        if d.get("detector_type") == "HPGE":
            sem = d.get("semiconductor_type")
            if sem == "P_TYPE":
                sem_str = f"بلورة نمط موجب ({isolate_ltr('P-Type')})"
            elif sem == "N_TYPE":
                sem_str = f"بلورة نمط سالب ({isolate_ltr('N-Type')})"
            else:
                sem_str = "نوع البلورة غير محدد — يلزم التعبئة"
            form.addRow("نوع شبه الموصل:", QLabel(format_mixed_text(sem_str)))

            cert_pol = d.get("certified_polarity") or "غير موثقة"
            cert_ref = d.get("certificate_ref") or "—"
            form.addRow("قطبية الشهادة المعتمدة:", QLabel(format_mixed_text(f"{cert_pol} (مرجع: {cert_ref})")))

            cooling_h = d.get("cooling_hours_cert")
            cooling_txt = f"{cooling_h} ساعة (شهادة)" if cooling_h else "6.0 ساعة (افتراضي)"
            form.addRow("زمن التبريد الأدنى:", QLabel(format_mixed_text(cooling_txt)))

            ramp = d.get("hv_ramp_max_v_per_s") if d.get("hv_ramp_max_v_per_s") is not None else 10.0
            ramp_ref = d.get("hv_ramp_reference") or "دليل عام"
            form.addRow("أقصى معدل رفع جهد:", QLabel(format_mixed_text(f"{ramp} V/s (مرجع: {ramp_ref})")))

            form.addRow("المضخم الأولي:", QLabel(format_mixed_text(str(d.get("preamp_type") or "—"))))
            form.addRow("نافذة البريليوم:", QLabel(format_mixed_text(f"{d.get('window_type')} ({d.get('beryllium_window_status')})")))
            form.addRow("FWHM الاسمي:", QLabel(format_mixed_text(f"{d.get('nominal_fwhm_kev') or '—'} keV")))
            form.addRow("الكفاءة الاسمية:", QLabel(format_mixed_text(f"{d.get('nominal_efficiency_pct') or '—'} %")))
        else:
            form.addRow("أبعاد البلورة:", QLabel(format_mixed_text(f"{d.get('diameter_inch')}\" × {d.get('height_inch')}\"")))
            form.addRow("الدقة الاسمية (%R):", QLabel(format_mixed_text(f"{d.get('nominal_resolution_pct') or '—'} %")))

        if d.get("notes"):
            form.addRow("ملاحظات:", QLabel(format_mixed_text(str(d.get("notes")))))

        self.scroll_area.setWidget(card)
        root_layout.addWidget(self.scroll_area, 1)

        btn_close = QPushButton("إغلاق")
        btn_close.setProperty("class", "secondary")
        btn_close.clicked.connect(self.accept)
        root_layout.addWidget(btn_close)


class DetectorsView(QWidget):
    """Main registry view for detectors."""

    def __init__(self, conn: sqlite3.Connection, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.conn = conn
        self.session = session
        self.repo = DetectorRepository(conn)

        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header Bar
        header = QHBoxLayout()
        title = QLabel("سجل الكواشف الإشعاعية (HPGe & NaI)")
        title.setProperty("class", "title")
        header.addWidget(title)
        header.addStretch()

        self.btn_add = QPushButton("➕ إضافة كاشف")
        if self.session.can("manage_detectors"):
            self.btn_add.clicked.connect(self._on_add_detector)
            header.addWidget(self.btn_add)
        else:
            self.btn_add.setVisible(False)

        self.btn_refresh = QPushButton("🔄 تحديث")
        self.btn_refresh.setProperty("class", "secondary")
        self.btn_refresh.clicked.connect(self.refresh)
        header.addWidget(self.btn_refresh)

        layout.addLayout(header)

        # Filter Bar
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(12)

        self.edit_search = QLineEdit()
        self.edit_search.setProperty("class", "search-box")
        self.edit_search.setPlaceholderText("🔍 بحث بالرمز أو الشركة الصانعة أو الرقم التسلسلي...")
        self.edit_search.textChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.edit_search, stretch=2)

        filter_bar.addWidget(QLabel("المنشأة:"))
        self.combo_facility = QComboBox()
        self.combo_facility.addItem("كافة المعامل والمنشآت", "")
        self.combo_facility.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.combo_facility, stretch=1)

        layout.addLayout(filter_bar)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "المعرّف",
            "الرمز المعملي",
            "النوع",
            "الشركة الصانعة",
            "الطراز",
            "الرقم التسلسلي",
            "المنشأة",
            "الحالة",
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._on_view_details)
        layout.addWidget(self.table)

        # Action Buttons Row
        action_row = QHBoxLayout()
        self.btn_details = QPushButton("👁️ عرض التفاصيل")
        self.btn_details.setProperty("class", "secondary")
        self.btn_details.clicked.connect(self._on_view_details)
        action_row.addWidget(self.btn_details)

        self.btn_edit = QPushButton("✏️ تعديل السجل")
        self.btn_edit.setProperty("class", "secondary")
        self.btn_edit.clicked.connect(self._on_edit_detector)
        self.btn_edit.setVisible(self.session.can("manage_detectors"))
        action_row.addWidget(self.btn_edit)

        self.btn_retire = QPushButton("🛑 إحالة للتقاعد")
        self.btn_retire.setProperty("class", "danger")
        self.btn_retire.clicked.connect(self._on_retire_detector)
        self.btn_retire.setVisible(self.session.can("manage_detectors"))
        action_row.addWidget(self.btn_retire)

        action_row.addStretch()
        layout.addLayout(action_row)

    def refresh(self) -> None:
        """Fetch records and refresh table."""
        # Refresh facilities dropdown
        all_records = self.repo.list_all(include_retired=True)
        facilities = sorted({r["facility_name"] for r in all_records if r.get("facility_name")})
        current_fac = self.combo_facility.currentData()
        self.combo_facility.blockSignals(True)
        self.combo_facility.clear()
        self.combo_facility.addItem("كافة المعامل والمنشآت", "")
        for f in facilities:
            self.combo_facility.addItem(f, f)
        idx = self.combo_facility.findData(current_fac)
        if idx >= 0:
            self.combo_facility.setCurrentIndex(idx)
        self.combo_facility.blockSignals(False)

        self._load_table_data()

    def _on_filter_changed(self) -> None:
        self._load_table_data()

    def _load_table_data(self) -> None:
        facility = self.combo_facility.currentData() or None
        query = self.edit_search.text().strip() or None
        records = self.repo.list_all(facility=facility, query=query, include_retired=True)

        self.table.setRowCount(len(records))
        for row, rec in enumerate(records):
            self.table.setItem(row, 0, QTableWidgetItem(str(rec["id"])))
            self.table.setItem(row, 1, QTableWidgetItem(rec["code"]))

            # Detector type column with crystal/semiconductor indicator
            if rec["detector_type"] == "HPGE":
                sem = rec.get("semiconductor_type")
                if sem == "P_TYPE":
                    type_str = f"HPGe ({isolate_ltr('P-Type')})"
                elif sem == "N_TYPE":
                    type_str = f"HPGe ({isolate_ltr('N-Type')})"
                else:
                    type_str = "HPGe [نوع البلورة غير محدد — يلزم التعبئة]"
            else:
                type_str = "NaI(Tl)"
            self.table.setItem(row, 2, QTableWidgetItem(type_str))

            self.table.setItem(row, 3, QTableWidgetItem(rec["manufacturer"]))
            self.table.setItem(row, 4, QTableWidgetItem(rec["model"]))
            self.table.setItem(row, 5, QTableWidgetItem(rec["serial_number"]))
            self.table.setItem(row, 6, QTableWidgetItem(rec["facility_name"] or "—"))

            status_str = "نشط" if rec["status"] == "ACTIVE" else ("متقاعد" if rec["status"] == "RETIRED" else rec["status"])
            self.table.setItem(row, 7, QTableWidgetItem(status_str))

    def _get_selected_detector_id(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            selected = self.table.selectedItems()
            if not selected:
                return None
            row = selected[0].row()
        item = self.table.item(row, 0)
        return int(item.text()) if item else None

    def _on_add_detector(self) -> None:
        dlg = AddEditDetectorDialog(repo=self.repo, session=self.session, parent=self)
        if _exec_dlg(dlg) == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_edit_detector(self) -> None:
        det_id = self._get_selected_detector_id()
        if not det_id:
            _show_msg(self, "warn", "تنبيه", "يرجى تحديد كاشف من الجدول أولاً.")
            return
        dlg = AddEditDetectorDialog(repo=self.repo, session=self.session, detector_id=det_id, parent=self)
        if _exec_dlg(dlg) == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_view_details(self) -> None:
        det_id = self._get_selected_detector_id()
        if not det_id:
            _show_msg(self, "warn", "تنبيه", "يرجى تحديد كاشف من الجدول أولاً.")
            return
        data = self.repo.get_by_id(det_id)
        if data:
            dlg = DetectorDetailDialog(data=data, parent=self)
            _exec_dlg(dlg)

    def _on_retire_detector(self) -> None:
        det_id = self._get_selected_detector_id()
        if not det_id:
            _show_msg(self, "warn", "تنبيه", "يرجى تحديد كاشف من الجدول أولاً.")
            return

        det = self.repo.get_by_id(det_id)
        if not det:
            return

        if det.get("status") == "RETIRED":
            _show_msg(self, "info", "معلومة", "هذا الكاشف محال للتقاعد بالفعل.")
            return

        if _ask_confirm(
            self,
            "تأكيد إحالة الكاشف للتقاعد",
            f"هل أنت متأكد من إحالة الكاشف ({det['code']}) للتقاعد؟\nلا يمكن استخدامه في الفحوصات الجديدة لاحقاً.",
        ):
            self.repo.retire(det_id, self.session, reason="إحالة إدارية عبر الواجهة")
            self.refresh()

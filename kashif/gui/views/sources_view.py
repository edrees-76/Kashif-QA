"""Radioactive Sources management view for KASHIF-QA.

Handles registry of single and multi-isotope calibration sources:
- Table listing with real-time decay-corrected activity
- Interactive decay calculator supporting 8 units (Bq, kBq, MBq, GBq, TBq, µCi, mCi, Ci)
- Multi-isotope support with individual isotope lifecycle and decay tracking
- Technical evaluation against R-SRC-01 (restricted exclusively to Co-60 and Cs-137)
- Add/Edit dialog with dynamic isotope table and custom nuclide registration
- Safe retirement with confirmation dialog
"""

from __future__ import annotations

from datetime import date, datetime
import logging
import math
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDateEdit,
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
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from kashif.core.decay import (
    ACTIVITY_UNITS,
    HALF_LIFE_UNITS,
    convert_activity_from_bq,
    convert_activity_to_bq,
    decay_activity,
    format_activity_auto,
    format_half_life,
)
from kashif.core.models import RadioactiveSource, SourceIsotope
from kashif.core.rules import eval_r_src_01_source_activity
from kashif.db.repositories import (
    DuplicateEntityError,
    EntityNotFoundError,
    NuclideRepository,
    SourceRepository,
)
from kashif.gui.bidi import format_mixed_text, isolate_ltr
from kashif.gui.theme import get_stylesheet
from kashif.services.session import Session

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Add Custom Nuclide Dialog
# ---------------------------------------------------------------------------

class AddCustomNuclideDialog(QDialog):
    """Dialog for registering a new custom radionuclide."""

    def __init__(self, repo: NuclideRepository, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.repo = repo
        self.session = session
        self.created_nuclide_code: Optional[str] = None

        self.setWindowTitle("تعريف نظير مشع جديد — منظومة كاشف")
        self.setMinimumWidth(460)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.edit_code = QLineEdit()
        self.edit_code.setPlaceholderText("رمز النظير (مثال: Am-241 أو Tc-99m)")
        form.addRow("رمز النظير المشع (*):", self.edit_code)

        self.edit_name_ar = QLineEdit()
        self.edit_name_ar.setPlaceholderText("مثال: أمريسيوم-241")
        form.addRow("الاسم بالعربية (*):", self.edit_name_ar)

        self.edit_name_en = QLineEdit()
        self.edit_name_en.setPlaceholderText("مثال: Americium-241")
        form.addRow("الاسم بالإنجليزية (*):", self.edit_name_en)

        hl_row = QHBoxLayout()
        self.edit_hl_val = QLineEdit()
        self.edit_hl_val.setPlaceholderText("القيمة الرقمية لعمر النصف (مثال: 432.6)")
        self.combo_hl_unit = QComboBox()
        for u_code, u_info in HALF_LIFE_UNITS.items():
            self.combo_hl_unit.addItem(f"{u_info['name_ar']} ({u_code})", u_code)
        self.combo_hl_unit.setCurrentIndex(self.combo_hl_unit.findData("y"))

        hl_row.addWidget(self.edit_hl_val, stretch=2)
        hl_row.addWidget(self.combo_hl_unit, stretch=1)
        form.addRow("عمر النصف (T½) (*):", hl_row)

        self.edit_reference = QLineEdit()
        self.edit_reference.setPlaceholderText("المصدر المرجعي المعتمد (مثال: DDEP Vol. 5 / NNDC)")
        form.addRow("مصدر القيمة (المرجع) (*):", self.edit_reference)

        layout.addLayout(form)

        self.lbl_error = QLabel()
        self.lbl_error.setProperty("class", "error-text")
        self.lbl_error.setWordWrap(True)
        layout.addWidget(self.lbl_error)

        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("حفظ النظير")
        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel = QPushButton("إلغاء")
        self.btn_cancel.setProperty("class", "secondary")
        self.btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

    def _on_save(self) -> None:
        self.lbl_error.setText("")
        code = self.edit_code.text().strip()
        name_ar = self.edit_name_ar.text().strip()
        name_en = self.edit_name_en.text().strip()
        hl_str = self.edit_hl_val.text().strip()
        hl_unit = self.combo_hl_unit.currentData()
        reference = self.edit_reference.text().strip()

        try:
            hl_val = float(hl_str)
        except (ValueError, TypeError):
            self.lbl_error.setText("يرجى إدخال قيمة عددية موجبة وصحيحة لعمر النصف.")
            return

        try:
            record = self.repo.create_custom_nuclide(
                code=code,
                name_ar=name_ar,
                name_en=name_en,
                half_life_value=hl_val,
                half_life_unit=hl_unit,
                reference=reference,
                session=self.session,
            )
            self.created_nuclide_code = record["code"]
            self.accept()
        except Exception as e:
            self.lbl_error.setText(str(e))


# ---------------------------------------------------------------------------
# Add / Edit Radioactive Source Dialog (Multi-Isotope Support)
# ---------------------------------------------------------------------------

class AddEditSourceDialog(QDialog):
    """Dialog for creating or modifying a multi-isotope radioactive source."""

    def __init__(
        self,
        repo: SourceRepository,
        session: Session,
        source_id: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.nuclide_repo = NuclideRepository(repo.conn)
        self.session = session
        self.source_id = source_id

        title = "تعديل مصدر مشع" if source_id else "إضافة مصدر مشع جديد — منظومة كاشف"
        self.setWindowTitle(title)
        self.setMinimumWidth(800)
        self.setMinimumHeight(600)
        self.resize(800, 600)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())

        self._init_ui()
        if self.source_id:
            self._load_existing_data()
        else:
            self._add_isotope_row(nuclide_code="Co-60", act_val=37000.0, unit="Bq")

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)

        # 1. Physical Source Info
        group_basic = QGroupBox("البيانات الفيزيائية للمصدر")
        form_basic = QFormLayout(group_basic)
        form_basic.setSpacing(10)

        self.edit_code = QLineEdit()
        self.edit_code.setPlaceholderText("الرمز المعملي الفريد (مثال: SRC-CAL-01)")
        self.edit_serial = QLineEdit()
        self.edit_serial.setPlaceholderText("الرقم التسلسلي في شهادة المعايرة")
        self.edit_cert_num = QLineEdit()
        self.edit_cert_num.setPlaceholderText("رقم شهادة المعايرة (مثال: IAEA-CERT-2024-001)")
        self.edit_facility = QLineEdit()
        self.edit_facility.setPlaceholderText("اسم المنشأة أو المعمل الحاضن للمصدر")

        form_basic.addRow("الرمز المعملي الفريد (*):", self.edit_code)
        form_basic.addRow("الرقم التسلسلي (*):", self.edit_serial)
        form_basic.addRow("رقم شهادة المعايرة:", self.edit_cert_num)
        form_basic.addRow("اسم المعمل / المنشأة:", self.edit_facility)
        layout.addWidget(group_basic)

        # 2. Isotopes Registry Section
        group_iso = QGroupBox("النظائر المشعة في المصدر (نظير واحد على الأقل)")
        layout_iso = QVBoxLayout(group_iso)
        layout_iso.setSpacing(8)

        # Top buttons for isotopes
        iso_actions = QHBoxLayout()
        self.btn_add_iso = QPushButton("➕ إضافة نظير للمصدر")
        self.btn_add_iso.setProperty("class", "secondary")
        self.btn_add_iso.clicked.connect(lambda: self._add_isotope_row())
        iso_actions.addWidget(self.btn_add_iso)

        self.btn_new_custom_nuclide = QPushButton("✨ تعريف نظير جديد...")
        self.btn_new_custom_nuclide.setProperty("class", "secondary")
        self.btn_new_custom_nuclide.clicked.connect(self._on_define_custom_nuclide)
        iso_actions.addWidget(self.btn_new_custom_nuclide)
        iso_actions.addStretch()

        layout_iso.addLayout(iso_actions)

        # Isotopes table
        self.tbl_isotopes = QTableWidget()
        self.tbl_isotopes.setColumnCount(5)
        self.tbl_isotopes.setHorizontalHeaderLabels([
            "النظير المشع (*)",
            "الفعالية المعتمدة (*)",
            "الوحدة (*)",
            "تاريخ الشهادة (*)",
            "حذف",
        ])
        iso_hdr = self.tbl_isotopes.horizontalHeader()
        iso_hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        iso_hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        iso_hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        iso_hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        iso_hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_isotopes.verticalHeader().setVisible(False)
        self.tbl_isotopes.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.tbl_isotopes.setMinimumHeight(140)
        layout_iso.addWidget(self.tbl_isotopes)

        layout.addWidget(group_iso)

        # 3. Notes
        self.edit_notes = QTextEdit()
        self.edit_notes.setMaximumHeight(55)
        self.edit_notes.setPlaceholderText("ملاحظات فنية أو شروط التخزين والسلامة الإشعاعية...")
        layout.addWidget(QLabel("ملاحظات:"))
        layout.addWidget(self.edit_notes)

        # Error label
        self.lbl_error = QLabel()
        self.lbl_error.setProperty("class", "error-text")
        self.lbl_error.setWordWrap(True)
        layout.addWidget(self.lbl_error)

        # Action Buttons
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("حفظ المصدر المشع")
        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel = QPushButton("إلغاء")
        self.btn_cancel.setProperty("class", "secondary")
        self.btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

    def _get_available_nuclides(self) -> List[Dict[str, Any]]:
        return self.nuclide_repo.list_all()

    def _add_isotope_row(
        self,
        nuclide_code: Optional[str] = None,
        act_val: Optional[float] = None,
        unit: str = "Bq",
        cert_date_val: Optional[date] = None,
    ) -> None:
        """Add an editable isotope configuration row to the isotopes table."""
        row_idx = self.tbl_isotopes.rowCount()
        self.tbl_isotopes.insertRow(row_idx)

        # 1. Nuclide Combo
        nuclides = self._get_available_nuclides()
        combo_nuclide = QComboBox()
        for n in nuclides:
            label = f"{isolate_ltr(n['code'])} — {n['name_ar']}"
            combo_nuclide.addItem(label, n["code"])
        if nuclide_code:
            idx = combo_nuclide.findData(nuclide_code)
            if idx >= 0:
                combo_nuclide.setCurrentIndex(idx)
        self.tbl_isotopes.setCellWidget(row_idx, 0, combo_nuclide)

        # 2. Activity Value
        edit_act = QLineEdit()
        edit_act.setPlaceholderText("مثال: 37000 أو 1.0")
        if act_val is not None:
            edit_act.setText(str(act_val))
        self.tbl_isotopes.setCellWidget(row_idx, 1, edit_act)

        # 3. Unit Combo (All 8 units)
        combo_unit = QComboBox()
        for u_code, u_info in ACTIVITY_UNITS.items():
            combo_unit.addItem(f"{u_info['name_ar']} ({u_code})", u_code)
        idx_u = combo_unit.findData(unit)
        if idx_u >= 0:
            combo_unit.setCurrentIndex(idx_u)
        self.tbl_isotopes.setCellWidget(row_idx, 2, combo_unit)

        # 4. Cert Date
        date_edit = QDateEdit()
        date_edit.setCalendarPopup(True)
        if cert_date_val:
            date_edit.setDate(QDate(cert_date_val.year, cert_date_val.month, cert_date_val.day))
        else:
            date_edit.setDate(QDate.currentDate().addYears(-1))
        self.tbl_isotopes.setCellWidget(row_idx, 3, date_edit)

        # 5. Delete Row Button
        btn_delete = QPushButton("🗑️ حذف")
        btn_delete.setProperty("class", "danger")
        btn_delete.clicked.connect(lambda: self._remove_isotope_row(btn_delete))
        self.tbl_isotopes.setCellWidget(row_idx, 4, btn_delete)

        self._update_table_geometry()

    def _remove_isotope_row(self, btn: QPushButton) -> None:
        """Remove isotope row, ensuring at least one isotope remains."""
        if self.tbl_isotopes.rowCount() <= 1:
            _show_msg(self, "warn", "تنبيه", "يجب أن يحتوي المصدر المشع على نظير واحد على الأقل.")
            return

        for r in range(self.tbl_isotopes.rowCount()):
            if self.tbl_isotopes.cellWidget(r, 4) is btn:
                self.tbl_isotopes.removeRow(r)
                break

        self._update_table_geometry()

    def _update_table_geometry(self) -> None:
        """Ensure isotopes table displays at least 2 rows without vertical cutoff."""
        header_h = self.tbl_isotopes.horizontalHeader().height() or 34
        row_h = self.tbl_isotopes.rowHeight(0) if self.tbl_isotopes.rowCount() > 0 else 38
        target_rows = max(self.tbl_isotopes.rowCount(), 2)
        min_h = header_h + (row_h * min(target_rows, 5)) + 6
        self.tbl_isotopes.setMinimumHeight(max(140, min_h))

    def _on_define_custom_nuclide(self) -> None:
        """Open custom nuclide dialog and refresh combos upon creation."""
        dlg = AddCustomNuclideDialog(self.nuclide_repo, self.session, parent=self)
        if _exec_dlg(dlg) == QDialog.DialogCode.Accepted:
            new_code = dlg.created_nuclide_code
            nuclides = self._get_available_nuclides()
            # Update all existing row combos
            for r in range(self.tbl_isotopes.rowCount()):
                combo: QComboBox = self.tbl_isotopes.cellWidget(r, 0)  # type: ignore
                current = combo.currentData()
                combo.blockSignals(True)
                combo.clear()
                for n in nuclides:
                    combo.addItem(f"{isolate_ltr(n['code'])} — {n['name_ar']}", n["code"])
                idx = combo.findData(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                combo.blockSignals(False)

            # Also optionally add or select the new nuclide
            if new_code:
                _show_msg(self, "info", "نجاح", f"تم تعريف النظير {new_code} بنجاح وإضافته إلى القائمة المرجعية.")

    def _load_existing_data(self) -> None:
        if not self.source_id:
            return
        data = self.repo.get_by_id(self.source_id)
        if not data:
            return

        self.edit_code.setText(data.get("source_code") or "")
        self.edit_serial.setText(data.get("serial_number") or "")
        self.edit_cert_num.setText(data.get("certificate_number") or "")
        self.edit_facility.setText(data.get("facility_name") or "")
        self.edit_notes.setText(data.get("notes") or "")

        self.tbl_isotopes.setRowCount(0)
        isotopes = data.get("isotopes") or []
        for iso in isotopes:
            c_date = date.fromisoformat(iso["cert_date"]) if iso.get("cert_date") else None
            self._add_isotope_row(
                nuclide_code=iso.get("nuclide_code"),
                act_val=iso.get("cert_activity_bq"),
                unit="Bq",
                cert_date_val=c_date,
            )
        self._update_table_geometry()

    def _on_save(self) -> None:
        """Validate inputs on submit only and commit to repository."""
        self.lbl_error.setText("")

        code = self.edit_code.text().strip()
        serial = self.edit_serial.text().strip()
        cert_num = self.edit_cert_num.text().strip() or None
        facility = self.edit_facility.text().strip() or None
        notes = self.edit_notes.toPlainText().strip() or None

        if not code or not serial:
            self.lbl_error.setText("يرجى ملء الرمز المعملي والرقم التسلسلي للمصدر.")
            return

        num_rows = self.tbl_isotopes.rowCount()
        if num_rows == 0:
            self.lbl_error.setText("يجب إضافة نظير مشع واحد على الأقل للمصدر.")
            return

        isotopes_list: List[SourceIsotope] = []
        seen_nuclides = set()

        for r in range(num_rows):
            combo_nuc: QComboBox = self.tbl_isotopes.cellWidget(r, 0)  # type: ignore
            edit_act: QLineEdit = self.tbl_isotopes.cellWidget(r, 1)    # type: ignore
            combo_u: QComboBox = self.tbl_isotopes.cellWidget(r, 2)     # type: ignore
            date_ed: QDateEdit = self.tbl_isotopes.cellWidget(r, 3)     # type: ignore

            nuc_code = combo_nuc.currentData()
            if not nuc_code:
                self.lbl_error.setText(f"يرجى تحديد النظير المشع في الصف رقم {r+1}.")
                return

            if nuc_code in seen_nuclides:
                self.lbl_error.setText(f"لا يمكن تكرار النظير ({nuc_code}) أكثر من مرة في نفس المصدر.")
                return
            seen_nuclides.add(nuc_code)

            act_str = edit_act.text().strip()
            try:
                raw_act = float(act_str)
                if raw_act <= 0 or not math.isfinite(raw_act):
                    raise ValueError
            except (ValueError, TypeError):
                self.lbl_error.setText(f"الفعالية في الصف رقم {r+1} يجب أن تكون قيمة عددية موجبة.")
                return

            unit = combo_u.currentData()
            act_bq = convert_activity_to_bq(raw_act, unit)
            cert_d = date_ed.date().toPyDate()

            isotopes_list.append(SourceIsotope(
                nuclide_code=nuc_code,
                cert_activity_bq=act_bq,
                cert_date=cert_d,
            ))

        try:
            source_model = RadioactiveSource(
                source_code=code,
                serial_number=serial,
                isotopes=isotopes_list,
                certificate_number=cert_num,
                facility_name=facility,
                notes=notes,
            )

            if self.source_id:
                self.repo.update(self.source_id, source_model, self.session)
            else:
                self.repo.create(source_model, self.session)

            self.accept()
        except DuplicateEntityError as e:
            self.lbl_error.setText(str(e))
        except Exception as e:
            self.lbl_error.setText(f"خطأ أثناء الحفظ: {e}")


# ---------------------------------------------------------------------------
# Source Detail and Compact Interactive Decay Calculator Dialog
# ---------------------------------------------------------------------------

class SourceDetailDialog(QDialog):
    """Detailed view for a radioactive source with compact multi-isotope decay calculator."""

    def __init__(self, data: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.data = data

        self.setWindowTitle(f"بطاقة المصدر المشع — {data.get('source_code')}")
        self.setMinimumWidth(720)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())

        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        # 1. Source Metadata Card
        meta_card = QFrame()
        meta_card.setProperty("class", "card")
        form_meta = QFormLayout(meta_card)
        form_meta.setSpacing(8)

        d = self.data
        form_meta.addRow("الرمز المعملي:", QLabel(format_mixed_text(str(d.get("source_code") or "—"))))
        form_meta.addRow("الرقم التسلسلي:", QLabel(format_mixed_text(str(d.get("serial_number") or "—"))))
        form_meta.addRow("رقم شهادة المعايرة:", QLabel(format_mixed_text(str(d.get("certificate_number") or "—"))))
        form_meta.addRow("المنشأة / المعمل:", QLabel(format_mixed_text(str(d.get("facility_name") or "—"))))
        form_meta.addRow("الحالة:", QLabel(format_mixed_text(str(d.get("status") or "—"))))
        if d.get("notes"):
            form_meta.addRow("ملاحظات:", QLabel(format_mixed_text(str(d.get("notes")))))

        layout.addWidget(meta_card)

        # 2. Compact Decay Calculator Card (Item 10 & Item 17)
        calc_group = QGroupBox("حاسبة الاضمحلال الإشعاعي اللحظية ونظائر المصدر")
        calc_layout = QVBoxLayout(calc_group)
        calc_layout.setContentsMargins(14, 12, 14, 12)
        calc_layout.setSpacing(10)

        # Controls row
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(10)

        ctrl_row.addWidget(QLabel("📅 تاريخ الحساب:"))
        self.calc_date_edit = QDateEdit()
        self.calc_date_edit.setCalendarPopup(True)
        self.calc_date_edit.setDate(QDate.currentDate())
        self.calc_date_edit.dateChanged.connect(self._recalc)
        ctrl_row.addWidget(self.calc_date_edit)

        ctrl_row.addWidget(QLabel("وحدة العرض:"))
        self.combo_unit = QComboBox()
        self.combo_unit.addItem("تلقائي (Auto)", "auto")
        for u_code, u_info in ACTIVITY_UNITS.items():
            self.combo_unit.addItem(f"{u_info['name_ar']} ({u_code})", u_code)
        self.combo_unit.currentIndexChanged.connect(self._recalc)
        ctrl_row.addWidget(self.combo_unit)

        ctrl_row.addStretch()
        calc_layout.addLayout(ctrl_row)

        # Table of isotopes with current activities
        self.tbl_calc = QTableWidget()
        self.tbl_calc.setColumnCount(6)
        self.tbl_calc.setHorizontalHeaderLabels([
            "النظير المشع",
            "عمر النصف (T½)",
            "الفعالية بالشهادة",
            "تاريخ الشهادة",
            "الفعالية اللحظية",
            "تقييم R-SRC-01",
        ])
        self.tbl_calc.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tbl_calc.verticalHeader().setVisible(False)
        self.tbl_calc.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        calc_layout.addWidget(self.tbl_calc)

        # Hint & Rule scope notice
        lbl_hint = QLabel("قاعدة R-SRC-01 (الفعالية 37 kBq ± 50%) مخصصة فقط لنظيري Co-60 و Cs-137 وفق معايير الدليل الإرشادي.")
        lbl_hint.setProperty("class", "hint")
        calc_layout.addWidget(lbl_hint)

        layout.addWidget(calc_group)

        self._recalc()

        btn_close = QPushButton("إغلاق")
        btn_close.setProperty("class", "secondary")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)

    def _recalc(self) -> None:
        target_d = self.calc_date_edit.date().toPyDate()
        isotopes = self.data.get("isotopes") or []
        unit_choice = self.combo_unit.currentData()

        self.tbl_calc.setRowCount(len(isotopes))

        for row, iso in enumerate(isotopes):
            nuc = iso.get("nuclide_code", "—")
            nuc_name = iso.get("name_ar") or ""
            nuc_display = f"{nuc} ({nuc_name})" if nuc_name else nuc

            # Half-life formatting in original unit
            hl_days = iso.get("half_life_days") or 0.0
            hl_val = iso.get("half_life_value")
            hl_unit = iso.get("half_life_unit")
            if hl_val is not None and hl_unit:
                hl_str = format_half_life(hl_val, hl_unit)
            else:
                hl_str = format_half_life(hl_days, "d")

            cert_bq = iso.get("cert_activity_bq", 0.0)
            cert_d_str = iso.get("cert_date", "")

            # Formatted cert activity
            if unit_choice == "auto":
                cert_act_str = format_activity_auto(cert_bq)
            else:
                c_val = convert_activity_from_bq(cert_bq, unit_choice)
                cert_act_str = f"{c_val:,.3g} {unit_choice}"

            self.tbl_calc.setItem(row, 0, QTableWidgetItem(nuc_display))
            self.tbl_calc.setItem(row, 1, QTableWidgetItem(hl_str))
            self.tbl_calc.setItem(row, 2, QTableWidgetItem(cert_act_str))
            self.tbl_calc.setItem(row, 3, QTableWidgetItem(cert_d_str or "—"))

            # Decay calculation
            badge_widget = QLabel()
            badge_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)

            try:
                cert_d = date.fromisoformat(cert_d_str)
                elapsed_days = (target_d - cert_d).days
                if elapsed_days >= 0:
                    current_bq = decay_activity(cert_bq, float(elapsed_days), hl_days)
                    if unit_choice == "auto":
                        act_now_str = format_activity_auto(current_bq)
                    else:
                        now_val = convert_activity_from_bq(current_bq, unit_choice)
                        act_now_str = f"{now_val:,.3g} {unit_choice}"

                    # R-SRC-01 evaluation (Only Co-60 and Cs-137)
                    if nuc in ("Co-60", "Cs-137"):
                        rule_res = eval_r_src_01_source_activity(current_bq)
                        if rule_res.verdict.value == "PASS":
                            badge_widget.setText("✓ كافية للفحص")
                            badge_widget.setProperty("class", "badge-valid")
                        else:
                            badge_widget.setText("⚠️ خارج النطاق")
                            badge_widget.setProperty("class", "badge-warning")
                    else:
                        badge_widget.setText("غير مشمول بقاعدة R-SRC-01")
                        badge_widget.setProperty("class", "badge-neutral")
                else:
                    act_now_str = "يسبق الشهادة"
                    badge_widget.setText("⚠️ تاريخ غير صالح")
                    badge_widget.setProperty("class", "badge-warning")
            except Exception as e:
                logger.warning("Decay calc error for isotope %s: %s", nuc, e)
                act_now_str = "خطأ بالحساب"
                badge_widget.setText("⚠️ بيانات غير صالحة")
                badge_widget.setProperty("class", "badge-expired")

            self.tbl_calc.setItem(row, 4, QTableWidgetItem(act_now_str))
            self.tbl_calc.setCellWidget(row, 5, badge_widget)


# ---------------------------------------------------------------------------
# Main Sources View
# ---------------------------------------------------------------------------

class SourcesView(QWidget):
    """Main registry view for radioactive sources with integrated interactive decay calculator."""

    def __init__(self, conn: sqlite3.Connection, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.conn = conn
        self.session = session
        self.repo = SourceRepository(conn)
        self.nuclide_repo = NuclideRepository(conn)

        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header Bar
        header = QHBoxLayout()
        title = QLabel("سجل المصادر المشعة القياسية والمركبة (Radioactive Sources)")
        title.setProperty("class", "title")
        header.addWidget(title)
        header.addStretch()

        self.btn_add = QPushButton("➕ إضافة مصدر مشع")
        if self.session.can("manage_sources"):
            self.btn_add.clicked.connect(self._on_add_source)
            header.addWidget(self.btn_add)
        else:
            self.btn_add.setVisible(False)

        self.btn_refresh = QPushButton("🔄 تحديث")
        self.btn_refresh.setProperty("class", "secondary")
        self.btn_refresh.clicked.connect(self.refresh)
        header.addWidget(self.btn_refresh)

        layout.addLayout(header)

        # Interactive Decay Calculator Top Bar (Item 10)
        calc_box = QFrame()
        calc_box.setProperty("class", "card")
        calc_box_layout = QHBoxLayout(calc_box)
        calc_box_layout.setContentsMargins(14, 10, 14, 10)
        calc_box_layout.setSpacing(16)

        calc_box_layout.addWidget(QLabel("📅 تاريخ حساب الاضمحلال اللحظي:"))
        self.date_calc_global = QDateEdit()
        self.date_calc_global.setCalendarPopup(True)
        self.date_calc_global.setDate(QDate.currentDate())
        self.date_calc_global.dateChanged.connect(self._load_table_data)
        calc_box_layout.addWidget(self.date_calc_global)

        self.lbl_calc_hint = QLabel("يتم تصحيح فعاليات نظائر جميع المصادر في الجدول لحظياً وفق التاريخ المختار.")
        self.lbl_calc_hint.setProperty("class", "hint")
        calc_box_layout.addWidget(self.lbl_calc_hint)
        calc_box_layout.addStretch()

        layout.addWidget(calc_box)

        # Filter Bar
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(12)

        self.edit_search = QLineEdit()
        self.edit_search.setProperty("class", "search-box")
        self.edit_search.setPlaceholderText("🔍 بحث بالرمز المعملي أو الرقم التسلسلي أو النظير...")
        self.edit_search.textChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.edit_search, stretch=2)

        filter_bar.addWidget(QLabel("النظير:"))
        self.combo_nuclide_filter = QComboBox()
        self.combo_nuclide_filter.addItem("كافة النظائر", "")
        self.combo_nuclide_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.combo_nuclide_filter, stretch=1)

        filter_bar.addWidget(QLabel("المنشأة:"))
        self.combo_facility = QComboBox()
        self.combo_facility.addItem("كافة المعامل والمنشآت", "")
        self.combo_facility.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.combo_facility, stretch=1)

        layout.addLayout(filter_bar)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels([
            "المعرّف",
            "النظائر المشعة",
            "الرمز المعملي",
            "الرقم التسلسلي",
            "الفعالية المعتمدة",
            "تاريخ الشهادة",
            "الفعالية اللحظية",
            "تقييم R-SRC-01",
            "المنشأة",
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
        self.btn_details = QPushButton("👁️ بطاقة المصدر والحاسبة")
        self.btn_details.setProperty("class", "secondary")
        self.btn_details.clicked.connect(self._on_view_details)
        action_row.addWidget(self.btn_details)

        self.btn_edit = QPushButton("✏️ تعديل السجل")
        self.btn_edit.setProperty("class", "secondary")
        self.btn_edit.clicked.connect(self._on_edit_source)
        self.btn_edit.setVisible(self.session.can("manage_sources"))
        action_row.addWidget(self.btn_edit)

        self.btn_retire = QPushButton("🛑 إحالة للتقاعد")
        self.btn_retire.setProperty("class", "danger")
        self.btn_retire.clicked.connect(self._on_retire_source)
        self.btn_retire.setVisible(self.session.can("manage_sources"))
        action_row.addWidget(self.btn_retire)

        action_row.addStretch()
        layout.addLayout(action_row)

    def refresh(self) -> None:
        """Fetch records and refresh table and filters."""
        # Refresh facilities dropdown
        all_records = self.repo.list_all(include_retired=True)
        facilities = sorted({r["facility_name"] for r in all_records if r.get("facility_name")})
        current_fac = self.combo_facility.currentData()
        self.combo_facility.blockSignals(True)
        self.combo_facility.clear()
        self.combo_facility.addItem("كافة المعامل والمنشآت", "")
        for f in facilities:
            self.combo_facility.addItem(f, f)
        idx_fac = self.combo_facility.findData(current_fac)
        if idx_fac >= 0:
            self.combo_facility.setCurrentIndex(idx_fac)
        self.combo_facility.blockSignals(False)

        # Refresh nuclides filter dropdown
        nuclides = self.nuclide_repo.list_all()
        current_nuc = self.combo_nuclide_filter.currentData()
        self.combo_nuclide_filter.blockSignals(True)
        self.combo_nuclide_filter.clear()
        self.combo_nuclide_filter.addItem("كافة النظائر", "")
        for n in nuclides:
            self.combo_nuclide_filter.addItem(f"{isolate_ltr(n['code'])} — {n['name_ar']}", n["code"])
        idx_nuc = self.combo_nuclide_filter.findData(current_nuc)
        if idx_nuc >= 0:
            self.combo_nuclide_filter.setCurrentIndex(idx_nuc)
        self.combo_nuclide_filter.blockSignals(False)

        self._load_table_data()

    def _on_filter_changed(self) -> None:
        self._load_table_data()

    def _load_table_data(self) -> None:
        facility = self.combo_facility.currentData() or None
        nuclide = self.combo_nuclide_filter.currentData() or None
        query = self.edit_search.text().strip() or None

        records = self.repo.list_all(facility=facility, query=query, include_retired=True)
        if nuclide:
            records = [
                r for r in records
                if any(iso["nuclide_code"] == nuclide for iso in r.get("isotopes", []))
            ]

        target_date = self.date_calc_global.date().toPyDate()

        self.table.setRowCount(len(records))
        for row, rec in enumerate(records):
            self.table.setItem(row, 0, QTableWidgetItem(str(rec["id"])))

            isotopes = rec.get("isotopes", [])
            nuclides_str = " + ".join(iso["nuclide_code"] for iso in isotopes) if isotopes else rec.get("nuclide", "—")
            self.table.setItem(row, 1, QTableWidgetItem(nuclides_str))
            self.table.setItem(row, 2, QTableWidgetItem(rec["source_code"]))
            self.table.setItem(row, 3, QTableWidgetItem(rec["serial_number"]))

            # Certified activity summary
            if len(isotopes) == 1:
                cert_act_str = format_activity_auto(isotopes[0]["cert_activity_bq"])
                cert_date_str = isotopes[0]["cert_date"]
            elif len(isotopes) > 1:
                cert_act_str = " + ".join(format_activity_auto(iso["cert_activity_bq"]) for iso in isotopes)
                cert_date_str = isotopes[0]["cert_date"]
            else:
                cert_act_str = format_activity_auto(rec.get("cert_activity_bq") or 0.0)
                cert_date_str = rec.get("cert_date") or "—"

            self.table.setItem(row, 4, QTableWidgetItem(cert_act_str))
            self.table.setItem(row, 5, QTableWidgetItem(cert_date_str))

            # Decay Calculation for target_date across isotopes
            decay_acts = []
            has_corrupt_date = False
            has_co60_or_cs137 = False
            any_out_of_spec = False

            for iso in isotopes:
                n_code = iso["nuclide_code"]
                hl_days = iso.get("half_life_days") or 0.0
                c_bq = iso.get("cert_activity_bq") or 0.0
                c_date_raw = iso.get("cert_date")
                try:
                    c_date = date.fromisoformat(c_date_raw)
                    elapsed = (target_date - c_date).days
                    if elapsed >= 0:
                        cur_bq = decay_activity(c_bq, float(elapsed), hl_days)
                        decay_acts.append(format_activity_auto(cur_bq))

                        if n_code in ("Co-60", "Cs-137"):
                            has_co60_or_cs137 = True
                            rule_res = eval_r_src_01_source_activity(cur_bq)
                            if rule_res.verdict.value != "PASS":
                                any_out_of_spec = True
                    else:
                        decay_acts.append("يسبق الشهادة")
                        has_corrupt_date = True
                except Exception as e:
                    logger.warning("Invalid cert_date '%s' in source %s: %s", c_date_raw, rec.get("id"), e)
                    decay_acts.append("تاريخ غير صالح")
                    has_corrupt_date = True

            now_summary = " + ".join(decay_acts) if decay_acts else "—"
            self.table.setItem(row, 6, QTableWidgetItem(now_summary))

            # R-SRC-01 Badge
            badge_widget = QLabel()
            badge_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)

            if has_corrupt_date:
                badge_widget.setText("⚠️ بيانات غير صالحة")
                badge_widget.setProperty("class", "badge-expired")
            elif not has_co60_or_cs137:
                badge_widget.setText("غير مشمول بقاعدة R-SRC-01")
                badge_widget.setProperty("class", "badge-neutral")
            elif any_out_of_spec:
                badge_widget.setText("⚠️ خارج النطاق")
                badge_widget.setProperty("class", "badge-warning")
            else:
                badge_widget.setText("✓ كافية للفحص")
                badge_widget.setProperty("class", "badge-valid")

            self.table.setCellWidget(row, 7, badge_widget)
            self.table.setItem(row, 8, QTableWidgetItem(rec["facility_name"] or "—"))

    def _get_selected_source_id(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            selected = self.table.selectedItems()
            if not selected:
                return None
            row = selected[0].row()
        item = self.table.item(row, 0)
        return int(item.text()) if item else None

    def _on_add_source(self) -> None:
        dlg = AddEditSourceDialog(repo=self.repo, session=self.session, parent=self)
        if _exec_dlg(dlg) == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_edit_source(self) -> None:
        src_id = self._get_selected_source_id()
        if not src_id:
            _show_msg(self, "warn", "تنبيه", "يرجى تحديد مصدر مشع من الجدول أولاً.")
            return
        dlg = AddEditSourceDialog(repo=self.repo, session=self.session, source_id=src_id, parent=self)
        if _exec_dlg(dlg) == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_view_details(self) -> None:
        src_id = self._get_selected_source_id()
        if not src_id:
            _show_msg(self, "warn", "تنبيه", "يرجى تحديد مصدر مشع من الجدول أولاً.")
            return
        data = self.repo.get_by_id(src_id)
        if data:
            dlg = SourceDetailDialog(data=data, parent=self)
            _exec_dlg(dlg)

    def _on_retire_source(self) -> None:
        src_id = self._get_selected_source_id()
        if not src_id:
            _show_msg(self, "warn", "تنبيه", "يرجى تحديد مصدر مشع من الجدول أولاً.")
            return

        src = self.repo.get_by_id(src_id)
        if not src:
            return

        if src.get("status") == "RETIRED":
            _show_msg(self, "info", "معلومة", "هذا المصدر محال للتقاعد بالفعل.")
            return

        if _ask_confirm(
            self,
            "تأكيد إحالة المصدر المشع للتقاعد",
            f"هل أنت متأكد من إحالة المصدر ({src['source_code']}) للتقاعد؟\nسيتم إيقاف استخدامه في المعايرات الجديدة.",
        ):
            self.repo.retire(src_id, self.session, reason="إحالة إدارية عبر الواجهة")
            self.refresh()

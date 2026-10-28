"""Instruments management view for KASHIF-QA.

Handles registry of test instruments (NIM Bin, HV Supply, Amplifier, MCA, Oscilloscope):
- Table listing with calibration status badges (evaluated via R-INST-01)
- Red warning badge with icon and text for expired calibration
- Add/Edit dialog with structured fields (ripple mV, MCA channels, shaping range)
- Details dialog and safe retirement with confirmation
"""

from __future__ import annotations

from datetime import date, datetime
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
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

from kashif.core.models import Instrument, InstrumentType
from kashif.core.rules import eval_r_inst_01_calibration
from kashif.db.repositories import (
    DuplicateEntityError,
    EntityNotFoundError,
    InstrumentRepository,
)
from kashif.gui.bidi import (
    format_mixed_text,
    format_status_ar,
    isolate_ltr,
    isolate_quantity,
)
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


INSTRUMENT_TYPE_LABELS: Dict[str, str] = {
    "NIM_BIN": "سلة نيم (NIM Bin)",
    "HV_SUPPLY": "مجهز جهد عالي (HV Supply)",
    "AMPLIFIER": "مضخم نبضات (Spectroscopy Amplifier)",
    "MCA": "محلل متعدد القنوات (Multichannel Analyzer - MCA)",
    "SCOPE": "راسم إشارة (Oscilloscope)",
}


def _format_instrument_type(t: str) -> str:
    """Arabic label for instrument type from central dictionary."""
    return INSTRUMENT_TYPE_LABELS.get(t, t)


class AddEditInstrumentDialog(QDialog):
    """Dialog for adding or editing a test instrument."""

    def __init__(
        self,
        repo: InstrumentRepository,
        session: Session,
        instrument_id: Optional[int] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.repo = repo
        self.session = session
        self.instrument_id = instrument_id

        title = "تعديل جهاز فحص" if instrument_id else "إضافة جهاز فحص جديد — منظومة كاشف"
        self.setWindowTitle(title)
        self.setMinimumWidth(540)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())
        enable_dialog_window_controls(self)

        self._init_ui()
        if self.instrument_id:
            self._load_existing_data()
        adjust_dialog_size_to_screen(self, preferred_width=580, preferred_height=700)

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

        # 1. Basic Info
        basic_group = QGroupBox("البيانات الأساسية للجهاز")
        form_basic = QFormLayout(basic_group)
        form_basic.setSpacing(10)

        self.combo_type = QComboBox()
        self.combo_type.addItem("— اختر النوع —", "")
        for it in InstrumentType:
            self.combo_type.addItem(_format_instrument_type(it.value), it.value)

        self.edit_mfr = QLineEdit()
        self.edit_model = QLineEdit()
        self.edit_serial = QLineEdit()
        self.edit_facility = QLineEdit()

        form_basic.addRow("نوع الجهاز (*):", self.combo_type)
        form_basic.addRow("الشركة الصانعة (*):", self.edit_mfr)
        form_basic.addRow("الطراز (*):", self.edit_model)
        form_basic.addRow("الرقم التسلسلي (*):", self.edit_serial)
        form_basic.addRow("اسم المنشأة / المعمل:", self.edit_facility)
        layout.addWidget(basic_group)

        # 2. Technical Specs (Ripple, MCA channels, Shaping range)
        specs_group = QGroupBox("المواصفات الفنية المتقدمة")
        form_specs = QFormLayout(specs_group)
        form_specs.setSpacing(10)

        self.edit_ripple = QLineEdit()
        self.edit_ripple.setPlaceholderText("التموج بالمللي فولت (mV) - لمجهز الجهد")

        self.edit_regulation = QLineEdit()
        self.edit_regulation.setPlaceholderText("نسبة تنظيم الجهد %")

        self.edit_channels = QLineEdit()
        self.edit_channels.setPlaceholderText("عدد قنوات MCA (مثال: 4096 أو 8192)")

        shaping_row = QHBoxLayout()
        self.edit_shaping_min = QLineEdit()
        self.edit_shaping_min.setPlaceholderText("الحد الأدنى (µs) مثال: 0.5")
        self.edit_shaping_max = QLineEdit()
        self.edit_shaping_max.setPlaceholderText("الحد الأقصى (µs) مثال: 12.0")
        shaping_row.addWidget(QLabel("من:"))
        shaping_row.addWidget(self.edit_shaping_min)
        shaping_row.addWidget(QLabel("إلى:"))
        shaping_row.addWidget(self.edit_shaping_max)

        form_specs.addRow("تموج الجهد (Ripple mV):", self.edit_ripple)
        form_specs.addRow("تنظيم الجهد (Regulation %):", self.edit_regulation)
        form_specs.addRow("عدد قنوات MCA:", self.edit_channels)
        form_specs.addRow("نطاق زمن التشكيل:", shaping_row)
        layout.addWidget(specs_group)

        # 3. Calibration Dates & Service Status
        cal_group = QGroupBox("شهادة المعايرة والحالة التشغيلية")
        form_cal = QFormLayout(cal_group)
        form_cal.setSpacing(10)

        self.chk_has_cal = QCheckBox("الجهاز خاضع للمعايرة وله شهادة سارية")
        self.chk_has_cal.setChecked(True)
        self.chk_has_cal.toggled.connect(self._on_cal_toggled)

        self.date_cal = QDateEdit()
        self.date_cal.setCalendarPopup(True)
        self.date_cal.setDate(QDate.currentDate().addYears(-1))

        self.date_expiry = QDateEdit()
        self.date_expiry.setCalendarPopup(True)
        self.date_expiry.setDate(QDate.currentDate().addYears(1))

        self.chk_in_service = QCheckBox("الجهاز جاهز وفي الخدمة المعملية (In-Service)")
        self.chk_in_service.setChecked(True)

        form_cal.addRow("", self.chk_has_cal)
        form_cal.addRow("تاريخ المعايرة:", self.date_cal)
        form_cal.addRow("تاريخ انتهاء المعايرة:", self.date_expiry)
        form_cal.addRow("حالة الخدمة:", self.chk_in_service)
        layout.addWidget(cal_group)

        # 4. Notes
        self.edit_notes = QTextEdit()
        self.edit_notes.setMaximumHeight(60)
        self.edit_notes.setPlaceholderText("ملاحظات إضافية...")
        layout.addWidget(QLabel("ملاحظات:"))
        layout.addWidget(self.edit_notes)
        layout.addStretch()

        self.scroll_area.setWidget(scroll_content)
        root_layout.addWidget(self.scroll_area, 1)

        # Error text (fixed outside scroll area)
        self.lbl_error = QLabel()
        self.lbl_error.setProperty("class", "error-text")
        self.lbl_error.setWordWrap(True)
        root_layout.addWidget(self.lbl_error)

        # Action Buttons (fixed outside scroll area)
        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("حفظ الجهاز")
        self.btn_save.clicked.connect(self._on_save)

        self.btn_cancel = QPushButton("إلغاء")
        self.btn_cancel.setProperty("class", "secondary")
        self.btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_cancel)
        root_layout.addLayout(btn_row)

    def _on_cal_toggled(self, checked: bool) -> None:
        self.date_cal.setEnabled(checked)
        self.date_expiry.setEnabled(checked)

    def _load_existing_data(self) -> None:
        if not self.instrument_id:
            return
        data = self.repo.get_by_id(self.instrument_id)
        if not data:
            return

        idx = self.combo_type.findData(data.get("instrument_type"))
        if idx >= 0:
            self.combo_type.setCurrentIndex(idx)

        self.edit_mfr.setText(data.get("manufacturer") or "")
        self.edit_model.setText(data.get("model") or "")
        self.edit_serial.setText(data.get("serial_number") or "")
        self.edit_facility.setText(data.get("facility_name") or "")
        self.edit_notes.setText(data.get("notes") or "")

        if data.get("ripple_mv") is not None:
            self.edit_ripple.setText(str(data["ripple_mv"]))
        if data.get("regulation_pct") is not None:
            self.edit_regulation.setText(str(data["regulation_pct"]))
        if data.get("channels_count") is not None:
            self.edit_channels.setText(str(data["channels_count"]))
        if data.get("shaping_range_min_us") is not None:
            self.edit_shaping_min.setText(str(data["shaping_range_min_us"]))
        if data.get("shaping_range_max_us") is not None:
            self.edit_shaping_max.setText(str(data["shaping_range_max_us"]))

        if data.get("cal_date"):
            qd = QDate.fromString(data["cal_date"], Qt.DateFormat.ISODate)
            if qd.isValid():
                self.date_cal.setDate(qd)
        if data.get("cal_expiry"):
            qd = QDate.fromString(data["cal_expiry"], Qt.DateFormat.ISODate)
            if qd.isValid():
                self.date_expiry.setDate(qd)
        else:
            self.chk_has_cal.setChecked(False)

        self.chk_in_service.setChecked(bool(data.get("is_in_service", 1)))

    def _on_save(self) -> None:
        """Validate inputs on submit and commit."""
        self.lbl_error.setText("")

        mfr = self.edit_mfr.text().strip()
        model = self.edit_model.text().strip()
        serial = self.edit_serial.text().strip()
        facility = self.edit_facility.text().strip() or None
        notes = self.edit_notes.toPlainText().strip() or None
        itype_val = self.combo_type.currentData()
        if not itype_val:
            self.lbl_error.setText("يرجى اختيار نوع جهاز الفحص من القائمة.")
            return

        itype = InstrumentType(itype_val)

        if not mfr or not model or not serial:
            self.lbl_error.setText("يرجى ملء كافة الحقول الأساسية الإلزامية المؤشرة بـ (*).")
            return

        try:
            ripple = float(self.edit_ripple.text().strip()) if self.edit_ripple.text().strip() else None
            regulation = float(self.edit_regulation.text().strip()) if self.edit_regulation.text().strip() else None
            channels = int(self.edit_channels.text().strip()) if self.edit_channels.text().strip() else None
            shaping_min = float(self.edit_shaping_min.text().strip()) if self.edit_shaping_min.text().strip() else None
            shaping_max = float(self.edit_shaping_max.text().strip()) if self.edit_shaping_max.text().strip() else None

            if self.chk_has_cal.isChecked():
                cal_d = self.date_cal.date().toPyDate()
                exp_d = self.date_expiry.date().toPyDate()
            else:
                cal_d = None
                exp_d = None

            instrument_obj = Instrument(
                instrument_type=itype,
                manufacturer=mfr,
                model=model,
                serial_number=serial,
                ripple_mv=ripple,
                regulation_pct=regulation,
                channels_count=channels,
                shaping_range_min_us=shaping_min,
                shaping_range_max_us=shaping_max,
                cal_date=cal_d,
                cal_expiry=exp_d,
                is_in_service=self.chk_in_service.isChecked(),
                facility_name=facility,
                notes=notes,
            )

            if self.instrument_id:
                self.repo.update(self.instrument_id, instrument_obj, self.session)
            else:
                self.repo.create(instrument_obj, self.session)

            self.accept()
        except DuplicateEntityError as e:
            self.lbl_error.setText(str(e))
        except ValueError as e:
            self.lbl_error.setText(f"خطأ في قيم الحقول: {str(e)}")
        except Exception as e:
            self.lbl_error.setText(f"تعذر الحفظ: {str(e)}")


class InstrumentDetailDialog(QDialog):
    """Dialog displaying detailed instrument specifications and calibration status."""

    def __init__(self, data: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.data = data
        self.setWindowTitle(f"بطاقة جهاز الفحص — {data.get('model')}")
        self.setMinimumWidth(500)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())
        enable_dialog_window_controls(self)
        self._init_ui()
        adjust_dialog_size_to_screen(self, preferred_width=520, preferred_height=600)

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
        form.addRow("نوع الجهاز:", QLabel(format_mixed_text(_format_instrument_type(d.get("instrument_type") or "—"))))
        form.addRow("الشركة الصانعة:", QLabel(format_mixed_text(str(d.get("manufacturer") or "—"))))
        form.addRow("الطراز:", QLabel(format_mixed_text(str(d.get("model") or "—"))))
        form.addRow("الرقم التسلسلي:", QLabel(format_mixed_text(str(d.get("serial_number") or "—"))))
        form.addRow("المنشأة / المعمل:", QLabel(format_mixed_text(str(d.get("facility_name") or "—"))))
        form.addRow("حالة الخدمة:", QLabel("في الخدمة المعملية" if d.get("is_in_service") else "خارج الخدمة"))
        if d.get("status"):
            form.addRow("الحالة:", QLabel(format_status_ar(d.get("status"))))

        # Calibration evaluation via R-INST-01
        cal_exp = d.get("cal_expiry")
        if cal_exp:
            is_corrupt = False
            is_expired = False
            try:
                exp_date = date.fromisoformat(cal_exp)
                rule_res = eval_r_inst_01_calibration(exp_date)
                is_expired = rule_res.verdict.value == "WARNING"
            except (ValueError, TypeError) as e:
                logger.warning(
                    "Corrupt or invalid calibration expiry date '%s' for instrument ID %s (SN: %s): %s",
                    cal_exp,
                    d.get("id"),
                    d.get("serial_number"),
                    e,
                )
                is_corrupt = True

            if is_corrupt:
                lbl_badge = QLabel("⚠️ بيانات غير صالحة")
                lbl_badge.setProperty("class", "badge-expired")
            elif is_expired:
                lbl_badge = QLabel("⚠️ منتهي المعايرة")
                lbl_badge.setProperty("class", "badge-expired")
            else:
                lbl_badge = QLabel("✓ سارية المعايرة")
                lbl_badge.setProperty("class", "badge-valid")

            self.lbl_cal_badge = lbl_badge
            cal_row = QHBoxLayout()
            cal_row.addWidget(QLabel(format_mixed_text(f"{d.get('cal_date') or '—'} إلى {cal_exp}")))
            cal_row.addWidget(lbl_badge)
            cal_row.addStretch()
            form.addRow("حالة المعايرة:", cal_row)
        else:
            form.addRow("حالة المعايرة:", QLabel("غير محدد"))

        if d.get("ripple_mv") is not None:
            form.addRow("تموج الجهد (Ripple):", QLabel(isolate_quantity(d.get("ripple_mv"), "mV")))
        if d.get("regulation_pct") is not None:
            form.addRow("تنظيم الجهد (Regulation):", QLabel(isolate_quantity(d.get("regulation_pct"), "%")))
        if d.get("channels_count") is not None:
            form.addRow("عدد قنوات MCA:", QLabel(isolate_quantity(d.get("channels_count"), "قناة")))
        if d.get("shaping_range_min_us") is not None and d.get("shaping_range_max_us") is not None:
            range_str = f"{d.get('shaping_range_min_us')} – {d.get('shaping_range_max_us')}"
            form.addRow("نطاق زمن التشكيل:", QLabel(isolate_quantity(range_str, "µs")))

        if d.get("notes"):
            form.addRow("ملاحظات:", QLabel(format_mixed_text(str(d.get("notes")))))

        self.scroll_area.setWidget(card)
        root_layout.addWidget(self.scroll_area, 1)

        btn_close = QPushButton("إغلاق")
        btn_close.setProperty("class", "secondary")
        btn_close.clicked.connect(self.accept)
        root_layout.addWidget(btn_close)


class InstrumentsView(QWidget):
    """Main registry view for test instruments."""

    def __init__(self, conn: sqlite3.Connection, session: Session, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.conn = conn
        self.session = session
        self.repo = InstrumentRepository(conn)

        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header Bar
        header = QHBoxLayout()
        title = QLabel("سجل أجهزة الفحص والمعايرة (Instruments)")
        title.setProperty("class", "title")
        header.addWidget(title)
        header.addStretch()

        self.btn_add = QPushButton("➕ إضافة جهاز فحص")
        if self.session.can("manage_instruments"):
            self.btn_add.clicked.connect(self._on_add_instrument)
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
        self.edit_search.setPlaceholderText("🔍 بحث بالرقم التسلسلي أو الطراز أو الشركة الصانعة...")
        self.edit_search.textChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.edit_search, stretch=2)

        filter_bar.addWidget(QLabel("النوع:"))
        self.combo_type_filter = QComboBox()
        self.combo_type_filter.addItem("كافة أنواع الأجهزة", "")
        for it in InstrumentType:
            self.combo_type_filter.addItem(_format_instrument_type(it.value), it.value)
        self.combo_type_filter.currentIndexChanged.connect(self._on_filter_changed)
        filter_bar.addWidget(self.combo_type_filter, stretch=1)

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
            "النوع",
            "الشركة الصانعة",
            "الطراز",
            "الرقم التسلسلي",
            "انتهاء المعايرة",
            "حالة المعايرة",
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
        self.btn_edit.clicked.connect(self._on_edit_instrument)
        self.btn_edit.setVisible(self.session.can("manage_instruments"))
        action_row.addWidget(self.btn_edit)

        self.btn_retire = QPushButton("🛑 إحالة للتقاعد")
        self.btn_retire.setProperty("class", "danger")
        self.btn_retire.clicked.connect(self._on_retire_instrument)
        self.btn_retire.setVisible(self.session.can("manage_instruments"))
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
        type_filter = self.combo_type_filter.currentData() or None
        query = self.edit_search.text().strip() or None

        records = self.repo.list_all(facility=facility, query=query, include_retired=True)
        if type_filter:
            records = [r for r in records if r["instrument_type"] == type_filter]

        self.table.setRowCount(len(records))
        for row, rec in enumerate(records):
            self.table.setItem(row, 0, QTableWidgetItem(str(rec["id"])))
            self.table.setItem(row, 1, QTableWidgetItem(rec["instrument_type"]))
            self.table.setItem(row, 2, QTableWidgetItem(rec["manufacturer"]))
            self.table.setItem(row, 3, QTableWidgetItem(rec["model"]))
            self.table.setItem(row, 4, QTableWidgetItem(rec["serial_number"]))
            self.table.setItem(row, 5, QTableWidgetItem(rec["cal_expiry"] or "—"))

            # R-INST-01 evaluation for calibration badge
            cal_exp = rec.get("cal_expiry")
            badge_widget = QLabel()
            badge_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if cal_exp:
                try:
                    exp_date = date.fromisoformat(cal_exp)
                    rule_res = eval_r_inst_01_calibration(exp_date)
                    is_expired = rule_res.verdict.value == "WARNING"
                    if is_expired:
                        badge_widget.setText("⚠️ منتهي المعايرة")
                        badge_widget.setProperty("class", "badge-expired")
                    else:
                        badge_widget.setText("✓ سارية")
                        badge_widget.setProperty("class", "badge-valid")
                except Exception as e:
                    logger.warning("Corrupt or invalid calibration expiry date '%s' for instrument %s: %s", cal_exp, rec.get("id"), e)
                    badge_widget.setText("⚠️ بيانات غير صالحة")
                    badge_widget.setProperty("class", "badge-expired")
            else:
                badge_widget.setText("—")

            self.table.setCellWidget(row, 6, badge_widget)
            self.table.setItem(row, 7, QTableWidgetItem(rec["facility_name"] or "—"))

            status_str = format_status_ar(rec.get("status"))
            self.table.setItem(row, 8, QTableWidgetItem(status_str))

    def _get_selected_instrument_id(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            selected = self.table.selectedItems()
            if not selected:
                return None
            row = selected[0].row()
        item = self.table.item(row, 0)
        return int(item.text()) if item else None

    def _on_add_instrument(self) -> None:
        dlg = AddEditInstrumentDialog(repo=self.repo, session=self.session, parent=self)
        if _exec_dlg(dlg) == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_edit_instrument(self) -> None:
        inst_id = self._get_selected_instrument_id()
        if not inst_id:
            _show_msg(self, "warn", "تنبيه", "يرجى تحديد جهاز فحص من الجدول أولاً.")
            return
        dlg = AddEditInstrumentDialog(repo=self.repo, session=self.session, instrument_id=inst_id, parent=self)
        if _exec_dlg(dlg) == QDialog.DialogCode.Accepted:
            self.refresh()

    def _on_view_details(self) -> None:
        inst_id = self._get_selected_instrument_id()
        if not inst_id:
            _show_msg(self, "warn", "تنبيه", "يرجى تحديد جهاز فحص من الجدول أولاً.")
            return
        data = self.repo.get_by_id(inst_id)
        if data:
            dlg = InstrumentDetailDialog(data=data, parent=self)
            _exec_dlg(dlg)

    def _on_retire_instrument(self) -> None:
        inst_id = self._get_selected_instrument_id()
        if not inst_id:
            _show_msg(self, "warn", "تنبيه", "يرجى تحديد جهاز فحص من الجدول أولاً.")
            return

        inst = self.repo.get_by_id(inst_id)
        if not inst:
            return

        if inst.get("status") == "RETIRED":
            _show_msg(self, "info", "معلومة", "هذا الجهاز محال للتقاعد بالفعل.")
            return

        if _ask_confirm(
            self,
            "تأكيد إحالة الجهاز للتقاعد",
            f"هل أنت متأكد من إحالة الجهاز ({inst['model']} — S/N: {inst['serial_number']}) للتقاعد؟\nسيتم إخراجه من الخدمة المعملية فوراً.",
        ):
            self.repo.retire(inst_id, self.session, reason="إحالة إدارية عبر الواجهة")
            self.refresh()

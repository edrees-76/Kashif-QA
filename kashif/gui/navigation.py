"""Right sidebar navigation component for KASHIF-QA.

Implements Decision 12:
- Right-aligned sidebar collapsible into icons.
- Single declarative configuration list.
- Role-based visibility via session.can().
- Unready sections are hidden, never displayed disabled.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from kashif.gui.bidi import format_mixed_text
from kashif.services.session import Session


# Declarative Navigation Items Configuration List
# Unready sections (like inspections, reports) are intentionally omitted.
NAV_ITEMS: List[Dict[str, Any]] = [
    {
        "id": "dashboard",
        "title": "الرئيسية",
        "icon": "🏠",
        "permission": None,
    },
    {
        "id": "detectors",
        "title": "الكواشف",
        "icon": "⚛️",
        "permission": "view_detectors",
    },
    {
        "id": "instruments",
        "title": "أجهزة الفحص",
        "icon": "📟",
        "permission": "view_instruments",
    },
    {
        "id": "sources",
        "title": "المصادر المشعة",
        "icon": "☢️",
        "permission": "view_sources",
    },
    {
        "id": "users",
        "title": "المستخدمون",
        "icon": "👥",
        "permission": "manage_users",
    },
]


class SidebarNavigation(QFrame):
    """Collapsible right sidebar navigation widget."""

    section_selected = pyqtSignal(str)

    def __init__(self, session: Optional[Session] = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.session = session
        self.is_collapsed: bool = False
        self.active_section_id: str = "dashboard"
        self._buttons: Dict[str, QPushButton] = {}
        self._items: List[Dict[str, Any]] = []

        self.setProperty("class", "sidebar")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._init_ui()

    def _init_ui(self) -> None:
        self.layout_main = QVBoxLayout(self)
        self.layout_main.setContentsMargins(10, 14, 10, 14)
        self.layout_main.setSpacing(8)

        # Header with toggle button
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 4)

        self.lbl_title = QLabel("القائمة الرئيسية")
        self.lbl_title.setProperty("class", "h2")
        header_row.addWidget(self.lbl_title)

        header_row.addStretch()

        self.btn_toggle = QPushButton("◀")
        self.btn_toggle.setProperty("class", "nav-toggle")
        self.btn_toggle.setToolTip("طيّ / توسيع القائمة")
        self.btn_toggle.clicked.connect(self.toggle_collapse)
        header_row.addWidget(self.btn_toggle)

        self.layout_main.addLayout(header_row)

        # Navigation buttons container
        self.buttons_layout = QVBoxLayout()
        self.buttons_layout.setSpacing(6)
        self.layout_main.addLayout(self.buttons_layout)

        self.layout_main.addStretch()

        self._build_items()
        self._update_sidebar_width()

    def _build_items(self) -> None:
        """Filter items by session.can() and create buttons."""
        # Clear any existing buttons
        for btn in self._buttons.values():
            btn.setParent(None)
        self._buttons.clear()

        # Filter items
        self._items = []
        for item in NAV_ITEMS:
            perm = item["permission"]
            if perm is None or (self.session and self.session.can(perm)):
                self._items.append(item)

        # Create navigation buttons
        for item in self._items:
            sec_id = item["id"]
            icon = item["icon"]
            title = item["title"]

            btn = QPushButton()
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(title)

            # Store mapping
            self._buttons[sec_id] = btn

            # Wire click signal
            btn.clicked.connect(lambda checked=False, sid=sec_id: self._on_button_clicked(sid))

            self.buttons_layout.addWidget(btn)

        self._update_buttons_display()

    def _on_button_clicked(self, section_id: str) -> None:
        """Handle button click and notify parent."""
        self.set_active_section(section_id)
        self.section_selected.emit(section_id)

    def set_active_section(self, section_id: str) -> None:
        """Highlight active section button."""
        self.active_section_id = section_id
        for sid, btn in self._buttons.items():
            if sid == section_id:
                btn.setProperty("class", "nav-item-active")
            else:
                btn.setProperty("class", "nav-item")
            # Force stylesheet refresh on dynamic property change
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def toggle_collapse(self) -> None:
        """Toggle between expanded and collapsed (icons-only) states."""
        self.is_collapsed = not self.is_collapsed
        self.btn_toggle.setText("▶" if self.is_collapsed else "◀")
        self.lbl_title.setVisible(not self.is_collapsed)
        self._update_buttons_display()
        self._update_sidebar_width()

    def _update_buttons_display(self) -> None:
        """Update button text and styling according to collapse state."""
        for item in self._items:
            sec_id = item["id"]
            btn = self._buttons.get(sec_id)
            if not btn:
                continue

            if self.is_collapsed:
                btn.setText(item["icon"])
            else:
                btn.setText(f"{item['icon']}  {item['title']}")

        self.set_active_section(self.active_section_id)

    def _update_sidebar_width(self) -> None:
        """Adjust container width constraints for collapsed vs expanded."""
        if self.is_collapsed:
            self.setMinimumWidth(64)
            self.setMaximumWidth(70)
        else:
            self.setMinimumWidth(190)
            self.setMaximumWidth(230)

    def get_visible_sections(self) -> List[str]:
        """Return the list of visible section IDs."""
        return [item["id"] for item in self._items]

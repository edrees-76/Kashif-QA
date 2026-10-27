"""Package: views for KASHIF-QA GUI."""

from kashif.gui.views.detectors_view import DetectorsView
from kashif.gui.views.instruments_view import InstrumentsView
from kashif.gui.views.sources_view import SourcesView
from kashif.gui.views.users_view import UsersManagementDialog

__all__ = [
    "DetectorsView",
    "InstrumentsView",
    "SourcesView",
    "UsersManagementDialog",
]

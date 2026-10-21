"""Re-exports the data source dialog, which moved to gpq_downloader/gui/dialog.py (#195).

Existing `from gpq_downloader.dialog import ...` uses keep working. The shim
is deleted at 1.0 (#198); import from gui.dialog instead.
"""

from .gui.dialog import OSM_CHECKBOX_COLUMNS, DataSourceDialog  # noqa: F401

"""The output formats the save dialog offers, and their file extensions.

The dialog filter string used to be a literal in plugin.py, duplicated by
hand from whatever format worker.py actually knew how to write. Keeping the
mapping here means plugin.py's dialog, the settings panel's format picker,
and copy_options.py's dispatch-by-extension all read the same table instead
of three things that have to be kept in sync by eye.
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class OutputFormat:
    name: str
    extension: str
    dialog_filter: str


FORMATS: Tuple[OutputFormat, ...] = (
    OutputFormat("GeoParquet", ".parquet", "GeoParquet (*.parquet)"),
    OutputFormat("DuckDB Database", ".duckdb", "DuckDB Database (*.duckdb)"),
    OutputFormat("GeoPackage", ".gpkg", "GeoPackage (*.gpkg)"),
    OutputFormat("FlatGeobuf", ".fgb", "FlatGeobuf (*.fgb)"),
    OutputFormat("GeoJSON", ".geojson", "GeoJSON (*.geojson)"),
)

DEFAULT_FORMAT_NAME = "GeoParquet"


def format_names() -> Tuple[str, ...]:
    """The format names in table order, for a settings panel's dropdown."""
    return tuple(f.name for f in FORMATS)


def dialog_filters() -> str:
    """The ``;;``-joined filter string QFileDialog.getSaveFileName expects."""
    return ";;".join(f.dialog_filter for f in FORMATS)


def format_for(name: str) -> OutputFormat:
    """The format stored under ``name``, or GeoParquet if ``name`` is unknown.

    A name can go stale: a settings file written by an older version with a
    format this version dropped, or a typo that never round-tripped through
    the dialog. Falling back to GeoParquet keeps that recoverable instead of
    raising.
    """
    for f in FORMATS:
        if f.name == name:
            return f
    return FORMATS[0]

"""The output formats the save dialog offers, and their file extensions.

The dialog filter string used to be a literal in plugin.py, duplicated by
hand from whatever format worker.py actually knew how to write. Keeping the
mapping here means plugin.py's dialog, the settings panel's format picker,
and copy_options.py's dispatch-by-extension all read the same table instead
of three things that have to be kept in sync by eye.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


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

# What the large-GeoJSON warning never offers: GeoJSON is what triggered the
# warning, and a DuckDB database is not an interchange file. Everything else
# in FORMATS is an alternative, derived by exclusion so a format added to
# the table reaches the warning dialog with no second list to update - the
# warning used to carry its own copy of all three filter strings, which a
# new format or a renamed one would have left silently behind.
LARGE_DATASET_EXCLUDED = ("GeoJSON", "DuckDB Database")


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


def format_for_path(path: str) -> Optional[OutputFormat]:
    """The format whose extension ``path`` ends with, or None for an unknown one.

    Matched case-insensitively, because the save dialog takes a typed
    ``.GPKG`` and writes it. This is how the plugin learns which format it
    just wrote: the extension on disk decides what the file actually is,
    whichever filter happened to be selected when the name was typed.
    """
    lowered = str(path).lower()
    for f in FORMATS:
        if lowered.endswith(f.extension):
            return f
    return None


def large_dataset_alternatives() -> Tuple[OutputFormat, ...]:
    """The formats the large-GeoJSON warning offers, in table order.

    FORMATS minus LARGE_DATASET_EXCLUDED: derived, not listed, so it can
    never name a format the table no longer has (the previous allowlist
    raised KeyError from inside a Qt slot if the two drifted apart).
    """
    return tuple(f for f in FORMATS if f.name not in LARGE_DATASET_EXCLUDED)

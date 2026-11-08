"""The QgsSettings keys the dialog and plugin remember between sessions.

Every value is stored under the Plugins section, so QSettings keeps it as
plugins/gpq_downloader/<name>, except where noted. The helpers make exactly
the calls the dialog and plugin made inline before (#195): a new QgsSettings
for each read or write, the same defaults and the same types.
"""

from qgis.core import QgsSettings

from ..core import copy_options

RADIO_SELECTION = "gpq_downloader/radio_selection"
CLIP_TO_AREA = "gpq_downloader/clip_to_area"
AOI_ENABLED = "gpq_downloader/aoi_enabled"
LAST_USED_EXTENT = "gpq_downloader/last_used_extent"
# The attribute filter as last typed. The text is remembered so it can be
# tweaked rather than retyped; the tick that makes it apply is not, so a
# restored filter cannot shrink a download nobody asked to filter (#231).
ATTRIBUTE_FILTER = "gpq_downloader/attribute_filter"

# One key per checkbox; fill in {key} with the preset's key
CHECKBOX = "gpq_downloader/checkbox_{key}"
BASE_SUBTYPE_CHECKBOX = "gpq_downloader/base_subtype_checkbox_{key}"
DIVISIONS_SUBTYPE_CHECKBOX = "gpq_downloader/divisions_subtype_checkbox_{key}"
OSM_CHECKBOX = "gpq_downloader/osm_checkbox_{key}"

# The settings panel's keys, added across Tasks 3-6.
OUTPUT_FORMAT = "gpq_downloader/output_format"
OUTPUT_FOLDER = "gpq_downloader/output_folder"
REMEMBER_LAST_FOLDER = "gpq_downloader/remember_last_folder"
# The folder itself, last saved to. This is storage the plugin consults, not
# a setting the panel shows; REMEMBER_LAST_FOLDER only decides whether it is
# read. Written by plugin.py, not by the options page.
LAST_OUTPUT_FOLDER = "gpq_downloader/last_output_folder"
COMPRESSION_CODEC = "gpq_downloader/compression_codec"
COMPRESSION_LEVEL = "gpq_downloader/compression_level"
ROW_GROUP_SIZE = "gpq_downloader/row_group_size"
SPATIAL_SORT = "gpq_downloader/spatial_sort"
LOG_LEVEL = "gpq_downloader/log_level"

LOG_LEVELS = ("Debug", "Info", "Warning", "Critical")
DEFAULT_LOG_LEVEL = "Info"


def save_value(key, value):
    """Store value under key in the Plugins section."""
    QgsSettings().setValue(
        key,
        value,
        section=QgsSettings.Section.Plugins,
    )


def load_flag(key, default=False):
    """The bool stored under key in the Plugins section, default if none is.

    A checkbox that starts out ticked has to pass default=True here rather
    than fall back to True at the call site: "nothing stored yet" and "the
    user unticked it" are both a False from a plain read, so the fallback
    would re-tick the box on every restart. QgsSettings only returns the
    default when the key is absent, so a stored False still wins.
    """
    return QgsSettings().value(
        key,
        default,
        type=bool,
        section=QgsSettings.Section.Plugins,
    )


def load_text(key):
    """The str stored under key in the Plugins section, "" if none is.

    Anything that is not a str reads as "". `type=str` normally sees to that,
    but the value still travels through QSettings' own storage, and a key
    written by another version - or by a test that stands a mock in for
    QgsSettings - can come back as something a QLineEdit will not take.
    """
    value = QgsSettings().value(
        key,
        "",
        type=str,
        section=QgsSettings.Section.Plugins,
    )
    return value if isinstance(value, str) else ""


def load_int(key, default):
    """The int stored under key, or default when absent or not convertible.

    This reads the raw value and converts by hand rather than passing
    type=int: QgsSettings.value(key, default, type=int) turns a non-numeric
    stored value into 0, which would be indistinguishable from the user
    having genuinely chosen 0. Converting by hand also guards the way
    load_text guards a non-str: a test that stands a MagicMock in for
    QgsSettings must get default back, not a Mock a QSpinBox cannot take.
    """
    value = QgsSettings().value(key, default, section=QgsSettings.Section.Plugins)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_choice(key, allowed, default):
    """The str stored under key if it is one of allowed, else default.

    A stored choice can go stale the same way a stored format name can
    (see output_formats.format_for): a value written by an older version,
    or by a test standing a mock in for QgsSettings, that no longer matches
    anything this version offers. Falling back to default keeps that
    recoverable instead of handing an unrecognised value to a combo box.
    """
    value = QgsSettings().value(
        key,
        default,
        type=str,
        section=QgsSettings.Section.Plugins,
    )
    if not isinstance(value, str) or value not in allowed:
        return default
    return value


def load_download_settings():
    """The DownloadJob/Worker write-settings, defaulted and clamped.

    Exactly the four keyword arguments those two take, so plugin.py can
    spread this dict at the one call site rather than each caller repeating
    the clamp and the fallbacks.
    """
    level = load_int(COMPRESSION_LEVEL, copy_options.DEFAULT_COMPRESSION_LEVEL)
    level = max(
        copy_options.MIN_COMPRESSION_LEVEL,
        min(copy_options.MAX_COMPRESSION_LEVEL, level),
    )

    row_group_size = load_int(ROW_GROUP_SIZE, None)
    if row_group_size is not None and row_group_size <= 0:
        row_group_size = None

    return {
        "compression_codec": load_choice(
            COMPRESSION_CODEC, copy_options.CODECS, copy_options.DEFAULT_CODEC
        ),
        "compression_level": level,
        "row_group_size": row_group_size,
        "spatial_sort": load_flag(SPATIAL_SORT, copy_options.DEFAULT_SPATIAL_SORT),
    }


def load_radio_selection():
    """The saved source button's text, or None. Read with no default or type."""
    return QgsSettings().value(RADIO_SELECTION, section=QgsSettings.Section.Plugins)


def save_last_used_extent(extent_str):
    """Store an extent's toString() under LAST_USED_EXTENT."""
    save_value(LAST_USED_EXTENT, extent_str)


def load_last_used_extent():
    """Read LAST_USED_EXTENT, as a str ("" if none is stored).

    Unlike every other read this passes no section, so it looks outside the
    Plugins section that save_last_used_extent() writes to and never finds the
    saved extent. That oddity is pinned by test_dialog_settings.py and kept.
    """
    return QgsSettings().value(LAST_USED_EXTENT, "", type=str)

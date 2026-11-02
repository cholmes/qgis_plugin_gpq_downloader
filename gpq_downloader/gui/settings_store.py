"""The QgsSettings keys the dialog and plugin remember between sessions.

Every value is stored under the Plugins section, so QSettings keeps it as
plugins/gpq_downloader/<name>, except where noted. The helpers make exactly
the calls the dialog and plugin made inline before (#195): a new QgsSettings
for each read or write, the same defaults and the same types.
"""

from qgis.core import QgsSettings

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


def save_value(key, value):
    """Store value under key in the Plugins section."""
    QgsSettings().setValue(
        key,
        value,
        section=QgsSettings.Section.Plugins,
    )


def load_flag(key):
    """The bool stored under key in the Plugins section, False if none is."""
    return QgsSettings().value(
        key,
        False,
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

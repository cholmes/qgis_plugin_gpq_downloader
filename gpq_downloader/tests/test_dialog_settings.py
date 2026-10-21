"""Characterization of what the dialog remembers between sessions.

The dialog and plugin keep their state in QgsSettings under the Plugins
section, which QSettings stores as plugins/gpq_downloader/<name>. Splitting the
dialog moves all of this, so the keys, their values and the round trip back
into a new dialog are pinned here, oddities included.

Each test starts with no gpq_downloader settings and puts the user's own back
afterwards.
"""

from unittest.mock import MagicMock, patch

import pytest
from qgis.core import QgsRectangle, QgsSettings

from gpq_downloader.dialog import DataSourceDialog
from gpq_downloader.plugin import QgisPluginGeoParquet

PREFIXES = ("plugins/gpq_downloader/", "gpq_downloader/")

OVERTURE_KEYS = ["buildings", "places", "transportation", "addresses", "divisions", "base"]
BASE_SUBTYPES = ["infrastructure", "land", "land_cover", "land_use", "water", "bathymetry"]
DIVISIONS_SUBTYPES = ["division", "division_area", "division_boundary"]
OSM_KEYS = [
    "addresses", "buildings", "boundaries", "highways", "parks", "pois", "settlements", "waterways", "wetlands",
]

ALL_SAVED_KEYS = sorted(
    ["plugins/gpq_downloader/clip_to_area", "plugins/gpq_downloader/aoi_enabled"]
    + [f"plugins/gpq_downloader/checkbox_{k}" for k in OVERTURE_KEYS]
    + [f"plugins/gpq_downloader/base_subtype_checkbox_{k}" for k in BASE_SUBTYPES]
    + [f"plugins/gpq_downloader/divisions_subtype_checkbox_{k}" for k in DIVISIONS_SUBTYPES]
    + [f"plugins/gpq_downloader/osm_checkbox_{k}" for k in OSM_KEYS]
)


def our_keys():
    return sorted(k for k in QgsSettings().allKeys() if k.startswith(PREFIXES))


@pytest.fixture
def clean_settings(qgs_app):
    """No gpq_downloader settings during the test; the previous ones after it."""
    settings = QgsSettings()
    saved = {k: settings.value(k) for k in our_keys()}
    for key in saved:
        settings.remove(key)
    yield settings
    for key in our_keys():
        settings.remove(key)
    for key, value in saved.items():
        settings.setValue(key, value)


@pytest.fixture
def iface():
    iface = MagicMock()
    iface.mainWindow.return_value = None
    return iface


@pytest.fixture
def dialogs(iface):
    """Make dialogs, and close each one afterwards."""
    made = []

    def make():
        dialog = DataSourceDialog(None, iface)
        made.append(dialog)
        return dialog

    yield make
    for dialog in made:
        dialog.close()


def flag(key):
    return QgsSettings().value(key, None, type=bool)


def test_a_new_dialog_saves_nothing(clean_settings, dialogs):
    dialogs()
    assert our_keys() == []


def test_the_keys_saved(clean_settings, dialogs):
    dialog = dialogs()
    dialog.save_checkbox_states()
    assert our_keys() == ALL_SAVED_KEYS
    assert all(flag(k) is False for k in ALL_SAVED_KEYS)


def test_toggling_a_checkbox_saves_every_state(clean_settings, dialogs):
    dialog = dialogs()
    dialog.osm_checkboxes["pois"].setChecked(True)
    # The toggled signal saves all checkboxes, not only the one toggled...
    assert flag("plugins/gpq_downloader/osm_checkbox_pois") is True
    assert flag("plugins/gpq_downloader/checkbox_buildings") is False
    # ...including clip_to_area and aoi_enabled
    assert our_keys() == ALL_SAVED_KEYS


def test_the_values_saved(clean_settings, dialogs):
    dialog = dialogs()
    checked = {
        "checkbox_places", "checkbox_base", "base_subtype_checkbox_land_use", "base_subtype_checkbox_water",
        "divisions_subtype_checkbox_division_area", "osm_checkbox_pois", "osm_checkbox_wetlands",
        "clip_to_area", "aoi_enabled",
    }
    dialog.overture_checkboxes["places"].setChecked(True)
    dialog.base_checkbox.setChecked(True)
    dialog.base_subtype_checkboxes["land_use"].setChecked(True)
    dialog.base_subtype_checkboxes["water"].setChecked(True)
    dialog.divisions_subtype_checkboxes["division_area"].setChecked(True)
    dialog.osm_checkboxes["pois"].setChecked(True)
    dialog.osm_checkboxes["wetlands"].setChecked(True)
    dialog.clip_checkbox.setChecked(True)
    dialog.extent_group.setChecked(True)
    dialog.save_checkbox_states()
    assert {k: flag(k) for k in ALL_SAVED_KEYS} == {
        k: k.split("/")[-1] in checked for k in ALL_SAVED_KEYS
    }


def test_a_new_dialog_restores_them(clean_settings, dialogs):
    first = dialogs()
    first.overture_checkboxes["transportation"].setChecked(True)
    first.divisions_checkbox.setChecked(True)
    first.divisions_subtype_checkboxes["division_boundary"].setChecked(True)
    first.base_subtype_checkboxes["bathymetry"].setChecked(True)  # with Base itself off
    first.osm_checkboxes["highways"].setChecked(True)
    first.clip_checkbox.setChecked(True)
    first.extent_group.setChecked(True)
    first.save_checkbox_states()

    second = dialogs()
    assert {k for k, c in second.overture_checkboxes.items() if c.isChecked()} == {"transportation", "divisions"}
    assert {k for k, c in second.divisions_subtype_checkboxes.items() if c.isChecked()} == {"division_boundary"}
    assert {k for k, c in second.base_subtype_checkboxes.items() if c.isChecked()} == {"bathymetry"}
    assert {k for k, c in second.osm_checkboxes.items() if c.isChecked()} == {"highways"}
    assert second.clip_checkbox.isChecked()
    assert second.extent_group.isChecked()
    # The subtype rows follow their parent checkbox
    assert not second.divisions_subtype_widget.isHidden()
    assert second.base_subtype_widget.isHidden()


def test_clip_is_restored_from_its_own_key(clean_settings, dialogs):
    clean_settings.setValue("gpq_downloader/clip_to_area", True, section=QgsSettings.Plugins)
    assert dialogs().clip_checkbox.isChecked()
    clean_settings.setValue("gpq_downloader/clip_to_area", False, section=QgsSettings.Plugins)
    assert not dialogs().clip_checkbox.isChecked()


@pytest.mark.parametrize("radio, name", [
    ("overture_radio", "Overture Maps"),
    ("sourcecoop_radio", "Source Cooperative"),
    ("osm_radio", "OpenStreetMap"),
    ("custom_radio", "Custom URL"),
])
def test_the_radio_selection_is_saved_by_name(clean_settings, dialogs, radio, name):
    dialog = dialogs()
    getattr(dialog, radio).setChecked(True)
    dialog.save_radio_button_state()
    assert our_keys() == ["plugins/gpq_downloader/radio_selection"]
    assert QgsSettings().value("gpq_downloader/radio_selection", section=QgsSettings.Plugins) == name


def test_no_radio_checked_saves_custom_url(clean_settings, dialogs):
    dialog = dialogs()
    for radio in (dialog.overture_radio, dialog.sourcecoop_radio, dialog.osm_radio, dialog.custom_radio):
        radio.setAutoExclusive(False)
        radio.setChecked(False)
    dialog.save_radio_button_state()
    assert QgsSettings().value("gpq_downloader/radio_selection", section=QgsSettings.Plugins) == "Custom URL"


def run_plugin(iface):
    """plugin.run(), returning the dialog it opened."""
    made = []

    def make(*args):
        made.append(DataSourceDialog(*args))
        return made[-1]

    with patch("gpq_downloader.plugin.DataSourceDialog", side_effect=make):
        QgisPluginGeoParquet(iface).run()
    assert len(made) == 1
    return made[0]


@pytest.mark.parametrize("saved, radio", [
    (None, "overture_radio"),  # nothing saved: Overture
    ("OpenStreetMap", "osm_radio"),
    ("Custom URL", "custom_radio"),
    ("Source Cooperative", "sourcecoop_radio"),
])
def test_plugin_run_restores_the_radio_selection(clean_settings, iface, saved, radio):
    if saved:
        clean_settings.setValue("gpq_downloader/radio_selection", saved, section=QgsSettings.Plugins)
    dialog = run_plugin(iface)
    try:
        checked = [r for r in ("overture_radio", "sourcecoop_radio", "osm_radio", "custom_radio")
                   if getattr(dialog, r).isChecked()]
        assert checked == [radio]
    finally:
        dialog.close()


def test_plugin_run_with_an_unknown_saved_name_checks_nothing(clean_settings, iface):
    """Oddity: a saved name that matches no button (a renamed one, say) leaves
    every radio button unchecked, rather than falling back to Overture."""
    clean_settings.setValue("gpq_downloader/radio_selection", "Overture", section=QgsSettings.Plugins)
    dialog = run_plugin(iface)
    try:
        assert not any(r.isChecked() for r in (
            dialog.overture_radio, dialog.sourcecoop_radio, dialog.osm_radio, dialog.custom_radio))
    finally:
        dialog.close()


def test_accept_saves_the_extent_load_last_extent_cannot_restore(clean_settings, dialogs):
    """Oddities: accept() saves last_used_extent in the Plugins section, but
    load_last_extent() (which nothing calls) reads it without one, so the
    saved extent is never found. Under the key it does read it fails anyway:
    it parses with QgsRectangle.fromString(), which does not exist, and the
    AttributeError is swallowed."""
    dialog = dialogs()
    dialog.current_extent = QgsRectangle(1, 2, 3, 4)
    dialog.accept()
    assert our_keys() == ["plugins/gpq_downloader/last_used_extent"]
    saved = QgsSettings().value("gpq_downloader/last_used_extent", section=QgsSettings.Plugins)
    assert saved == QgsRectangle(1, 2, 3, 4).toString()

    reopened = dialogs()
    reopened.load_last_extent()
    assert reopened.current_extent is None

    assert not hasattr(QgsRectangle, "fromString")
    clean_settings.setValue("gpq_downloader/last_used_extent", saved)
    reopened.load_last_extent()
    assert reopened.current_extent is None


def test_accept_without_an_extent_saves_nothing(clean_settings, dialogs):
    dialogs().accept()
    assert our_keys() == []

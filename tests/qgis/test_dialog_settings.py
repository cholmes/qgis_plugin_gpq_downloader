"""Characterization of what the dialog remembers between sessions.

The dialog and plugin keep their state in QgsSettings under the Plugins
section, which QSettings stores as plugins/gpq_downloader/<name>. Splitting the
dialog moves all of this, so the keys, their values and the round trip back
into a new dialog are pinned here, oddities included.

Each test starts with no gpq_downloader settings and puts the user's own back
afterwards.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from qgis.core import QgsRectangle, QgsSettings

from gpq_downloader.dialog import DataSourceDialog
from gpq_downloader.gui import settings_store, user_sources_store
from gpq_downloader.plugin import QgisPluginGeoParquet

PREFIXES = ("plugins/gpq_downloader/", "gpq_downloader/")

OVERTURE_KEYS = ["buildings", "places", "transportation", "addresses", "divisions", "base"]
# Building Parts modifies the Buildings download rather than being a preset of
# its own, but keeps the top-level key it was saved under while it was a
# top-level checkbox (#80), so moving it loses nobody's choice
BUILDINGS_MODIFIERS = ["building_parts"]
BASE_SUBTYPES = ["infrastructure", "land", "land_cover", "land_use", "water", "bathymetry"]
DIVISIONS_SUBTYPES = ["division", "division_area", "division_boundary"]
OSM_KEYS = [
    "addresses", "buildings", "boundaries", "highways", "parks", "pois", "settlements", "waterways", "wetlands",
]

# Everything saved as a true/false
FLAG_KEYS = sorted(
    ["plugins/gpq_downloader/clip_to_area", "plugins/gpq_downloader/aoi_enabled"]
    + [f"plugins/gpq_downloader/checkbox_{k}" for k in OVERTURE_KEYS + BUILDINGS_MODIFIERS]
    + [f"plugins/gpq_downloader/base_subtype_checkbox_{k}" for k in BASE_SUBTYPES]
    + [f"plugins/gpq_downloader/divisions_subtype_checkbox_{k}" for k in DIVISIONS_SUBTYPES]
    + [f"plugins/gpq_downloader/osm_checkbox_{k}" for k in OSM_KEYS]
)
# The filter condition is text, not a flag; the tick that applies it is
# deliberately not saved at all (#231).
FILTER_TEXT_KEY = "plugins/gpq_downloader/attribute_filter"
ALL_SAVED_KEYS = sorted(FLAG_KEYS + [FILTER_TEXT_KEY])

# The checkboxes that start out ticked, so a new user downloads the whole of
# Buildings and of Divisions when they tick either parent
ON_BY_DEFAULT = {f"checkbox_{k}" for k in BUILDINGS_MODIFIERS} | {
    f"divisions_subtype_checkbox_{k}" for k in DIVISIONS_SUBTYPES
}


def our_keys():
    return sorted(k for k in QgsSettings().allKeys() if k.startswith(PREFIXES))


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
    # Of the flags, only the on-by-default ones start out true (#230). The
    # filter is text rather than a flag, so it is not read with type=bool.
    assert {k: flag(k) for k in FLAG_KEYS} == {
        k: k.split("/")[-1] in ON_BY_DEFAULT for k in FLAG_KEYS
    }
    assert QgsSettings().value(FILTER_TEXT_KEY) == ""
    assert not any("filter" in k for k in FLAG_KEYS)  # no remembered tick (#231)


def test_toggling_a_checkbox_saves_every_state(clean_settings, dialogs):
    dialog = dialogs()
    dialog.osm_checkboxes["pois"].setChecked(True)
    # The toggled signal saves all checkboxes, not only the one toggled...
    assert flag("plugins/gpq_downloader/osm_checkbox_pois") is True
    assert flag("plugins/gpq_downloader/checkbox_buildings") is False
    # ...including clip_to_area and aoi_enabled
    assert our_keys() == ALL_SAVED_KEYS


def test_building_parts_is_on_by_default_and_remembers_being_turned_off(clean_settings, dialogs):
    """Nested under Buildings and on unless the user says otherwise. Nothing is
    stored for a user who has never touched it, so the key is read with True as
    its default - and a stored False still wins, or unticking it would not last.
    """
    first = dialogs()
    assert first.buildings_modifier_checkboxes["building_parts"].isChecked()
    assert flag("plugins/gpq_downloader/checkbox_buildings") is False

    first.buildings_modifier_checkboxes["building_parts"].setChecked(False)
    first.save_checkbox_states()
    assert flag("plugins/gpq_downloader/checkbox_building_parts") is False

    second = dialogs()
    assert not second.buildings_modifier_checkboxes["building_parts"].isChecked()


@pytest.mark.parametrize("stored", [True, False])
def test_building_parts_keeps_what_was_saved_under_its_old_top_level_key(
    clean_settings, dialogs, stored
):
    """It was a top-level checkbox (#80) saved under checkbox_building_parts.
    The nested checkbox reads that same key, so an upgrading user keeps their
    choice either way round."""
    clean_settings.setValue(
        "gpq_downloader/checkbox_building_parts", stored, section=QgsSettings.Section.Plugins
    )
    assert dialogs().buildings_modifier_checkboxes["building_parts"].isChecked() is stored


def test_load_flag_defaults_only_when_nothing_is_stored(clean_settings):
    """A checkbox that defaults on needs the three states kept apart: never
    saved is not the same as saved False, or unticking it would not last."""
    key = "gpq_downloader/checkbox_building_parts"
    assert settings_store.load_flag(key) is False
    assert settings_store.load_flag(key, default=True) is True

    clean_settings.setValue(key, False, section=QgsSettings.Section.Plugins)
    assert settings_store.load_flag(key, default=True) is False

    clean_settings.setValue(key, True, section=QgsSettings.Section.Plugins)
    assert settings_store.load_flag(key, default=False) is True


def test_the_divisions_subtypes_are_on_by_default(clean_settings, dialogs):
    """All three default on; unticking one of them lasts."""
    first = dialogs()
    assert {k for k, c in first.divisions_subtype_checkboxes.items() if c.isChecked()} == set(
        DIVISIONS_SUBTYPES
    )

    first.divisions_subtype_checkboxes["division_area"].setChecked(False)
    first.save_checkbox_states()

    second = dialogs()
    assert {k for k, c in second.divisions_subtype_checkboxes.items() if c.isChecked()} == {
        "division", "division_boundary",
    }


def test_the_values_saved(clean_settings, dialogs):
    dialog = dialogs()
    checked = {
        "checkbox_places", "checkbox_base", "base_subtype_checkbox_land_use", "base_subtype_checkbox_water",
        "divisions_subtype_checkbox_division_area", "osm_checkbox_pois", "osm_checkbox_wetlands",
        "clip_to_area", "aoi_enabled",
        *ON_BY_DEFAULT,  # left alone here
    }
    dialog.overture_checkboxes["places"].setChecked(True)
    dialog.base_checkbox.setChecked(True)
    dialog.base_subtype_checkboxes["land_use"].setChecked(True)
    dialog.base_subtype_checkboxes["water"].setChecked(True)
    dialog.divisions_subtype_checkboxes["division_area"].setChecked(True)  # already on
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
    first.divisions_subtype_checkboxes["division"].setChecked(False)  # on by default
    first.divisions_subtype_checkboxes["division_area"].setChecked(False)
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
    # The Buildings modifier follows Buildings, which is off
    assert second.buildings_modifier_checkboxes["building_parts"].isHidden()


def test_clip_is_restored_from_its_own_key(clean_settings, dialogs):
    clean_settings.setValue("gpq_downloader/clip_to_area", True, section=QgsSettings.Plugins)
    assert dialogs().clip_checkbox.isChecked()
    clean_settings.setValue("gpq_downloader/clip_to_area", False, section=QgsSettings.Plugins)
    assert not dialogs().clip_checkbox.isChecked()


@pytest.mark.parametrize("radio, name", [
    ("overture_radio", "Overture Maps"),
    ("osm_radio", "OpenStreetMap"),
    ("custom_radio", "Custom URL"),
])
def test_the_radio_selection_is_saved_by_name(clean_settings, dialogs, radio, name):
    dialog = dialogs()
    getattr(dialog, radio).setChecked(True)
    dialog.save_radio_button_state()
    assert our_keys() == ["plugins/gpq_downloader/radio_selection"]
    assert QgsSettings().value("gpq_downloader/radio_selection", section=QgsSettings.Plugins) == name


def test_the_seeded_sourcecoop_radio_selection_is_saved_by_name(clean_settings, dialogs):
    """The seeded "Source Cooperative" custom group's radio is saved by text
    exactly like any other custom source's - there is no sourcecoop_radio
    attribute any more (#84 follow-up)."""
    os.remove(user_sources_store.user_config_path())  # missing file -> seeded
    dialog = dialogs()
    dialog.custom_source_radios["Source Cooperative"].setChecked(True)
    dialog.save_radio_button_state()
    assert our_keys() == ["plugins/gpq_downloader/radio_selection"]
    assert QgsSettings().value("gpq_downloader/radio_selection", section=QgsSettings.Plugins) == "Source Cooperative"


def test_no_radio_checked_saves_custom_url(clean_settings, dialogs):
    dialog = dialogs()
    # The built-in radios live in an exclusive QButtonGroup, which refuses to
    # leave zero buttons checked - setAutoExclusive(False) is a per-button
    # flag and does not override that, so the group itself must be made
    # non-exclusive before setChecked(False) can actually clear the one
    # the dialog defaulted to.
    dialog.source_button_group.setExclusive(False)
    for radio in (dialog.overture_radio, dialog.osm_radio, dialog.custom_radio):
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
])
def test_plugin_run_restores_the_radio_selection(clean_settings, iface, saved, radio):
    if saved:
        clean_settings.setValue("gpq_downloader/radio_selection", saved, section=QgsSettings.Plugins)
    dialog = run_plugin(iface)
    try:
        checked = [r for r in ("overture_radio", "osm_radio", "custom_radio")
                   if getattr(dialog, r).isChecked()]
        assert checked == [radio]
    finally:
        dialog.close()


def test_plugin_run_restores_the_seeded_sourcecoop_radio_selection(clean_settings, iface):
    """The saved name "Source Cooperative" matches the seeded custom
    source's radio by text - free backward compatibility with a session
    saved before Source Cooperative stopped being a hardcoded built-in."""
    os.remove(user_sources_store.user_config_path())  # missing file -> seeded
    clean_settings.setValue("gpq_downloader/radio_selection", "Source Cooperative", section=QgsSettings.Plugins)
    dialog = run_plugin(iface)
    try:
        assert dialog.custom_source_radios["Source Cooperative"].isChecked()
        for radio in (dialog.overture_radio, dialog.osm_radio, dialog.custom_radio):
            assert not radio.isChecked()
    finally:
        dialog.close()


def test_plugin_run_with_an_unknown_saved_name_falls_back_to_custom_url(clean_settings, iface):
    """A saved name that matches no visible radio (a renamed or hidden
    source, say) falls back to Custom URL, per the dialog's fallback rule:
    after restore, if no visible radio ended up checked, Custom URL is."""
    clean_settings.setValue("gpq_downloader/radio_selection", "Overture", section=QgsSettings.Plugins)
    dialog = run_plugin(iface)
    try:
        checked = [r for r in ("overture_radio", "osm_radio", "custom_radio")
                   if getattr(dialog, r).isChecked()]
        assert checked == ["custom_radio"]
    finally:
        dialog.close()


def test_refresh_dialog_sources_reopens_with_the_new_config(clean_settings, iface):
    """Applying the Options page's Data Sources tab while the downloader is
    open must show the new sources without the user closing and reopening it
    themselves (#84): the dialog reads the config once, at construction, so
    the refresh is a close-and-reopen in place."""
    import gpq_downloader.gui.user_sources_store as user_sources_store

    made = []

    def make(*args):
        made.append(DataSourceDialog(*args))
        return made[-1]

    with patch("gpq_downloader.plugin.DataSourceDialog", side_effect=make):
        plugin = QgisPluginGeoParquet(iface)
        plugin.run()
        first = made[0]
        assert first.isVisible()

        config = user_sources_store.load_user_config()
        config["hidden_sources"] = ["openstreetmap"]
        user_sources_store.save_user_config(config)
        plugin.refresh_dialog_sources()

    assert len(made) == 2
    second = made[1]
    try:
        assert not first.isVisible()
        assert second.isVisible()
        assert second.osm_radio.isHidden()
        assert second.osm_radio not in second.source_radios
    finally:
        second.close()


def test_refresh_dialog_sources_without_an_open_dialog_does_nothing(clean_settings, iface):
    with patch("gpq_downloader.plugin.DataSourceDialog") as ctor:
        QgisPluginGeoParquet(iface).refresh_dialog_sources()
    ctor.assert_not_called()


def test_plugin_run_does_not_resurrect_a_hidden_radio_via_the_saved_name(clean_settings, iface):
    """run() must not re-check a radio by name after the dialog's own
    restore: a hidden radio is still built and still in the button group,
    so matching the saved name against the four built-in attributes (rather
    than against dialog.source_radios, which omits hidden ones) would check
    it anyway, landing the stack on the hidden source's page and letting
    get_urls() return its URL even though it is invisible (CRITICAL 1)."""
    import gpq_downloader.gui.user_sources_store as user_sources_store

    config = user_sources_store.load_user_config()
    config["hidden_sources"] = ["openstreetmap"]
    user_sources_store.save_user_config(config)
    clean_settings.setValue("gpq_downloader/radio_selection", "OpenStreetMap", section=QgsSettings.Plugins)

    dialog = run_plugin(iface)
    try:
        assert dialog.custom_radio.isChecked()
        assert not dialog.osm_radio.isChecked()
        assert dialog.stack.currentIndex() == 0
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


def test_a_checked_custom_source_dataset_carries_its_stored_validation(
    clean_settings, iface, tmp_path
):
    """A download of a dataset the Data Sources manager already probed
    (Task B / #84 follow-up) carries that stored result into its
    DownloadJob as a hint instead of the {} every job got before; the
    Worker still re-checks it per download, this just lets it skip
    rediscovering what is already known."""
    config = user_sources_store.load_user_config()
    config["custom_sources"] = [
        {
            "name": "My Co",
            "info_url": "",
            "datasets": [
                {
                    "name": "Parcels",
                    "url": "https://example.com/parcels.parquet",
                    "info_url": "",
                    "group": "",
                    "validation": {
                        "has_bbox": True,
                        "bbox_column": "bbox",
                        "geometry_column": "geometry",
                    },
                }
            ],
        }
    ]
    user_sources_store.save_user_config(config)

    plugin = QgisPluginGeoParquet(iface)
    dialog = DataSourceDialog(None, iface)
    dialog.custom_source_radios["My Co"].setChecked(True)
    dialog.custom_checkboxes["My Co"]["Parcels"].setChecked(True)

    try:
        with patch(
            "gpq_downloader.plugin.QFileDialog.getSaveFileName",
            side_effect=lambda *a, **k: (
                str(tmp_path / "out.parquet"), "GeoParquet (*.parquet)",
            ),
        ):
            with patch.object(plugin.downloads, "enqueue", return_value=0) as enqueue:
                plugin.handle_dialog_accepted(dialog)

        [job] = enqueue.call_args[0][0]
        assert job.validation_results == {
            "has_bbox": True,
            "bbox_column": "bbox",
            "geometry_column": "geometry",
        }
    finally:
        dialog.close()

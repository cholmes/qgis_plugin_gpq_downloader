"""The dialog honors hidden built-in sources and datasets (#84).

Hiding a source or a dataset is the user's own config
(user_sources_store.load_user_config()/core.user_sources.apply_hidden()),
read once at dialog construction. These tests pin what the dialog does with
that config: a hidden source's radio disappears rather than its attribute,
a hidden dataset disappears from its page, a remembered selection that no
longer exists falls back to Custom URL, and a corrupt config still opens a
dialog with every built-in showing.
"""

import os
from unittest.mock import MagicMock

import pytest
from qgis.PyQt.QtWidgets import QLabel

from gpq_downloader.core.user_sources import default_config
from gpq_downloader.dialog import DataSourceDialog
from gpq_downloader.gui import user_sources_store


@pytest.fixture
def iface():
    """A bare MagicMock is enough: DataSourceDialog only calls methods on it,
    and every call on a MagicMock answers with another MagicMock (see
    test_dialog_settings.py's identical fixture)."""
    iface = MagicMock()
    iface.mainWindow.return_value = None
    return iface


def write_config(**overrides):
    """Save a config built from default_config() with `overrides` applied,
    through user_sources_store.save_user_config() so it is written exactly
    as the real config store would write it (isolated_user_sources in
    conftest.py points this at tmp_path for every test here)."""
    config = default_config()
    config.update(overrides)
    user_sources_store.save_user_config(config)
    return config


def test_hidden_source_radio_is_not_visible(clean_settings, iface):
    write_config(hidden_sources=["openstreetmap"])
    dialog = DataSourceDialog(None, iface)

    assert dialog.osm_radio.isHidden()
    assert dialog.osm_radio not in dialog.source_radios
    # A hidden radio keeps its attribute and its page, so nothing else that
    # already reaches for self.osm_radio breaks (the design's ruling).
    assert not dialog.overture_radio.isHidden()
    assert dialog.overture_radio in dialog.source_radios


def test_hidden_osm_dataset_has_no_checkbox(clean_settings, iface):
    write_config(hidden_datasets={"openstreetmap": ["addresses"]})
    dialog = DataSourceDialog(None, iface)

    assert "addresses" not in dialog.osm_checkboxes
    assert "addresses" not in dialog.PRESET_DATASETS["openstreetmap"]
    assert "buildings" in dialog.osm_checkboxes


def test_hidden_overture_dataset_has_no_checkbox(clean_settings, iface):
    write_config(hidden_datasets={"overture": ["places"]})
    dialog = DataSourceDialog(None, iface)

    assert "places" not in dialog.overture_checkboxes
    assert "places" not in dialog.PRESET_DATASETS["overture"]
    assert "transportation" in dialog.overture_checkboxes


def test_hidden_overture_buildings_takes_its_modifiers_along(clean_settings, iface):
    write_config(hidden_datasets={"overture": ["buildings"]})
    dialog = DataSourceDialog(None, iface)

    assert "buildings" not in dialog.overture_checkboxes
    # The modifier (building_parts) is only reachable through Buildings, so
    # it goes too, even though it was not hidden itself.
    assert dialog.buildings_modifier_checkboxes == {}
    # The attribute survives, hidden and unchecked, so code that touches it
    # unconditionally (load_checkbox_state's visibility updates) still works.
    assert dialog.buildings_checkbox.isHidden()
    assert not dialog.buildings_checkbox.isChecked()


def test_hidden_overture_building_parts_drops_only_the_modifier(clean_settings, iface):
    write_config(hidden_datasets={"overture": ["building_parts"]})
    dialog = DataSourceDialog(None, iface)

    assert "buildings" in dialog.overture_checkboxes
    assert dialog.buildings_modifier_checkboxes == {}


def test_hidden_overture_category_drops_its_subtype_row(clean_settings, iface):
    write_config(hidden_datasets={"overture": ["base"]})
    dialog = DataSourceDialog(None, iface)

    assert "base" not in dialog.overture_checkboxes
    assert dialog.base_subtype_checkboxes == {}
    assert dialog.base_checkbox.isHidden()
    assert not dialog.base_subtype_widget.isVisible()
    # Divisions is untouched.
    assert "divisions" in dialog.overture_checkboxes
    assert dialog.divisions_subtype_checkboxes


def test_hidden_overture_dataset_cannot_reach_get_urls(clean_settings, iface):
    # Session 1: Places was ticked and remembered.
    dialog = DataSourceDialog(None, iface)
    dialog.overture_checkboxes["places"].setChecked(True)
    dialog.save_checkbox_states()

    # Session 2: Places is hidden; the saved tick must not resurrect it.
    write_config(hidden_datasets={"overture": ["places"]})
    dialog = DataSourceDialog(None, iface)
    dialog.load_checkbox_states()
    assert all(
        not checkbox.isChecked() for checkbox in dialog.overture_checkboxes.values()
    )


def test_saved_radio_for_hidden_source_falls_back(clean_settings, iface):
    # Session 1: OpenStreetMap was the remembered selection.
    first = DataSourceDialog(None, iface)
    first.osm_radio.setChecked(True)
    first.save_radio_button_state()
    first.close()

    # Between sessions, OpenStreetMap gets hidden.
    write_config(hidden_sources=["openstreetmap"])

    # Session 2 ("reopen"): the saved name ("OpenStreetMap") matches nothing
    # in source_radios any more, so Custom URL is checked instead of nothing.
    second = DataSourceDialog(None, iface)
    assert second.custom_radio.isChecked()
    second.close()


def test_all_builtins_hidden_leaves_custom_url(clean_settings, iface):
    write_config(hidden_sources=["overture", "openstreetmap"])
    dialog = DataSourceDialog(None, iface)

    assert dialog.source_radios == [dialog.custom_radio]
    assert dialog.custom_radio.isChecked()
    for radio in (dialog.overture_radio, dialog.osm_radio):
        assert radio.isHidden()


def test_dialog_opens_with_garbage_config(clean_settings, iface):
    path = user_sources_store.user_config_path()
    with open(path, "wb") as f:
        f.write(b"\xff\xfe not even close to JSON")

    dialog = DataSourceDialog(None, iface)

    for radio in (
        dialog.overture_radio,
        dialog.osm_radio,
        dialog.custom_radio,
    ):
        assert not radio.isHidden()
    assert dialog.source_radios == [
        dialog.overture_radio,
        dialog.osm_radio,
        dialog.custom_radio,
    ]


def test_default_config_changes_nothing(clean_settings, iface):
    dialog = DataSourceDialog(None, iface)

    for radio in (
        dialog.overture_radio,
        dialog.osm_radio,
        dialog.custom_radio,
    ):
        assert not radio.isHidden()
    # No custom source at all, seeded or otherwise: the autouse fixture
    # already wrote an explicit, empty default_config() to this test's
    # config file, so load_user_config() never takes the missing-file
    # (seeding) branch.
    assert dialog.source_radios == [
        dialog.overture_radio,
        dialog.osm_radio,
        dialog.custom_radio,
    ]


def test_missing_config_seeds_source_cooperative_radio_and_page(clean_settings, iface):
    """A first run (no user_sources.json at all) seeds the "Source
    Cooperative" custom group (gui/user_sources_store.load_user_config()),
    which flows through the same custom-source machinery as a group the
    user built themselves - no SC-specific code anywhere in the dialog."""
    os.remove(user_sources_store.user_config_path())

    dialog = DataSourceDialog(None, iface)

    names = [radio.text() for radio in dialog.source_radios]
    assert "Source Cooperative" in names
    checkboxes = dialog.custom_checkboxes["Source Cooperative"]
    assert "VIDA Google/Microsoft/OSM Buildings" in checkboxes
    dialog.close()


# --- Task 4: custom source pages ---------------------------------------


def _custom_source(**overrides):
    source = {
        "name": "My Company Data",
        "info_url": "",
        "datasets": [
            {
                "name": "Parcels",
                "url": "https://example.com/parcels.parquet",
                "info_url": "",
                "group": "",
            },
        ],
    }
    source.update(overrides)
    return source


def test_custom_source_gets_radio_and_checkboxes(clean_settings, iface):
    write_config(custom_sources=[_custom_source()])
    dialog = DataSourceDialog(None, iface)

    names = [radio.text() for radio in dialog.source_radios]
    assert "My Company Data" in names
    # Custom URL stays last, after every custom source (the Design's rule).
    assert names[-1] == "Custom URL"
    assert "Parcels" in dialog.custom_checkboxes["My Company Data"]
    dialog.close()


def test_custom_source_get_urls_returns_checked_urls(clean_settings, iface):
    write_config(
        custom_sources=[
            _custom_source(
                datasets=[
                    {
                        "name": "Parcels",
                        "url": "https://example.com/parcels.parquet",
                        "info_url": "",
                        "group": "",
                    },
                    {
                        "name": "Roads",
                        "url": "https://example.com/roads.parquet",
                        "info_url": "",
                        "group": "",
                    },
                    {
                        "name": "Rivers",
                        "url": "https://example.com/rivers.parquet",
                        "info_url": "",
                        "group": "",
                    },
                ]
            )
        ]
    )
    dialog = DataSourceDialog(None, iface)
    radio = next(r for r in dialog.source_radios if r.text() == "My Company Data")
    radio.setChecked(True)

    checkboxes = dialog.custom_checkboxes["My Company Data"]
    checkboxes["Parcels"].setChecked(True)
    checkboxes["Rivers"].setChecked(True)
    checkboxes["Roads"].setChecked(False)

    urls = dialog.get_urls()
    assert set(urls) == {
        "https://example.com/parcels.parquet",
        "https://example.com/rivers.parquet",
    }
    dialog.close()


def test_custom_checkbox_state_remembered(clean_settings, iface):
    write_config(custom_sources=[_custom_source()])

    first = DataSourceDialog(None, iface)
    first.custom_checkboxes["My Company Data"]["Parcels"].setChecked(True)
    first.save_checkbox_states()
    first.close()

    second = DataSourceDialog(None, iface)
    assert second.custom_checkboxes["My Company Data"]["Parcels"].isChecked()
    second.close()


def test_group_headers_render_in_group_order(clean_settings, iface):
    write_config(
        custom_sources=[
            _custom_source(
                datasets=[
                    {
                        "name": "Parcels",
                        "url": "https://example.com/parcels.parquet",
                        "info_url": "",
                        "group": "Boundaries",
                    },
                    {
                        "name": "Roads",
                        "url": "https://example.com/roads.parquet",
                        "info_url": "",
                        "group": "Transport",
                    },
                    {
                        "name": "Lakes",
                        "url": "https://example.com/lakes.parquet",
                        "info_url": "",
                        "group": "Boundaries",
                    },
                ]
            )
        ]
    )
    dialog = DataSourceDialog(None, iface)
    radio = next(r for r in dialog.source_radios if r.text() == "My Company Data")
    radio.setChecked(True)

    page = dialog.stack.currentWidget()
    labels = [label.text() for label in page.findChildren(QLabel)]
    assert "<b>Boundaries</b>" in labels
    assert "<b>Transport</b>" in labels
    assert labels.index("<b>Boundaries</b>") < labels.index("<b>Transport</b>")
    dialog.close()


def test_dataset_tooltip_carries_url(clean_settings, iface):
    write_config(
        custom_sources=[
            _custom_source(
                datasets=[
                    {
                        "name": "Parcels",
                        "url": "https://example.com/parcels.parquet",
                        "info_url": "https://example.com/parcels",
                        "group": "",
                    },
                ]
            )
        ]
    )
    dialog = DataSourceDialog(None, iface)
    checkbox = dialog.custom_checkboxes["My Company Data"]["Parcels"]
    assert "https://example.com/parcels.parquet" in checkbox.toolTip()
    assert "https://example.com/parcels" in checkbox.toolTip()
    dialog.close()


def test_empty_custom_source_gets_no_radio(clean_settings, iface):
    write_config(
        custom_sources=[{"name": "Empty Source", "info_url": "", "datasets": []}]
    )
    dialog = DataSourceDialog(None, iface)

    names = [radio.text() for radio in dialog.source_radios]
    assert "Empty Source" not in names
    assert "Empty Source" not in dialog.custom_checkboxes
    dialog.close()


# --- Task B / #84 follow-up: stored probe results as download hints -------


def test_validation_hints_for_a_validated_dataset(clean_settings, iface):
    write_config(
        custom_sources=[
            _custom_source(
                datasets=[
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
                    },
                    {
                        "name": "Roads",
                        "url": "https://example.com/roads.parquet",
                        "info_url": "",
                        "group": "",
                    },
                ]
            )
        ]
    )
    dialog = DataSourceDialog(None, iface)

    assert dialog.validation_hints_for("https://example.com/parcels.parquet") == {
        "has_bbox": True,
        "bbox_column": "bbox",
        "geometry_column": "geometry",
    }
    # Unvalidated dataset, and any URL that is not a stored dataset's own
    # (a typed-in Custom URL, say): both get no hints at all.
    assert dialog.validation_hints_for("https://example.com/roads.parquet") == {}
    assert dialog.validation_hints_for("https://example.com/unknown.parquet") == {}
    dialog.close()


def test_custom_dataset_label_for_a_known_url_and_none_for_an_unknown_one(
    clean_settings, iface
):
    write_config(
        custom_sources=[
            _custom_source(
                datasets=[
                    {
                        "name": "Parcels",
                        "url": "https://example.com/parcels.parquet",
                        "info_url": "",
                        "group": "",
                    },
                ]
            )
        ]
    )
    dialog = DataSourceDialog(None, iface)

    assert dialog.custom_dataset_label_for("https://example.com/parcels.parquet") == (
        "My Company Data",
        "Parcels",
    )
    assert dialog.custom_dataset_label_for("https://example.com/unknown.parquet") is None
    dialog.close()


def test_custom_radio_selection_remembered(clean_settings, iface):
    write_config(custom_sources=[_custom_source()])

    first = DataSourceDialog(None, iface)
    radio = next(r for r in first.source_radios if r.text() == "My Company Data")
    radio.setChecked(True)
    first.save_radio_button_state()
    first.close()

    second = DataSourceDialog(None, iface)
    radio2 = next(r for r in second.source_radios if r.text() == "My Company Data")
    assert radio2.isChecked()
    second.close()

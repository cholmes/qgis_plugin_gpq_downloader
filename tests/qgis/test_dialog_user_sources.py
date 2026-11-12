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
from qgis.PyQt.QtWidgets import QComboBox, QLabel

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
    user built themselves - no SC-specific code anywhere in the dialog.

    It is seeded with "style": "dropdown" (user_sources.
    source_cooperative_group()), which is the single-pick page it had
    before #84 - so it lands in custom_dropdowns, not custom_checkboxes."""
    os.remove(user_sources_store.user_config_path())

    dialog = DataSourceDialog(None, iface)

    names = [radio.text() for radio in dialog.source_radios]
    assert "Source Cooperative" in names
    assert "Source Cooperative" not in dialog.custom_checkboxes
    combo = dialog.custom_dropdowns["Source Cooperative"]
    items = [combo.itemText(i) for i in range(combo.count())]
    assert "VIDA Google/Microsoft/OSM Buildings" in items
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


# --- Task D: per-group page style (checkboxes vs drop-down) --------------


def _dropdown_source(**overrides):
    source = _custom_source(
        style="dropdown",
        datasets=[
            {
                "name": "Parcels",
                "url": "https://example.com/parcels.parquet",
                "info_url": "https://example.com/parcels",
                "group": "",
            },
            {
                "name": "Roads",
                "url": "https://example.com/roads.parquet",
                "info_url": "",
                "group": "",
            },
        ],
    )
    source.update(overrides)
    return source


def _combo_items(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def _radio(dialog, name):
    return next(r for r in dialog.source_radios if r.text() == name)


def test_dropdown_source_builds_a_combo_page_and_no_checkboxes(clean_settings, iface):
    write_config(custom_sources=[_dropdown_source()])
    dialog = DataSourceDialog(None, iface)

    assert "My Company Data" not in dialog.custom_checkboxes
    combo = dialog.custom_dropdowns["My Company Data"]
    assert _combo_items(combo) == ["Parcels", "Roads"]
    # The combo is on the page the source's own radio shows.
    _radio(dialog, "My Company Data").setChecked(True)
    assert combo in dialog.stack.currentWidget().findChildren(QComboBox)
    dialog.close()


def test_checkbox_source_has_no_dropdown_entry(clean_settings, iface):
    """A source appears in exactly one of the two registries, and an
    explicit "checkboxes" style renders exactly as it always has."""
    write_config(custom_sources=[_custom_source(style="checkboxes")])
    dialog = DataSourceDialog(None, iface)

    assert "My Company Data" not in dialog.custom_dropdowns
    assert "Parcels" in dialog.custom_checkboxes["My Company Data"]
    dialog.close()


def test_config_without_a_style_still_renders_checkboxes(clean_settings, iface):
    write_config(custom_sources=[_custom_source()])
    dialog = DataSourceDialog(None, iface)

    assert dialog.custom_dropdowns == {}
    assert "Parcels" in dialog.custom_checkboxes["My Company Data"]
    dialog.close()


def test_dropdown_get_urls_returns_only_the_selected_dataset(clean_settings, iface):
    write_config(custom_sources=[_dropdown_source()])
    dialog = DataSourceDialog(None, iface)
    _radio(dialog, "My Company Data").setChecked(True)

    combo = dialog.custom_dropdowns["My Company Data"]
    combo.setCurrentIndex(_combo_items(combo).index("Roads"))
    assert dialog.get_urls() == ["https://example.com/roads.parquet"]

    combo.setCurrentIndex(_combo_items(combo).index("Parcels"))
    assert dialog.get_urls() == ["https://example.com/parcels.parquet"]
    dialog.close()


def test_dropdown_selection_is_remembered_across_dialogs(clean_settings, iface):
    write_config(custom_sources=[_dropdown_source()])

    first = DataSourceDialog(None, iface)
    combo = first.custom_dropdowns["My Company Data"]
    combo.setCurrentIndex(_combo_items(combo).index("Roads"))
    first.save_checkbox_states()
    first.close()

    second = DataSourceDialog(None, iface)
    assert second.custom_dropdowns["My Company Data"].currentText() == "Roads"
    second.close()


def test_a_saved_selection_that_no_longer_exists_is_ignored(clean_settings, iface):
    write_config(custom_sources=[_dropdown_source()])
    first = DataSourceDialog(None, iface)
    combo = first.custom_dropdowns["My Company Data"]
    combo.setCurrentIndex(_combo_items(combo).index("Roads"))
    first.save_checkbox_states()
    first.close()

    # Roads is gone from the config; the combo opens on what is left.
    write_config(
        custom_sources=[
            _dropdown_source(
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
    second = DataSourceDialog(None, iface)
    assert second.custom_dropdowns["My Company Data"].currentText() == "Parcels"
    second.close()


def test_dropdown_info_link_follows_the_selection(clean_settings, iface):
    write_config(custom_sources=[_dropdown_source()])
    dialog = DataSourceDialog(None, iface)
    combo = dialog.custom_dropdowns["My Company Data"]
    link = dialog.custom_dropdown_links["My Company Data"]

    combo.setCurrentIndex(_combo_items(combo).index("Parcels"))
    assert "https://example.com/parcels" in link.text()
    assert link.openExternalLinks()

    # Roads has no info_url of its own, so there is nothing to link to.
    combo.setCurrentIndex(_combo_items(combo).index("Roads"))
    assert link.text() == ""
    dialog.close()


def test_dropdown_page_keeps_the_groups_own_info_link(clean_settings, iface):
    write_config(
        custom_sources=[_dropdown_source(info_url="https://example.com/catalog")]
    )
    dialog = DataSourceDialog(None, iface)
    _radio(dialog, "My Company Data").setChecked(True)

    texts = [
        label.text() for label in dialog.stack.currentWidget().findChildren(QLabel)
    ]
    assert any("https://example.com/catalog" in text for text in texts)
    dialog.close()


def test_changing_the_dropdown_reports_the_new_selection(clean_settings, iface):
    """currentTextChanged has to drive _selection_changed the way a tick
    does, or the OK button would still be accounting for the old pick."""
    write_config(custom_sources=[_dropdown_source()])
    dialog = DataSourceDialog(None, iface)
    _radio(dialog, "My Company Data").setChecked(True)

    combo = dialog.custom_dropdowns["My Company Data"]
    combo.setCurrentIndex(_combo_items(combo).index("Roads"))
    assert dialog.selected_datasets() == ["https://example.com/roads.parquet"]
    dialog.close()


def test_checkbox_page_links_exactly_the_datasets_with_an_info_url(
    clean_settings, iface
):
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

    links = dialog.custom_dataset_links["My Company Data"]
    assert set(links) == {"Parcels"}
    assert 'href="https://example.com/parcels"' in links["Parcels"].text()
    assert links["Parcels"].openExternalLinks()
    assert links["Parcels"].toolTip() == "https://example.com/parcels"
    dialog.close()


def test_wrapping_a_checkbox_in_its_info_row_changes_nothing_else(
    clean_settings, iface
):
    """The info anchor sits in a QWidget with its checkbox, so the checkbox
    is no longer a direct child of the flow - the registry, the saved state
    and get_urls must not notice."""
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
    first = DataSourceDialog(None, iface)
    _radio(first, "My Company Data").setChecked(True)
    first.custom_checkboxes["My Company Data"]["Parcels"].setChecked(True)
    assert first.get_urls() == ["https://example.com/parcels.parquet"]
    first.save_checkbox_states()
    first.close()

    second = DataSourceDialog(None, iface)
    assert second.custom_checkboxes["My Company Data"]["Parcels"].isChecked()
    second.close()

"""The Data Sources manager widget: tree rules, mutation methods, buttons.

Constructed directly with a config dict and a presets dict (no QGIS profile,
no disk) - SourcesManagerWidget never touches user_sources_store itself; that
is SettingsPage's job (Task 6). Button-handler tests patch the QMessageBox
statics so no modal exec loop runs under pytest, matching the pattern in
tests/qgis/test_dialog*.py.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox

from gpq_downloader.core import user_sources
from gpq_downloader.gui.sources_page import SourcesManagerWidget

PRESETS = {
    "overture": {
        "buildings": {"url_template": "s3://overture/buildings/*", "info_url": ""},
        "places": {"url_template": "s3://overture/places/*", "info_url": ""},
    },
    "source_cooperative": {
        "vida_buildings": {
            "url": "s3://vida/*",
            "info_url": "https://source.coop/vida",
            "display_name": "VIDA Buildings",
        },
        "other_dataset": {"url": "s3://other/*", "info_url": ""},
    },
    "openstreetmap": {
        "addresses": {"url": "https://osm/addresses.parquet", "info_url": ""},
        "buildings": {"url": "https://osm/buildings.parquet", "info_url": ""},
    },
}


def _config(**overrides):
    config = user_sources.default_config()
    config.update(overrides)
    return config


def _custom_config():
    return _config(
        custom_sources=[
            {
                "name": "My Company",
                "info_url": "https://example.com",
                "datasets": [
                    {
                        "name": "Parcels",
                        "url": "https://example.com/parcels.parquet",
                        "info_url": "",
                        "group": "",
                    }
                ],
            }
        ]
    )


def _top_item(widget, text):
    for i in range(widget.tree.topLevelItemCount()):
        item = widget.tree.topLevelItem(i)
        if item.text(0) == text:
            return item
    raise AssertionError(f"no top-level item named {text!r}")


def _child_item(parent_item, text):
    for i in range(parent_item.childCount()):
        child = parent_item.child(i)
        if child.text(0) == text:
            return child
    raise AssertionError(f"no child item named {text!r}")


def test_tree_lists_builtins_and_customs(qgs_app):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    top_texts = [
        widget.tree.topLevelItem(i).text(0)
        for i in range(widget.tree.topLevelItemCount())
    ]
    assert top_texts == [
        "Overture Maps",
        "Source Cooperative",
        "OpenStreetMap",
        "My Company",
    ]

    sourcecoop = _top_item(widget, "Source Cooperative")
    dataset_texts = [
        sourcecoop.child(i).text(0) for i in range(sourcecoop.childCount())
    ]
    assert "VIDA Buildings" in dataset_texts  # display_name, not the raw key
    assert "other_dataset" in dataset_texts  # no display_name -> falls back to the key

    custom = _top_item(widget, "My Company")
    assert custom.child(0).text(0) == "Parcels"


def test_unchecking_builtin_source_adds_to_hidden_sources(qgs_app):
    widget = SourcesManagerWidget(_config(), PRESETS)
    item = _top_item(widget, "OpenStreetMap")
    item.setCheckState(1, Qt.CheckState.Unchecked)
    assert widget.config()["hidden_sources"] == ["openstreetmap"]

    item.setCheckState(1, Qt.CheckState.Checked)
    assert widget.config()["hidden_sources"] == []


def test_unchecking_sourcecoop_child_hides_that_dataset(qgs_app):
    widget = SourcesManagerWidget(_config(), PRESETS)
    sourcecoop = _top_item(widget, "Source Cooperative")
    child = _child_item(sourcecoop, "VIDA Buildings")
    child.setCheckState(1, Qt.CheckState.Unchecked)
    assert widget.config()["hidden_datasets"] == {
        "source_cooperative": ["vida_buildings"]
    }

    child.setCheckState(1, Qt.CheckState.Checked)
    assert widget.config()["hidden_datasets"] == {}


def test_overture_children_are_not_checkable(qgs_app):
    widget = SourcesManagerWidget(_config(), PRESETS)
    overture = _top_item(widget, "Overture Maps")
    child = _child_item(overture, "buildings")
    assert not (child.flags() & Qt.ItemFlag.ItemIsUserCheckable)


def test_add_source_then_dataset_lands_in_config(qgs_app):
    widget = SourcesManagerWidget(_config(), PRESETS)
    assert widget._add_source({"name": "New Co", "info_url": "https://new.co"}) is True
    assert (
        widget._add_dataset(
            "New Co",
            {
                "name": "Roads",
                "url": "https://new.co/roads.parquet",
                "info_url": "",
                "group": "",
            },
        )
        is True
    )

    custom_sources = widget.config()["custom_sources"]
    assert len(custom_sources) == 1
    assert custom_sources[0]["name"] == "New Co"
    assert custom_sources[0]["info_url"] == "https://new.co"
    assert custom_sources[0]["datasets"] == [
        {
            "name": "Roads",
            "url": "https://new.co/roads.parquet",
            "info_url": "",
            "group": "",
        }
    ]


def test_duplicate_dataset_name_rejected(qgs_app):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    before = widget.config()
    ok = widget._add_dataset(
        "My Company",
        {
            "name": "parcels",
            "url": "https://example.com/other.parquet",
            "info_url": "",
            "group": "",
        },
    )
    assert ok is False
    assert widget.config() == before  # case-insensitive clash, state unchanged


def test_duplicate_source_name_rejected(qgs_app):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    before = widget.config()
    ok = widget._add_source({"name": "my company", "info_url": ""})
    assert ok is False
    assert widget.config() == before


def test_edit_dataset_updates_config(qgs_app):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    custom = _top_item(widget, "My Company")
    dataset_item = _child_item(custom, "Parcels")

    ok = widget._apply_edit(
        dataset_item,
        {
            "name": "Parcels 2025",
            "url": "https://example.com/parcels2025.parquet",
            "info_url": "https://example.com/parcels",
            "group": "Boundaries",
        },
    )
    assert ok is True
    datasets = widget.config()["custom_sources"][0]["datasets"]
    assert datasets == [
        {
            "name": "Parcels 2025",
            "url": "https://example.com/parcels2025.parquet",
            "info_url": "https://example.com/parcels",
            "group": "Boundaries",
        }
    ]


def test_remove_source_removes_datasets(qgs_app, monkeypatch):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )

    item = _top_item(widget, "My Company")
    widget.tree.setCurrentItem(item)
    widget.remove_button.click()

    assert widget.config()["custom_sources"] == []


def test_edit_remove_disabled_for_builtin_selection(qgs_app):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    item = _top_item(widget, "Overture Maps")
    widget.tree.setCurrentItem(item)

    assert widget.edit_button.isEnabled() is False
    assert widget.remove_button.isEnabled() is False
    assert widget.add_dataset_button.isEnabled() is False


def test_config_returns_deep_copy_of_initial_when_untouched(qgs_app):
    initial = _custom_config()
    widget = SourcesManagerWidget(initial, PRESETS)

    returned = widget.config()
    returned["custom_sources"][0]["name"] = "Tampered"
    returned["custom_sources"][0]["datasets"].append(
        {"name": "x", "url": "y", "info_url": "", "group": ""}
    )

    assert widget.config()["custom_sources"][0]["name"] == "My Company"
    assert len(widget.config()["custom_sources"][0]["datasets"]) == 1

    # Mutating the dict handed to __init__ afterwards must not reach in either.
    initial["custom_sources"][0]["name"] = "Also Tampered"
    assert widget.config()["custom_sources"][0]["name"] == "My Company"

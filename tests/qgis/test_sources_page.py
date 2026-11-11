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
    # Source Cooperative is no longer a built-in (it is seeded into
    # custom_sources instead, #84 follow-up) - PRESETS keeps a
    # source_cooperative section anyway, to pin that the manager ignores it.
    assert top_texts == [
        "Overture Maps",
        "OpenStreetMap",
        "My Company",
    ]

    custom = _top_item(widget, "My Company")
    assert custom.child(0).text(0) == "Parcels"


def test_builtin_dataset_row_shows_the_dialogs_label_not_the_raw_key(qgs_app):
    """An OSM row with no display_name still reads as a label, matching
    what the dialog itself shows for the same preset (dialog.py:620),
    while unchecking it writes the raw preset key - not the label - into
    hidden_datasets (finding IMPORTANT 3)."""
    widget = SourcesManagerWidget(_config(), PRESETS)
    osm = _top_item(widget, "OpenStreetMap")
    addresses = _child_item(osm, "Addresses")
    assert addresses.text(0) != "addresses"

    addresses.setCheckState(1, Qt.CheckState.Unchecked)
    assert widget.config()["hidden_datasets"]["openstreetmap"] == ["addresses"]


def test_unchecking_builtin_source_adds_to_hidden_sources(qgs_app):
    widget = SourcesManagerWidget(_config(), PRESETS)
    item = _top_item(widget, "OpenStreetMap")
    item.setCheckState(1, Qt.CheckState.Unchecked)
    assert widget.config()["hidden_sources"] == ["openstreetmap"]

    item.setCheckState(1, Qt.CheckState.Checked)
    assert widget.config()["hidden_sources"] == []


def test_unchecking_osm_child_hides_that_dataset_and_rechecking_restores_it(qgs_app):
    widget = SourcesManagerWidget(_config(), PRESETS)
    osm = _top_item(widget, "OpenStreetMap")
    child = _child_item(osm, "Buildings")
    child.setCheckState(1, Qt.CheckState.Unchecked)
    assert widget.config()["hidden_datasets"] == {"openstreetmap": ["buildings"]}

    child.setCheckState(1, Qt.CheckState.Checked)
    assert widget.config()["hidden_datasets"] == {}


def test_overture_children_are_checkable_and_write_hidden_datasets(qgs_app):
    widget = SourcesManagerWidget(_config(), PRESETS)
    overture = _top_item(widget, "Overture Maps")
    child = _child_item(overture, "Buildings")  # key.title(), not the raw key
    assert child.flags() & Qt.ItemFlag.ItemIsUserCheckable

    child.setCheckState(1, Qt.CheckState.Unchecked)
    assert widget.config()["hidden_datasets"] == {"overture": ["buildings"]}

    child.setCheckState(1, Qt.CheckState.Checked)
    assert widget.config()["hidden_datasets"] == {}


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


def test_a_custom_source_cannot_steal_a_builtin_or_custom_url_name(qgs_app):
    """A custom source named "Overture Maps" (say) would match the saved
    radio text the dialog restores against (dialog.py's
    load_radio_button_state(), which matches by text alone), stealing the
    real built-in's restore. Checked case-insensitively, and for both add
    and rename (finding Minor Sweep a).

    "Source Cooperative" is deliberately absent from this list: it is no
    longer a built-in, just the name the seeded custom group happens to
    carry, so it must stay exactly as renameable as any other custom
    source's name (#84 follow-up)."""
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    before = widget.config()

    for reserved in ("Overture Maps", "OPENSTREETMAP", "custom url"):
        assert widget._add_source({"name": reserved, "info_url": ""}) is False
    assert widget.config() == before

    item = _top_item(widget, "My Company")
    assert widget._apply_source_edit(item, {"name": "Overture Maps", "info_url": ""}) is False
    assert widget.config() == before


def test_source_cooperative_is_no_longer_a_reserved_name(qgs_app):
    widget = SourcesManagerWidget(_config(), PRESETS)
    assert widget._add_source({"name": "Source Cooperative", "info_url": ""}) is True
    assert _top_item(widget, "Source Cooperative") is not None


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
            "group": "My Company",  # same group: edit in place
        },
    )
    assert ok is True
    datasets = widget.config()["custom_sources"][0]["datasets"]
    assert datasets == [
        {
            "name": "Parcels 2025",
            "url": "https://example.com/parcels2025.parquet",
            "info_url": "https://example.com/parcels",
            "group": "",  # the stored sub-group field is hand-edit only now
        }
    ]


def test_edit_dataset_with_another_group_moves_it(qgs_app):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    assert widget._add_source({"name": "Other Co", "info_url": ""}) is True
    custom = _top_item(widget, "My Company")
    dataset_item = _child_item(custom, "Parcels")

    ok = widget._apply_edit(
        dataset_item,
        {
            "name": "Parcels",
            "url": "https://example.com/parcels.parquet",
            "info_url": "",
            "group": "Other Co",
        },
    )
    assert ok is True
    by_name = {s["name"]: s for s in widget.config()["custom_sources"]}
    assert by_name["My Company"]["datasets"] == []
    assert [d["name"] for d in by_name["Other Co"]["datasets"]] == ["Parcels"]


def test_edit_dataset_move_rejected_when_target_has_that_name(qgs_app):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    assert widget._add_source({"name": "Other Co", "info_url": ""}) is True
    assert widget._add_dataset(
        "Other Co",
        {"name": "Parcels", "url": "https://other.example.com/p.parquet"},
    )
    custom = _top_item(widget, "My Company")
    dataset_item = _child_item(custom, "Parcels")
    before = widget.config()

    ok = widget._apply_edit(
        dataset_item,
        {
            "name": "Parcels",
            "url": "https://example.com/parcels.parquet",
            "info_url": "",
            "group": "Other Co",
        },
    )
    assert ok is False
    assert widget.config() == before


def test_ensure_group_creates_finds_and_refuses(qgs_app):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    # Existing group, any case: the canonical name back, nothing created.
    assert widget._ensure_group("my company") == "My Company"
    # New name: created on the spot (the Add Dataset flow relies on this).
    assert widget._ensure_group("Fresh Group") == "Fresh Group"
    assert "Fresh Group" in [s["name"] for s in widget.config()["custom_sources"]]
    # Reserved and empty names are refused.
    assert widget._ensure_group("Overture Maps") is None
    assert widget._ensure_group("  ") is None


def test_remove_source_removes_datasets(qgs_app, monkeypatch):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )

    item = _top_item(widget, "My Company")
    widget.tree.setCurrentItem(item)
    widget.remove_button.click()

    assert widget.config()["custom_sources"] == []


def test_remove_selected_on_a_dataset_keeps_its_sibling(qgs_app):
    config = _custom_config()
    config["custom_sources"][0]["datasets"].append(
        {
            "name": "Roads",
            "url": "https://example.com/roads.parquet",
            "info_url": "",
            "group": "",
        }
    )
    widget = SourcesManagerWidget(config, PRESETS)
    custom = _top_item(widget, "My Company")
    widget.tree.setCurrentItem(_child_item(custom, "Parcels"))

    widget._remove_selected()

    datasets = widget.config()["custom_sources"][0]["datasets"]
    assert [d["name"] for d in datasets] == ["Roads"]


def test_apply_source_edit_renames_keeps_datasets_and_rejects_existing_name(qgs_app):
    config = _custom_config()
    config["custom_sources"].append(
        {"name": "Other Co", "info_url": "", "datasets": []}
    )
    widget = SourcesManagerWidget(config, PRESETS)
    item = _top_item(widget, "My Company")

    # Renaming to a name already used by another custom source is rejected.
    assert widget._apply_source_edit(
        item, {"name": "Other Co", "info_url": "https://x"}
    ) is False
    assert [s["name"] for s in widget.config()["custom_sources"]] == [
        "My Company", "Other Co",
    ]

    # A real rename keeps the datasets and applies the new info_url.
    assert widget._apply_source_edit(
        item, {"name": "My Renamed Co", "info_url": "https://new.example.com"}
    ) is True
    renamed = next(
        s for s in widget.config()["custom_sources"] if s["name"] == "My Renamed Co"
    )
    assert renamed["info_url"] == "https://new.example.com"
    assert renamed["datasets"] == [
        {
            "name": "Parcels",
            "url": "https://example.com/parcels.parquet",
            "info_url": "",
            "group": "",
        }
    ]


def test_edit_remove_disabled_for_builtin_selection(qgs_app):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    item = _top_item(widget, "Overture Maps")
    widget.tree.setCurrentItem(item)

    assert widget.edit_button.isEnabled() is False
    assert widget.remove_button.isEnabled() is False
    # Add Dataset stays enabled everywhere: its Group field says where the
    # dataset goes, so no custom selection is needed first.
    assert widget.add_dataset_button.isEnabled() is True


def test_unchecking_a_builtin_source_grays_out_its_children(qgs_app):
    widget = SourcesManagerWidget(_config(), PRESETS)
    osm = _top_item(widget, "OpenStreetMap")
    child = _child_item(osm, "Addresses")
    assert child.flags() & Qt.ItemFlag.ItemIsEnabled

    osm.setCheckState(1, Qt.CheckState.Unchecked)
    assert not (_child_item(osm, "Addresses").flags() & Qt.ItemFlag.ItemIsEnabled)
    # The child keeps its own check state while grayed.
    assert _child_item(osm, "Addresses").checkState(1) == Qt.CheckState.Checked

    osm.setCheckState(1, Qt.CheckState.Checked)
    assert _child_item(osm, "Addresses").flags() & Qt.ItemFlag.ItemIsEnabled


def test_a_source_hidden_in_the_config_starts_with_grayed_children(qgs_app):
    config = _config()
    config["hidden_sources"] = ["openstreetmap"]
    widget = SourcesManagerWidget(config, PRESETS)
    osm = _top_item(widget, "OpenStreetMap")
    assert not (_child_item(osm, "Addresses").flags() & Qt.ItemFlag.ItemIsEnabled)


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


# -- import / export ---------------------------------------------------------


def test_export_then_import_roundtrips(qgs_app, monkeypatch, tmp_path):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    path = str(tmp_path / "sources.json")
    widget._export_to(path)

    other = SourcesManagerWidget(_config(), PRESETS)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    assert other._import_from(path) is True
    assert other.config() == widget.config()


def test_export_to_unwritable_path_warns_and_does_not_crash(
    qgs_app, monkeypatch, tmp_path
):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    # A directory is never a writable file target - open(path, "w") raises
    # IsADirectoryError (an OSError) rather than ever writing anything.
    unwritable = str(tmp_path)

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    assert widget._export_to(unwritable) is False
    assert len(warnings) == 1


def test_import_invalid_file_warns_and_keeps_state(qgs_app, monkeypatch, tmp_path):
    widget = SourcesManagerWidget(_custom_config(), PRESETS)
    before = widget.config()
    path = str(tmp_path / "bad.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("not valid json {")

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    assert widget._import_from(path) is False
    assert widget.config() == before
    assert len(warnings) == 1


def test_import_replaces_after_confirm(qgs_app, monkeypatch, tmp_path):
    widget = SourcesManagerWidget(_config(), PRESETS)
    path = str(tmp_path / "sources.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(user_sources.serialize_config(_custom_config()))

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    assert widget._import_from(path) is True
    assert widget.config()["custom_sources"][0]["name"] == "My Company"


def test_import_cancelled_keeps_state(qgs_app, monkeypatch, tmp_path):
    widget = SourcesManagerWidget(_config(), PRESETS)
    before = widget.config()
    path = str(tmp_path / "sources.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write(user_sources.serialize_config(_custom_config()))

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    assert widget._import_from(path) is False
    assert widget.config() == before

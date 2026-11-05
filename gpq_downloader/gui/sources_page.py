"""The Data Sources manager widget: the Options page's Data Sources tab.

SourcesManagerWidget edits an in-memory, deep-copied user_sources.json config
(core/user_sources.py's shape) against a two-column QTreeWidget listing every
source - the three built-ins read from core/presets.py plus whatever custom
sources the config already carries. Nothing here touches disk: SettingsPage's
apply() is what persists config() through user_sources_store.save_user_config().
_export_to()/_import_from() read and write a file the user picks directly,
but the same disk-persistence boundary applies to them: import only replaces
the in-memory config, it does not save it.

Mutation is split from the modal prompts that drive it (_add_source,
_add_dataset, _apply_edit, _remove_selected) precisely so tests can call
those methods directly, with no QDialog/QMessageBox exec loop to run under
pytest - the pattern the rest of the GUI package's tests already use.
"""

import copy

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gpq_downloader.core import user_sources

# Display names users already know from the main dialog's radio buttons,
# keyed by the config/presets source key.
BUILTIN_SOURCE_LABELS = {
    "overture": "Overture Maps",
    "source_cooperative": "Source Cooperative",
    "openstreetmap": "OpenStreetMap",
}

# Fixed display order for the built-in rows, matching the dialog's radio row.
_BUILTIN_ORDER = ("overture", "source_cooperative", "openstreetmap")

# Item-data roles for what a row refers to, independent of its label text.
_SOURCE_KEY_ROLE = Qt.ItemDataRole.UserRole
_DATASET_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
_IS_CUSTOM_ROLE = Qt.ItemDataRole.UserRole + 2

# The tree's two columns.
_NAME_COLUMN = 0
_VISIBLE_COLUMN = 1

# The file filter shared by the Import/Export file dialogs.
_JSON_FILE_FILTER = "JSON files (*.json)"


class _EditDialog(QDialog):
    """OK/Cancel plumbing shared by DatasetEditDialog and SourceEditDialog:
    an inline error QLabel plus hand-rolled OK handling, so a missing
    required field re-shows the dialog with a message instead of either
    closing on empty input or popping a QMessageBox on top of a modal.

    Subclasses build their own fields in __init__, call _finish(form) once
    the form is ready, and override _validation_error() to say what (if
    anything) is still missing.
    """

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color: red;")
        self.error_label.setVisible(False)

    def _finish(self, form):
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.error_label)
        layout.addWidget(buttons)
        self.setLayout(layout)

    def _validation_error(self):
        """The inline message to show instead of accepting, or None if the
        fields are complete."""
        raise NotImplementedError

    def _on_ok(self):
        error = self._validation_error()
        if error:
            self.error_label.setText(error)
            self.error_label.setVisible(True)
            return
        self.accept()


class DatasetEditDialog(_EditDialog):
    """Add/Edit a dataset: name and url (required), info_url, group."""

    def __init__(self, existing_groups=(), initial=None, parent=None):
        super().__init__("Dataset", parent)
        initial = initial or {}

        self.name_edit = QLineEdit(initial.get("name", ""))
        self.url_edit = QLineEdit(initial.get("url", ""))
        self.info_url_edit = QLineEdit(initial.get("info_url", ""))

        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        groups = sorted(g for g in set(existing_groups) if g)
        self.group_combo.addItems(groups)
        self.group_combo.setEditText(initial.get("group", ""))

        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("URL", self.url_edit)
        form.addRow("Info URL", self.info_url_edit)
        form.addRow("Group", self.group_combo)
        self._finish(form)

    def _validation_error(self):
        if not self.name_edit.text().strip() or not self.url_edit.text().strip():
            return "Name and URL are both required."
        return None

    def values(self):
        """The dataset dict this dialog describes; empty optionals as ""."""
        return {
            "name": self.name_edit.text().strip(),
            "url": self.url_edit.text().strip(),
            "info_url": self.info_url_edit.text().strip(),
            "group": self.group_combo.currentText().strip(),
        }


class SourceEditDialog(_EditDialog):
    """Add/Edit a custom source: name (required), info_url."""

    def __init__(self, initial=None, parent=None):
        super().__init__("Source", parent)
        initial = initial or {}

        self.name_edit = QLineEdit(initial.get("name", ""))
        self.info_url_edit = QLineEdit(initial.get("info_url", ""))

        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Info URL", self.info_url_edit)
        self._finish(form)

    def _validation_error(self):
        if not self.name_edit.text().strip():
            return "Name is required."
        return None

    def values(self):
        """The source dict this dialog describes (no "datasets" key)."""
        return {
            "name": self.name_edit.text().strip(),
            "info_url": self.info_url_edit.text().strip(),
        }


class SourcesManagerWidget(QWidget):
    """One QTreeWidget of every source - built-in and custom - plus buttons
    to add/edit/remove custom content and to tick built-in visibility.

    Built-in rows carry a visibility checkbox: at the source level for all
    three built-ins, and at the dataset level only for the two sources in
    `core.user_sources.DATASET_HIDEABLE_SOURCES` (source_cooperative,
    openstreetmap) - Overture's children render with no checkbox at all,
    information only, since its page is a curated layout where per-dataset
    hiding would be inconsistent. Custom rows never carry a checkbox: custom
    content is edited or removed, not hidden.
    """

    def __init__(self, config, presets, parent=None):
        super().__init__(parent)
        self._config = user_sources.sanitize_config(copy.deepcopy(config))
        self._presets = presets

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Source / Dataset", "Visible"])
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._update_button_state)

        self.new_source_button = QPushButton("New Source…", self)
        self.add_dataset_button = QPushButton("Add Dataset…", self)
        self.edit_button = QPushButton("Edit…", self)
        self.remove_button = QPushButton("Remove", self)
        self.import_button = QPushButton("Import…", self)
        self.export_button = QPushButton("Export…", self)

        self.new_source_button.clicked.connect(self._on_new_source)
        self.add_dataset_button.clicked.connect(self._on_add_dataset)
        self.edit_button.clicked.connect(self._on_edit)
        self.remove_button.clicked.connect(self._on_remove)
        self.import_button.clicked.connect(self._on_import)
        self.export_button.clicked.connect(self._on_export)

        button_row = QHBoxLayout()
        button_row.addWidget(self.new_source_button)
        button_row.addWidget(self.add_dataset_button)
        button_row.addWidget(self.edit_button)
        button_row.addWidget(self.remove_button)
        button_row.addStretch()
        button_row.addWidget(self.import_button)
        button_row.addWidget(self.export_button)

        layout = QVBoxLayout()
        layout.addWidget(self.tree)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._rebuild_tree()

    def config(self):
        """The current in-memory config, sanitized, as a fresh deep copy."""
        return user_sources.sanitize_config(copy.deepcopy(self._config))

    # -- tree construction ------------------------------------------------

    def _rebuild_tree(self):
        """Rebuilds every row from self._config/self._presets from scratch.

        Simpler than patching individual rows in place after every mutation,
        and cheap enough at this scale; signals are blocked while building so
        setCheckState() on a freshly-made item does not re-enter
        _on_item_changed.
        """
        self.tree.blockSignals(True)
        self.tree.clear()
        for source_key in _BUILTIN_ORDER:
            datasets = self._presets.get(source_key)
            if isinstance(datasets, dict):
                self.tree.addTopLevelItem(
                    self._builtin_source_item(source_key, datasets)
                )
        for source in self._config["custom_sources"]:
            self.tree.addTopLevelItem(self._custom_source_item(source))
        self.tree.expandAll()
        self.tree.blockSignals(False)
        # clear()/addTopLevelItem() above drop any prior selection, so the
        # Edit/Remove/Add Dataset buttons must be re-evaluated here too -
        # otherwise they stay enabled with nothing selected (#deferred-review).
        self._update_button_state()

    @staticmethod
    def _set_checkable(item, checkable):
        """QTreeWidgetItem's default flags already include
        ItemIsUserCheckable, so a non-checkable row (Overture's children,
        every custom row) must have it cleared explicitly rather than just
        never OR'd in."""
        flags = item.flags()
        item.setFlags(
            (flags | Qt.ItemFlag.ItemIsUserCheckable)
            if checkable
            else (flags & ~Qt.ItemFlag.ItemIsUserCheckable)
        )

    def _builtin_source_item(self, source_key, datasets):
        item = QTreeWidgetItem([BUILTIN_SOURCE_LABELS.get(source_key, source_key)])
        item.setData(_NAME_COLUMN, _SOURCE_KEY_ROLE, source_key)
        item.setData(_NAME_COLUMN, _IS_CUSTOM_ROLE, False)
        self._set_checkable(item, True)
        hidden = source_key in self._config["hidden_sources"]
        item.setCheckState(
            _VISIBLE_COLUMN,
            Qt.CheckState.Unchecked if hidden else Qt.CheckState.Checked,
        )

        dataset_checkable = source_key in user_sources.DATASET_HIDEABLE_SOURCES
        hidden_datasets = self._config["hidden_datasets"].get(source_key, [])
        for dataset_key, info in datasets.items():
            label = (
                info.get("display_name", dataset_key)
                if isinstance(info, dict)
                else dataset_key
            )
            child = QTreeWidgetItem([label])
            child.setData(_NAME_COLUMN, _SOURCE_KEY_ROLE, source_key)
            child.setData(_NAME_COLUMN, _DATASET_KEY_ROLE, dataset_key)
            child.setData(_NAME_COLUMN, _IS_CUSTOM_ROLE, False)
            self._set_checkable(child, dataset_checkable)
            if dataset_checkable:
                child.setCheckState(
                    _VISIBLE_COLUMN,
                    (
                        Qt.CheckState.Unchecked
                        if dataset_key in hidden_datasets
                        else Qt.CheckState.Checked
                    ),
                )
            item.addChild(child)
        return item

    def _custom_source_item(self, source):
        item = QTreeWidgetItem([source["name"]])
        item.setData(_NAME_COLUMN, _SOURCE_KEY_ROLE, source["name"])
        item.setData(_NAME_COLUMN, _IS_CUSTOM_ROLE, True)
        self._set_checkable(item, False)
        for dataset in source["datasets"]:
            child = QTreeWidgetItem([dataset["name"]])
            child.setData(_NAME_COLUMN, _SOURCE_KEY_ROLE, source["name"])
            child.setData(_NAME_COLUMN, _DATASET_KEY_ROLE, dataset["name"])
            child.setData(_NAME_COLUMN, _IS_CUSTOM_ROLE, True)
            self._set_checkable(child, False)
            item.addChild(child)
        return item

    # -- checkbox handling --------------------------------------------------

    def _on_item_changed(self, item, column):
        """A built-in checkbox's state moved; custom rows have none to toggle."""
        if column != _VISIBLE_COLUMN or item.data(_NAME_COLUMN, _IS_CUSTOM_ROLE):
            return
        source_key = item.data(_NAME_COLUMN, _SOURCE_KEY_ROLE)
        dataset_key = item.data(_NAME_COLUMN, _DATASET_KEY_ROLE)
        checked = item.checkState(_VISIBLE_COLUMN) == Qt.CheckState.Checked

        if dataset_key is None:
            hidden_sources = self._config["hidden_sources"]
            if checked and source_key in hidden_sources:
                hidden_sources.remove(source_key)
            elif not checked and source_key not in hidden_sources:
                hidden_sources.append(source_key)
            return

        hidden_datasets = self._config["hidden_datasets"]
        hidden_for_source = hidden_datasets.setdefault(source_key, [])
        if checked:
            if dataset_key in hidden_for_source:
                hidden_for_source.remove(dataset_key)
            if not hidden_for_source:
                hidden_datasets.pop(source_key, None)
        elif dataset_key not in hidden_for_source:
            hidden_for_source.append(dataset_key)

    # -- selection / button enablement --------------------------------------

    def _selected_custom_item(self):
        """The selected QTreeWidgetItem, if it belongs to a custom source."""
        items = self.tree.selectedItems()
        if not items:
            return None
        item = items[0]
        return item if item.data(_NAME_COLUMN, _IS_CUSTOM_ROLE) else None

    def _selected_custom_source_name(self):
        """The custom source a selected row belongs to (itself, or its parent)."""
        item = self._selected_custom_item()
        if item is None:
            return None
        if item.parent() is None:
            return item.data(_NAME_COLUMN, _SOURCE_KEY_ROLE)
        return item.parent().data(_NAME_COLUMN, _SOURCE_KEY_ROLE)

    def _update_button_state(self):
        enabled = self._selected_custom_item() is not None
        self.add_dataset_button.setEnabled(enabled)
        self.edit_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)

    # -- config lookups ------------------------------------------------------

    def _find_custom_source(self, name):
        for source in self._config["custom_sources"]:
            if source["name"].lower() == str(name).lower():
                return source
        return None

    @staticmethod
    def _existing_groups(source):
        """The set of non-empty group names already used within `source`,
        for pre-populating a dataset dialog's group combo. "" with no
        source at all."""
        return {d.get("group", "") for d in source["datasets"]} if source else set()

    # -- mutation methods (no dialogs, no QMessageBox) ----------------------

    def _add_source(self, values):
        """Appends a new custom source; False, state unchanged, on a
        case-insensitive name clash with an existing custom source."""
        name = values.get("name", "").strip()
        if not name:
            return False
        if self._find_custom_source(name) is not None:
            return False
        self._config["custom_sources"].append(
            {
                "name": name,
                "info_url": values.get("info_url", "").strip(),
                "datasets": [],
            }
        )
        self._rebuild_tree()
        return True

    def _add_dataset(self, source_name, values):
        """Appends a dataset to the named custom source; False, state
        unchanged, on a missing source or a name clash within it."""
        source = self._find_custom_source(source_name)
        if source is None:
            return False
        name = values.get("name", "").strip()
        url = values.get("url", "").strip()
        if not name or not url:
            return False
        if any(d["name"].lower() == name.lower() for d in source["datasets"]):
            return False
        source["datasets"].append(
            {
                "name": name,
                "url": url,
                "info_url": values.get("info_url", "").strip(),
                "group": values.get("group", "").strip(),
            }
        )
        self._rebuild_tree()
        return True

    def _apply_edit(self, item, values):
        """Applies `values` to the custom source/dataset `item` refers to;
        False, state unchanged, on a missing target or a name clash."""
        if item.parent() is None:
            return self._apply_source_edit(item, values)
        return self._apply_dataset_edit(item, values)

    def _apply_source_edit(self, item, values):
        old_name = item.data(_NAME_COLUMN, _SOURCE_KEY_ROLE)
        source = self._find_custom_source(old_name)
        if source is None:
            return False
        name = values.get("name", "").strip()
        if not name:
            return False
        if (
            name.lower() != old_name.lower()
            and self._find_custom_source(name) is not None
        ):
            return False
        source["name"] = name
        source["info_url"] = values.get("info_url", "").strip()
        self._rebuild_tree()
        return True

    def _apply_dataset_edit(self, item, values):
        source_name = item.parent().data(_NAME_COLUMN, _SOURCE_KEY_ROLE)
        old_key = item.data(_NAME_COLUMN, _DATASET_KEY_ROLE)
        source = self._find_custom_source(source_name)
        if source is None:
            return False
        dataset = next((d for d in source["datasets"] if d["name"] == old_key), None)
        if dataset is None:
            return False
        name = values.get("name", "").strip()
        url = values.get("url", "").strip()
        if not name or not url:
            return False
        clashes = any(
            d is not dataset and d["name"].lower() == name.lower()
            for d in source["datasets"]
        )
        if clashes:
            return False
        dataset["name"] = name
        dataset["url"] = url
        dataset["info_url"] = values.get("info_url", "").strip()
        dataset["group"] = values.get("group", "").strip()
        self._rebuild_tree()
        return True

    def _remove_selected(self):
        """Removes the selected custom source or dataset. No confirmation -
        that is the Remove button handler's job (_on_remove)."""
        item = self._selected_custom_item()
        if item is None:
            return
        if item.parent() is None:
            name = item.data(_NAME_COLUMN, _SOURCE_KEY_ROLE)
            self._config["custom_sources"] = [
                s for s in self._config["custom_sources"] if s["name"] != name
            ]
        else:
            source_name = item.parent().data(_NAME_COLUMN, _SOURCE_KEY_ROLE)
            dataset_key = item.data(_NAME_COLUMN, _DATASET_KEY_ROLE)
            source = self._find_custom_source(source_name)
            if source is not None:
                source["datasets"] = [
                    d for d in source["datasets"] if d["name"] != dataset_key
                ]
        self._rebuild_tree()

    # -- button handlers (dialogs and QMessageBox live only here) -----------

    def _on_new_source(self):
        dialog = SourceEditDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not self._add_source(dialog.values()):
            QMessageBox.warning(
                self, "Duplicate source", "A source with that name already exists."
            )

    def _on_add_dataset(self):
        source_name = self._selected_custom_source_name()
        if source_name is None:
            return
        source = self._find_custom_source(source_name)
        dialog = DatasetEditDialog(
            existing_groups=self._existing_groups(source), parent=self
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not self._add_dataset(source_name, dialog.values()):
            QMessageBox.warning(
                self,
                "Duplicate dataset",
                "A dataset with that name already exists for this source.",
            )

    def _on_edit(self):
        item = self._selected_custom_item()
        if item is None:
            return
        if item.parent() is None:
            source = self._find_custom_source(item.data(_NAME_COLUMN, _SOURCE_KEY_ROLE))
            dialog = SourceEditDialog(initial=source, parent=self)
        else:
            source = self._find_custom_source(
                item.parent().data(_NAME_COLUMN, _SOURCE_KEY_ROLE)
            )
            dataset_key = item.data(_NAME_COLUMN, _DATASET_KEY_ROLE)
            dataset = (
                next((d for d in source["datasets"] if d["name"] == dataset_key), None)
                if source
                else None
            )
            dialog = DatasetEditDialog(
                existing_groups=self._existing_groups(source),
                initial=dataset,
                parent=self,
            )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not self._apply_edit(item, dialog.values()):
            QMessageBox.warning(self, "Duplicate name", "That name is already used.")

    def _on_remove(self):
        item = self._selected_custom_item()
        if item is None:
            return
        reply = QMessageBox.question(
            self,
            "Remove",
            f'Remove "{item.text(_NAME_COLUMN)}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._remove_selected()

    # -- import / export -----------------------------------------------------

    def _export_to(self, path):
        """Writes the current config to `path` as JSON. Returns whether the
        write succeeded; an unwritable `path` (read-only destination, full
        disk, a directory, ...) shows a warning instead of raising out of
        a Qt slot."""
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(user_sources.serialize_config(self.config()))
        except OSError as exc:
            QMessageBox.warning(
                self, "Export failed", f"Could not export to {path}:\n{exc}"
            )
            return False
        return True

    def _import_from(self, path):
        """Reads `path` and, after confirmation, replaces the in-memory
        config with it. Returns whether the replace happened: False, state
        unchanged, if `path` can't be read/decoded as a sources config (a
        warning is shown) or if the confirmation is declined."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            config = user_sources.parse_config(text)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(
                self, "Import failed", f"Could not import {path}:\n{exc}"
            )
            return False

        custom_count = len(config["custom_sources"])
        hidden_count = len(config["hidden_sources"]) + sum(
            len(keys) for keys in config["hidden_datasets"].values()
        )
        reply = QMessageBox.question(
            self,
            "Replace configuration",
            f"{custom_count} custom sources, {hidden_count} hidden items — "
            "replace current configuration?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False

        self._config = config
        self._rebuild_tree()
        return True

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Data Sources", "user_sources.json", _JSON_FILE_FILTER
        )
        if path:
            self._export_to(path)

    def _on_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Data Sources", "", _JSON_FILE_FILTER
        )
        if path:
            self._import_from(path)

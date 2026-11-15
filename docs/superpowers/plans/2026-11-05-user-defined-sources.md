# User-Defined Data Sources (Issue #84) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users customize the downloader's data sources — add their own source pages full of preset datasets (with optional groups), and hide built-in sources or individual built-in datasets — through a Data Sources manager on the existing QGIS Options page.

**Architecture:** A user config file (`user_sources.json` in the QGIS profile) is sanitized and merged with the bundled `data/presets.json` by pure functions in `core/user_sources.py`. The main dialog reads the config at construction: it skips hidden built-in radios/datasets and builds one radio + checkbox page per custom source. The Options page (from the `feature/settings-panel` branch this stacks on) gains a QTabWidget: the existing form becomes a "General" tab and a new "Data Sources" tab hosts a tree-based manager that edits the config in memory and persists it on apply().

**Tech Stack:** Python/PyQt (QGIS plugin), QgsSettings, pytest (+pytest-qt patterns already in tests/qgis), import-linter layering (core stays QGIS-free).

**Spec:** the Design section below (this plan implements GitHub issue #84 plus the maintainer's direction: a real management UI, not just a hand-edited JSON).

## Design

### User config file

`<QGIS profile>/gpq_downloader/user_sources.json`, schema version 1:

```json
{
  "version": 1,
  "hidden_sources": ["openstreetmap"],
  "hidden_datasets": {"source_cooperative": ["usda_crop"]},
  "custom_sources": [
    {
      "name": "My Company Data",
      "info_url": "https://example.com/catalog",
      "datasets": [
        {"name": "Parcels 2025", "url": "https://example.com/parcels.parquet",
         "info_url": "https://example.com/parcels", "group": "Boundaries"}
      ]
    }
  ]
}
```

Rules (enforced by sanitization, never by crashing):
- `hidden_sources` entries limited to `overture`, `source_cooperative`, `openstreetmap`. The Custom URL page can never be hidden — it is the escape hatch and the fallback for a remembered radio selection that no longer exists.
- `hidden_datasets` keys limited to `source_cooperative` and `openstreetmap`. Overture is hide-whole-source only: its page is a curated layout (buildings modifiers, divisions/base subtypes) where per-dataset hiding would be inconsistent.
- Custom source: non-empty stripped `name` (unique among custom sources, case-insensitive, first wins), optional `info_url`, list of datasets. Dataset: non-empty `name` (unique within its source, same rule) and `url`; optional `info_url` and `group`.
- Unreadable or invalid file → defaults, with one logger.log line. The dialog must always open.

### Dialog behavior

- The source radio row becomes a FlowLayout (gui/flow_layout.py) so any number of sources wraps instead of widening the dialog. Order: Overture Maps, Source Cooperative, OpenStreetMap, custom sources in config order, Custom URL last.
- Hidden built-in source: radio and page are still constructed (so every existing attribute/signal keeps working) but the radio is not added to the row and is `setVisible(False)`; an unticked hidden page contributes nothing to `get_urls()`.
- Hidden built-in dataset: filtered out of `PRESET_DATASETS` before the pages are built, so the Source Coop combo and OSM grid simply don't show it.
- Custom source page: one checkbox per dataset laid out in FlowLayouts; datasets with a `group` sit under a bold group QLabel (ungrouped datasets first, then groups in first-appearance order). Checkbox tooltip shows the dataset URL (and info_url when present). A source-level info link label (like the OSM page's) when the source has `info_url`. A custom source with zero datasets gets no radio and no page.
- Custom checkbox state is remembered like OSM's, under `gpq_downloader/custom_checkbox_{source}_{key}` with slugified names.
- Custom dataset URLs flow through the existing Worker unchanged: `Worker.needs_validation()` defaults unknown URLs to True, which is right for user-supplied data.
- The dialog is built fresh on every toolbar click, so Options changes take effect the next time it opens.

### Data Sources manager (Options page tab)

- A two-column QTreeWidget: top-level rows are sources, children are datasets. Built-in rows carry a visibility checkbox (source level for all three; dataset level only for Source Coop and OSM children; Overture children are shown unchecked-able, as information). Custom rows have no visibility checkbox — custom content is edited or removed, not hidden.
- Buttons: **New Source…**, **Add Dataset…**, **Edit…**, **Remove** (the latter three enabled only for custom selections; Remove confirms via QMessageBox.question), and **Import…** / **Export…** for the whole config as JSON (import sanitizes, shows what it found, and replaces the current config after a confirm; an invalid file warns and changes nothing).
- Edits happen on an in-memory config dict; `SettingsPage.apply()` writes the file. Cancel discards.

## Global Constraints

- `gpq_downloader/core` imports no qgis/PyQt/osgeo/gui/qgis_io; `qgis_io` never imports `gui` (pyproject import-linter contracts; CI runs `lint-imports`).
- Lint: `ruff check --select F,E9,E722 gpq_downloader/ scripts/ tests/` must stay clean.
- black line length 88.
- Local test run (QGIS-bundled Python; see `~/.claude/.../memory/local-test-environment.md`):
  `PYTHONPATH=/Users/cholmes/.local/lib/python3.12/site-packages QT_QPA_PLATFORM=offscreen /Applications/QGIS-final-4_2_3.app/Contents/MacOS/python -m pytest tests/ -q`
  Compare the **failure set** against a baseline run from the branch point, never the raw count: ~21 DuckDB-extension tests and 2 settings-pollution tests fail locally by environment. CI is authoritative.
- Every QgsSettings-touching test uses the `clean_settings` fixture (tests/qgis/conftest.py); macOS does not isolate QgsSettings via HOME.
- Tests patch Qt static methods (QMessageBox/QFileDialog) rather than running modal exec loops — the existing dialog tests show the pattern.
- Commit after each green task step pair; messages in the repo's plain style.

## Review Focus

1. A corrupt or hand-mangled `user_sources.json` must never stop the dialog or the Options page from opening → Task 2 test `test_unreadable_file_returns_defaults`, Task 3 test `test_dialog_opens_with_garbage_config`.
2. A remembered radio selection naming a now-hidden or deleted source must fall back to Custom URL → Task 3 test `test_saved_radio_for_hidden_source_falls_back`.
3. Two custom datasets whose names slugify identically must not corrupt each other's remembered state silently — the manager refuses duplicate names per source → Task 5 test `test_duplicate_dataset_name_rejected`; Task 1 sanitize dedupes anything hand-edited in.
4. Hiding every built-in source with no custom sources defined must leave a working dialog (Custom URL only) → Task 3 test `test_all_builtins_hidden_leaves_custom_url`.
5. Existing dialog tests construct `DataSourceDialog` hundreds of times; if they read the user's real profile config the suite becomes order- and machine-dependent → Task 2 adds an autouse tests/qgis fixture pointing `user_config_path()` at tmp_path.

---

### Task 1: core/user_sources.py — config model, sanitization, merge

**Files:**
- Create: `gpq_downloader/core/user_sources.py`
- Test: `tests/core/test_user_sources.py`

**Interfaces:**
- Consumes: nothing (pure stdlib: json, re).
- Produces (all pure; a "config" is the dict shape in the Design section, always fully sanitized):
  - `HIDEABLE_SOURCES: tuple[str, ...]` = `("overture", "source_cooperative", "openstreetmap")`
  - `DATASET_HIDEABLE_SOURCES: tuple[str, ...]` = `("source_cooperative", "openstreetmap")`
  - `default_config() -> dict` — `{"version": 1, "hidden_sources": [], "hidden_datasets": {}, "custom_sources": []}` (fresh objects each call)
  - `sanitize_config(raw) -> dict` — accepts anything; returns a valid config, applying the Design rules (drop unknown source keys, non-dict/list shapes, nameless/url-less datasets; dedupe names case-insensitively, first wins; strip whitespace; coerce missing optional fields to `""`)
  - `parse_config(text: str) -> dict` — json.loads then sanitize; raises `ValueError` on undecodable JSON or a non-dict top level
  - `serialize_config(config: dict) -> str` — json.dumps, indent=2, sorted keys off (preserve order)
  - `apply_hidden(presets: dict, config: dict) -> dict` — a deep-enough copy of the bundled presets with `hidden_datasets` entries removed (only for `DATASET_HIDEABLE_SOURCES`); never removes a whole source (the dialog handles `hidden_sources` at the radio level and still needs overture's dict shape)
  - `slug(name: str) -> str` — lowercase, runs of non-alphanumerics → `_`, stripped of leading/trailing `_`

- [ ] **Step 1: Write the failing tests** in `tests/core/test_user_sources.py` — plain pytest, no qgis imports. Cover at least:

```python
def test_default_config_shape(): ...          # exact dict above; two calls share no objects
def test_sanitize_passes_valid_config_through(): ...
def test_sanitize_drops_unhideable_sources(): # "custom_url", "junk" dropped from hidden_sources
def test_sanitize_limits_hidden_datasets_to_flat_sources():  # an "overture" key is dropped
def test_sanitize_drops_nameless_and_urlless_datasets(): ...
def test_sanitize_dedupes_source_and_dataset_names_case_insensitively(): ...
def test_parse_config_raises_on_bad_json_and_non_dict(): ...
def test_serialize_then_parse_roundtrips(): ...
def test_apply_hidden_removes_listed_datasets_only(): ...
def test_apply_hidden_leaves_original_presets_untouched(): ...  # no aliasing back
def test_slug():  # "My Data (2025)!" -> "my_data_2025"
```

- [ ] **Step 2: Run them to verify they fail** — `python3 -m pytest tests/core/test_user_sources.py -q` (plain python3 is fine: core is QGIS-free). Expected: import error / failures.
- [ ] **Step 3: Implement `gpq_downloader/core/user_sources.py`** with the signatures above.
- [ ] **Step 4: Run the tests to verify they pass**, then `python3 -m pytest tests/core -q` for no regressions, and `ruff check --select F,E9,E722 gpq_downloader/ tests/`.
- [ ] **Step 5: Commit** — `Add the user-sources config model to core`

### Task 2: gui/user_sources_store.py — profile-path load/save, test isolation

**Files:**
- Create: `gpq_downloader/gui/user_sources_store.py`
- Modify: `tests/qgis/conftest.py` (autouse isolation fixture)
- Test: `tests/qgis/test_user_sources_store.py`

**Interfaces:**
- Consumes: `core.user_sources.default_config/parse_config/serialize_config`; `QgsApplication.qgisSettingsDirPath()`; `gpq_downloader.logger`.
- Produces:
  - `user_config_path() -> str` — `os.path.join(QgsApplication.qgisSettingsDirPath(), "gpq_downloader", "user_sources.json")`
  - `load_user_config() -> dict` — missing file → `default_config()`; unreadable/ValueError → `default_config()` plus one `logger.log(...)` line; otherwise `parse_config(file text)`
  - `save_user_config(config: dict) -> None` — makedirs(exist_ok=True), write `serialize_config` to a temp file in the same directory, `os.replace` into place
  - tests/qgis/conftest.py gains an **autouse** fixture `isolated_user_sources(tmp_path, monkeypatch)` that monkeypatches `user_sources_store.user_config_path` to return `str(tmp_path / "user_sources.json")` — every qgis test, old and new, now reads/writes only its own temp config.

- [ ] **Step 1: Write the failing tests** — `test_missing_file_returns_defaults`, `test_unreadable_file_returns_defaults` (write garbage bytes first), `test_save_then_load_roundtrips`, `test_save_creates_parent_directory`, `test_autouse_fixture_points_path_into_tmp` (assert `user_config_path()` startswith str(tmp_path)).
- [ ] **Step 2: Run to verify they fail** — the Global Constraints pytest command with `tests/qgis/test_user_sources_store.py -q`.
- [ ] **Step 3: Implement the module and the autouse fixture.**
- [ ] **Step 4: Run the new file, then the full suite**; diff the failure set against the branch-point baseline.
- [ ] **Step 5: Commit** — `Store user sources in the QGIS profile, isolated in tests`

### Task 3: Dialog honors hidden sources and datasets

**Files:**
- Modify: `gpq_downloader/gui/dialog.py` (radio row ~355-390; PRESET_DATASETS load ~357; radio restore ~731-743 and the `_connect_filter_signals` radio loop ~868)
- Test: `tests/qgis/test_dialog_user_sources.py` (new)

**Interfaces:**
- Consumes: `user_sources_store.load_user_config()`, `core.user_sources.apply_hidden`, `FlowLayout`.
- Produces (Task 4 relies on these):
  - `self.user_config: dict` — loaded once in `__init__` before pages are built
  - `self.PRESET_DATASETS` — now `apply_hidden(load_presets(), self.user_config)`
  - `self.source_radios: list[QRadioButton]` — every *visible* radio in display order, Custom URL last; `_connect_filter_signals` and the restore/fallback logic iterate this list instead of the four named attributes
  - The radio row is a FlowLayout; hidden built-ins keep their attributes (`self.osm_radio` etc.) but are not added and are `setVisible(False)`; their stack pages may stay in the stack.
- Fallback rule: after restore, if no visible radio is checked, check `self.custom_radio`.

- [ ] **Step 1: Write the failing tests.** Helper at top of the new file: `write_config(tmp config via user_sources_store.save_user_config)` then construct `DataSourceDialog` (copy the construction pattern from `tests/qgis/test_dialog.py`; use `clean_settings`).

```python
def test_hidden_source_radio_is_not_visible(): ...        # hide openstreetmap
def test_hidden_sourcecoop_dataset_missing_from_combo(): ...
def test_hidden_osm_dataset_has_no_checkbox(): ...
def test_saved_radio_for_hidden_source_falls_back():      # save osm radio text, hide osm, reopen -> custom checked
def test_all_builtins_hidden_leaves_custom_url(): ...
def test_dialog_opens_with_garbage_config():              # garbage file bytes; dialog constructs; all four radios visible
def test_default_config_changes_nothing():                # all radios visible, combo count == bundled sourcecoop count
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** — load config, apply_hidden, FlowLayout radio row, `self.source_radios`, visibility, fallback.
- [ ] **Step 4: Run the new file, then the full suite**; diff the failure set (existing dialog tests are the real check here).
- [ ] **Step 5: Commit** — `Let the dialog hide built-in sources and datasets`

### Task 4: Custom source pages in the dialog

**Files:**
- Modify: `gpq_downloader/gui/dialog.py` (page construction after the OSM page ~575; `get_urls` ~1592+; `save_checkbox_states`/`load_checkbox_states` ~1752+), `gpq_downloader/gui/settings_store.py`
- Test: `tests/qgis/test_dialog_user_sources.py` (extend)

**Interfaces:**
- Consumes: `self.user_config["custom_sources"]`, `self.source_radios`, `core.user_sources.slug`, `FlowLayout`.
- Produces:
  - `settings_store.CUSTOM_CHECKBOX = "gpq_downloader/custom_checkbox_{source}_{key}"` (both fields slugged)
  - `self.custom_checkboxes: dict[str, dict[str, QCheckBox]]` — source name → dataset name → checkbox
  - One radio (text = source name) + one stack page per custom source with ≥1 dataset, ordered per Design; page layout: ungrouped FlowLayout of checkboxes, then per group a bold QLabel + FlowLayout; tooltips carry url (+ info_url); source-level `info_url` renders an open-external-links QLabel like `self.osm_link`.
  - `get_urls()`: when a custom source's radio is checked, return the checked datasets' urls (no release, no templates).
  - `save_checkbox_states`/`load_checkbox_states` handle `self.custom_checkboxes` with CUSTOM_CHECKBOX keys (default unchecked).

- [ ] **Step 1: Write the failing tests.**

```python
def test_custom_source_gets_radio_and_checkboxes(): ...
def test_custom_source_get_urls_returns_checked_urls(): ...   # two checked, one not
def test_custom_checkbox_state_remembered():                  # accept path saves; new dialog restores (clean_settings)
def test_group_headers_render_in_group_order(): ...           # QLabel texts on the page
def test_dataset_tooltip_carries_url(): ...
def test_empty_custom_source_gets_no_radio(): ...
def test_custom_radio_selection_remembered(): ...             # save_radio_button_state round-trip by source name
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** — settings_store key first, then page building, get_urls branch, save/load wiring.
- [ ] **Step 4: Run the new file, the other dialog test files, then the full suite**; diff the failure set.
- [ ] **Step 5: Commit** — `Give each custom source its own page in the dialog`

### Task 5: The Data Sources manager widget

**Files:**
- Create: `gpq_downloader/gui/sources_page.py`
- Test: `tests/qgis/test_sources_page.py`

**Interfaces:**
- Consumes: `core.user_sources` (sanitize, HIDEABLE_SOURCES, DATASET_HIDEABLE_SOURCES), `core.presets.load_presets()`.
- Produces:
  - `class DatasetEditDialog(QDialog)` — fields `name_edit`, `url_edit` (both required; OK rejects with inline message while empty), `info_url_edit`, `group_combo` (editable QComboBox pre-filled with the source's existing groups); `values() -> dict` (dataset dict, empty optionals as "")
  - `class SourceEditDialog(QDialog)` — `name_edit` (required), `info_url_edit`; `values() -> dict`
  - `class SourcesManagerWidget(QWidget)` — `__init__(self, config: dict, presets: dict, parent=None)` (deep-copies config), `config() -> dict` (current in-memory state, sanitized), and a QTreeWidget `self.tree` + buttons `self.new_source_button`, `self.add_dataset_button`, `self.edit_button`, `self.remove_button` (Import/Export buttons arrive in Task 7).
  - Tree rules per Design. Mutation methods separated from their modal prompts so tests drive them directly: `_add_source(values: dict) -> bool`, `_add_dataset(source_name: str, values: dict) -> bool`, `_apply_edit(item, values: dict) -> bool`, `_remove_selected() -> None` (the Remove *button handler* asks QMessageBox.question; tests patch it), plus checkbox-change handling that updates `hidden_sources`/`hidden_datasets`. `_add_*`/`_apply_edit` return False (and leave state unchanged) on a duplicate name within the scope.
- Duplicate-name rejection surfaces a `QMessageBox.warning` in the button handlers only; the underlying methods just return False.

- [ ] **Step 1: Write the failing tests.**

```python
def test_tree_lists_builtins_and_customs(): ...
def test_unchecking_builtin_source_adds_to_hidden_sources(): ...
def test_unchecking_sourcecoop_child_hides_that_dataset(): ...
def test_overture_children_are_not_checkable(): ...
def test_add_source_then_dataset_lands_in_config(): ...
def test_duplicate_dataset_name_rejected(): ...        # _add_dataset returns False, config unchanged
def test_duplicate_source_name_rejected(): ...
def test_edit_dataset_updates_config(): ...
def test_remove_source_removes_datasets(): ...         # patch QMessageBox.question -> Yes
def test_edit_remove_disabled_for_builtin_selection(): ...
def test_config_returns_deep_copy_of_initial_when_untouched(): ...  # mutating returned dict cannot alter widget state
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `sources_page.py`.**
- [ ] **Step 4: Run the new file, then the full suite**; diff the failure set; ruff.
- [ ] **Step 5: Commit** — `Add the Data Sources manager widget`

### Task 6: Wire the manager into the Options page

**Files:**
- Modify: `gpq_downloader/gui/settings_page.py`
- Test: `tests/qgis/test_settings_page.py` (extend)

**Interfaces:**
- Consumes: `SourcesManagerWidget`, `user_sources_store.load_user_config/save_user_config`, `core.presets.load_presets`.
- Produces: `SettingsPage` holds a `QTabWidget` with tabs **General** (the existing form + Advanced box, attributes unchanged) and **Data Sources** (`self.sources_widget = SourcesManagerWidget(user_sources_store.load_user_config(), load_presets(), self)`); `apply()` additionally calls `user_sources_store.save_user_config(self.sources_widget.config())`.

- [ ] **Step 1: Write the failing tests** — `test_page_has_general_and_data_sources_tabs`, `test_apply_saves_sources_config` (mutate via `self.sources_widget._add_source(...)`, apply, `load_user_config()` shows it), `test_apply_still_saves_general_settings` (guards the tab refactor).
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run test_settings_page.py, then the full suite**; diff the failure set.
- [ ] **Step 5: Commit** — `Put the sources manager on the options page`

### Task 7: Import/Export and documentation

**Files:**
- Modify: `gpq_downloader/gui/sources_page.py`, `README.md`
- Test: `tests/qgis/test_sources_page.py` (extend)

**Interfaces:**
- Consumes: `core.user_sources.parse_config/serialize_config`.
- Produces on `SourcesManagerWidget`: `self.import_button`/`self.export_button`; `_export_to(path: str) -> None` writes `serialize_config(self.config())`; `_import_from(path: str) -> bool` parses+sanitizes, on ValueError shows `QMessageBox.warning` and returns False leaving state unchanged, else `QMessageBox.question` summarising ("N custom sources, M hidden items — replace current configuration?") and on Yes replaces the whole in-memory config and rebuilds the tree. Button handlers get paths from `QFileDialog.getSaveFileName`/`getOpenFileName` (tests patch the statics, filter `"JSON files (*.json)"`).
- README: a short **Custom data sources** section — what the Options tab does, where `user_sources.json` lives, that it can be hand-edited or shared via Import/Export.

- [ ] **Step 1: Write the failing tests** — `test_export_then_import_roundtrips`, `test_import_invalid_file_warns_and_keeps_state` (patch warning), `test_import_replaces_after_confirm` (patch question → Yes), `test_import_cancelled_keeps_state` (→ No).
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement, then write the README section.**
- [ ] **Step 4: Run the new tests, the full suite, ruff, and lint-imports** (CI runs `lint-imports`; locally via the dev install if available, otherwise note it for CI).
- [ ] **Step 5: Commit** — `Let users import and export their sources config`

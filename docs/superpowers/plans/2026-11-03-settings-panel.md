# Settings Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the plugin one QGIS Options page holding eight settings, and move the hardcoded ZSTD level 22 to a configurable default of 15.

**Architecture:** Pure data and string-building live in `core/` (output-format table, COPY options builder). The page and its keys live in `gui/`. `plugin.py` reads the download settings once per enqueued batch and passes them through `DownloadJob` into `Worker`, because `pyproject.toml` forbids `qgis_io` from importing `gui` — directly or through `logger.py`. The logging level is a module-level threshold in `logger.py` that `plugin.py` and the page set.

**Tech Stack:** Python 3.7+, PyQt5 and PyQt6 via `qgis.PyQt`, `QgsOptionsWidgetFactory`/`QgsOptionsPageWidget`, `QgsSettings`, DuckDB 1.1.0+, pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-settings-panel-design.md`

## Global Constraints

- Qt5 and Qt6 both supported. Every Qt enum fully scoped: `QDialogButtonBox.StandardButton.Ok`, never `QDialogButtonBox.Ok`.
- `gpq_downloader.core` must not import `qgis`, `PyQt5`, `PyQt6`, `osgeo`, `gpq_downloader.gui` or `gpq_downloader.qgis_io` (import-linter contract "core stays QGIS-free").
- `gpq_downloader.qgis_io` must not import `gpq_downloader.gui`, directly or transitively (contract "qgis_io does not import the GUI", no `allow_indirect_imports`). `logger.py` is imported by `qgis_io`, so `logger.py` must not import `gui` either.
- `gui/settings_store.py` is the only module that constructs `QgsSettings`. All keys live under the `gpq_downloader/` prefix in `QgsSettings.Section.Plugins`.
- `COMPRESSION_LEVEL` is valid in DuckDB **only** with the ZSTD codec. With any other codec DuckDB raises `Binder Error: Compression level is only supported for the ZSTD compression codec`.
- New ZSTD default compression level: **15**. UI range 1–22.
- `$PYTEST` below means: `HOME=$(mktemp -d) PYTHONPATH=/Users/cholmes/.local/lib/python3.12/site-packages QT_QPA_PLATFORM=offscreen /Applications/QGIS-final-4_2_3.app/Contents/MacOS/python -m pytest`, and `python -m lint_imports` runs under that same interpreter. `HOME` does **not** isolate `QgsSettings` on this Mac; any test that writes settings must snapshot and restore every key matching `plugins/gpq_downloader/` and `gpq_downloader/`. `tests/qgis/test_dialog.py::test_building_parts_checkbox` and `test_dialog_attribute_filter.py::test_the_filter_is_not_remembered_between_sessions` fail locally on `main` for that reason — diff the failure *set* against a `main` baseline, never a raw count.

- Every commit message in this plan ends with these two trailer lines:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NCUyfXDTzEpTDU7KcNLtoj
```

## Review Focus

Input classes the spec implies that no task's own happy-path tests exercise. Each has its test added to the owning task.

1. **A stored output folder that no longer exists or cannot be created** (unmounted drive, revoked permission) — the save dialog must open in `~/Downloads` with a message-bar warning, not raise out of a Qt slot. *Task 6.*
2. **A stored compression level outside 1–22, or not an int at all** — must clamp into range rather than emit `COMPRESSION_LEVEL 900` and fail the whole download. *Task 3.*
3. **A stored output-format or codec name that is not in the table** (hand-edited profile, a name dropped in a later version) — must fall back to GeoParquet / ZSTD rather than raise at save or COPY time. *Tasks 1 and 3.*
4. **A stored log level that is not one of the four names** — must fall back to Info, because a raise in `log()` breaks every code path that logs. *Task 7.*
5. **A row group size of 0 or negative** — must emit no `ROW_GROUP_SIZE` clause, so DuckDB's own default applies and today's output is unchanged. *Task 1.*

---

## File Structure

**Created**
- `gpq_downloader/core/output_formats.py` — the output-format table: name, extension, save-dialog filter. Pure data.
- `gpq_downloader/core/copy_options.py` — the `COPY ... TO` trailing options string, the compression defaults, and the codec-error advice. Pure strings.
- `gpq_downloader/gui/settings_page.py` — `SettingsPage(QgsOptionsPageWidget)` and `SettingsPageFactory(QgsOptionsWidgetFactory)`.
- `tests/core/test_copy_options.py`, `tests/core/test_output_formats.py`
- `tests/qgis/test_settings_page.py`, `tests/qgis/test_plugin_settings.py`

**Modified**
- `gpq_downloader/gui/settings_store.py` — nine new keys, `load_int`, `load_choice`.
- `gpq_downloader/core/plan.py` — four new `DownloadJob` fields.
- `gpq_downloader/qgis_io/worker.py` — `Worker` takes the four download settings; the COPY options come from `core`; the Hilbert sort becomes conditional; the unsupported-format branch returns.
- `gpq_downloader/plugin.py` — registers the factory, reads the settings at enqueue, resolves the output folder and format per save dialog, sets the log level.
- `gpq_downloader/gui/dialog.py` — a gear button beside OK.
- `gpq_downloader/logger.py` — a level threshold, `set_level()`, `debug()`.
- `tests/qgis/conftest.py` — `MockIface` gains the three options-dialog methods.
- `tests/core/test_worker_golden_sql.py`, `tests/core/worker_cases.py` — level 22 → 15, a sort-off case.
- `tests/qgis/test_plugin.py` — the save-dialog stub takes the new `initialFilter` argument.

---

### Task 1: Output formats and COPY options in core

**Files:**
- Create: `gpq_downloader/core/output_formats.py`
- Create: `gpq_downloader/core/copy_options.py`
- Test: `tests/core/test_output_formats.py`, `tests/core/test_copy_options.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `output_formats.FORMATS: tuple[OutputFormat, ...]` where `OutputFormat` is a frozen dataclass with `name: str`, `extension: str`, `dialog_filter: str`.
  - `output_formats.DEFAULT_FORMAT_NAME = "GeoParquet"`
  - `output_formats.format_names() -> tuple[str, ...]`
  - `output_formats.dialog_filters() -> str` — the `";;"`-joined filter string.
  - `output_formats.format_for(name: str) -> OutputFormat` — the named format, or the GeoParquet one for any name not in the table.
  - `copy_options.CODECS: tuple[str, ...]`, `copy_options.DEFAULT_CODEC = "ZSTD"`, `copy_options.DEFAULT_COMPRESSION_LEVEL = 15`, `copy_options.MIN_COMPRESSION_LEVEL = 1`, `copy_options.MAX_COMPRESSION_LEVEL = 22`, `copy_options.DEFAULT_SPATIAL_SORT = True`
  - `copy_options.format_options(output_file: str, *, codec: str = DEFAULT_CODEC, compression_level: int = DEFAULT_COMPRESSION_LEVEL, row_group_size: Optional[int] = None) -> Optional[str]` — the whole trailing options string including its `;`, or `None` for an extension the plugin cannot write.
  - `copy_options.codec_error_advice(error_text: str, codec: str) -> Optional[str]` — a readable replacement for a DuckDB codec/compression error, or `None` when the error is about something else.

- [ ] **Step 1: Write the failing tests for the format table**

```python
# tests/core/test_output_formats.py
from gpq_downloader.core import output_formats as of

def test_the_dialog_filter_string_is_the_one_the_plugin_shows_today():
    assert of.dialog_filters() == (
        "GeoParquet (*.parquet);;DuckDB Database (*.duckdb);;GeoPackage (*.gpkg);;"
        "FlatGeobuf (*.fgb);;GeoJSON (*.geojson)"
    )

def test_each_name_maps_to_its_extension():
    assert [(f.name, f.extension) for f in of.FORMATS] == [
        ("GeoParquet", ".parquet"), ("DuckDB Database", ".duckdb"),
        ("GeoPackage", ".gpkg"), ("FlatGeobuf", ".fgb"), ("GeoJSON", ".geojson"),
    ]

def test_an_unknown_stored_name_falls_back_to_geoparquet():
    assert of.format_for("Shapefile").extension == ".parquet"
    assert of.format_for("").name == of.DEFAULT_FORMAT_NAME
```

- [ ] **Step 2: Write the failing tests for the options builder**

```python
# tests/core/test_copy_options.py
import pytest
from gpq_downloader.core import copy_options as co

def test_the_parquet_default_is_zstd_at_level_15():
    assert co.format_options("/out/x.parquet") == (
        "(FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 15);"
    )

@pytest.mark.parametrize("codec", ["SNAPPY", "GZIP", "BROTLI", "LZ4", "UNCOMPRESSED"])
def test_no_compression_level_clause_for_a_non_zstd_codec(codec):
    options = co.format_options("/out/x.parquet", codec=codec, compression_level=15)
    assert options == f"(FORMAT 'parquet', COMPRESSION '{codec}');"
    assert "COMPRESSION_LEVEL" not in options

def test_an_unknown_codec_falls_back_to_zstd():
    assert co.format_options("/out/x.parquet", codec="SQUEEZE") == (
        "(FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 15);"
    )

@pytest.mark.parametrize("size", [None, 0, -1])
def test_no_row_group_size_clause_when_it_is_unset_or_not_positive(size):
    assert "ROW_GROUP_SIZE" not in co.format_options("/out/x.parquet", row_group_size=size)

def test_a_positive_row_group_size_is_emitted_last():
    assert co.format_options("/out/x.parquet", row_group_size=15000) == (
        "(FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 15, ROW_GROUP_SIZE 15000);"
    )

@pytest.mark.parametrize("path, expected", [
    ("/out/x.gpkg", "(FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326');"),
    ("/out/x.fgb", "(FORMAT GDAL, DRIVER 'FlatGeobuf', SRS 'EPSG:4326');"),
    ("/out/x.geojson", "(FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326');"),
    ("/out/X.GPKG", "(FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326');"),
])
def test_the_gdal_formats_take_no_compression_options(path, expected):
    assert co.format_options(path, codec="SNAPPY", row_group_size=15000) == expected

def test_an_extension_the_plugin_cannot_write_has_no_options():
    assert co.format_options("/out/x.shp") is None

def test_a_compression_error_is_reported_with_the_codec_and_where_to_change_it():
    advice = co.codec_error_advice("Binder Error: Unsupported compression codec BROTLI", "BROTLI")
    assert "BROTLI" in advice and "Settings" in advice

def test_an_unrelated_error_is_left_alone():
    assert co.codec_error_advice("IO Error: no such file", "ZSTD") is None
```

- [ ] **Step 3: Run both test files to verify they fail**

Run: `$PYTEST tests/core/test_output_formats.py tests/core/test_copy_options.py -q -p no:cacheprovider --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'gpq_downloader.core.output_formats'`

- [ ] **Step 4: Implement `core/output_formats.py`**

A frozen dataclass and a module-level tuple in the order above; `dialog_filters()` joins `dialog_filter` with `";;"`; `format_for()` falls back to `FORMATS[0]`. No imports beyond `dataclasses` and `typing`.

- [ ] **Step 5: Implement `core/copy_options.py`**

`format_options()` dispatches on the lowercased extension of `output_file`. For Parquet it builds the clause list in the order the tests pin: `FORMAT 'parquet'`, `COMPRESSION '<codec>'`, then `COMPRESSION_LEVEL` **only** when the codec is ZSTD, then `ROW_GROUP_SIZE` only when `row_group_size` is a positive int. A codec not in `CODECS` becomes `DEFAULT_CODEC`. `compression_level` is clamped to `MIN_COMPRESSION_LEVEL..MAX_COMPRESSION_LEVEL`. `.duckdb` is not an extension this function is asked about (the worker commits instead of copying) — return `None` for it like any other unwritable extension.

`codec_error_advice()` returns `None` unless the error text contains `"compression"` (case-insensitively) or the codec name, in which case it returns:
`f"DuckDB could not write with the {codec} compression codec: {error_text}. Choose a different codec under Settings → Options → GeoParquet Downloader."`

- [ ] **Step 6: Run both test files to verify they pass**

Run: the command from Step 3.
Expected: PASS

- [ ] **Step 7: Check the layering contract still holds**

Run: `PYTHONPATH=/Users/cholmes/.local/lib/python3.12/site-packages /Applications/QGIS-final-4_2_3.app/Contents/MacOS/python -m lint_imports`
Expected: both contracts KEPT

- [ ] **Step 8: Commit**

```bash
git add gpq_downloader/core/output_formats.py gpq_downloader/core/copy_options.py tests/core/test_output_formats.py tests/core/test_copy_options.py
git commit -m "Build the COPY options and the output-format table in core"
```

---

### Task 2: The worker takes its write settings from the job

**Files:**
- Modify: `gpq_downloader/core/plan.py` (`DownloadJob`)
- Modify: `gpq_downloader/qgis_io/worker.py` (`Worker.__init__`; the COPY block around lines 380–412)
- Modify: `gpq_downloader/plugin.py` (`_make_worker`)
- Test: `tests/core/test_worker_golden_sql.py`, `tests/core/worker_cases.py`

**Interfaces:**
- Consumes: `core.copy_options.format_options`, `codec_error_advice`, and the four defaults from Task 1.
- Produces:
  - `DownloadJob` gains `compression_codec: str = copy_options.DEFAULT_CODEC`, `compression_level: int = copy_options.DEFAULT_COMPRESSION_LEVEL`, `row_group_size: Optional[int] = None`, `spatial_sort: bool = True`, all after `size_warning_accepted`.
  - `Worker.__init__(..., *, where_fragment=None, compression_codec=DEFAULT_CODEC, compression_level=DEFAULT_COMPRESSION_LEVEL, row_group_size=None, spatial_sort=True)` — keyword-only, same defaults, so every existing construction keeps working and produces level 15.
  - `worker_cases.run_worker(..., spatial_sort=True, compression_codec=None, compression_level=None, row_group_size=None)` — passes them through when given.

- [ ] **Step 1: Update the golden SQL for the new default and add the sort-off case**

In `tests/core/test_worker_golden_sql.py` change the `parquet` entry of `FORMAT_OPTIONS` to `"(FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 15);"`, add an unsorted helper beside `copy()`, and add one test:

```python
def copy_unsorted(output_file, options):
    return f"COPY ( SELECT * FROM download_data ) TO '{output_file}' {options}"

def test_spatial_sort_off_drops_the_hilbert_order_and_its_cte(monkeypatch):
    source = SOURCES["custom"]
    worker, conn, signals = run_worker(
        source, OUT + ".parquet", monkeypatch, spatial_sort=False
    )
    statements = [squash(s) for s in conn.statements]
    assert copy_unsorted(OUT + ".parquet", FORMAT_OPTIONS["parquet"]) in statements
    assert not any("ST_Hilbert" in s for s in statements)
```

Read the file first: use whatever `SOURCES` key and `conn.statements` accessor the neighbouring tests use, and follow their assertion style rather than this sketch's if they differ.

- [ ] **Step 2: Run the golden SQL tests to verify they fail**

Run: `$PYTEST tests/core/test_worker_golden_sql.py -q -p no:cacheprovider --no-cov`
Expected: FAIL — the level-22 cases mismatch, and `run_worker() got an unexpected keyword argument 'spatial_sort'`

- [ ] **Step 3: Add the four fields to `DownloadJob` and the four keyword arguments to `Worker.__init__`**

Store each on `self`. `dataclasses.replace()` already carries them through the GeoJSON size-warning retry.

- [ ] **Step 4: Replace the hardcoded options and make the sort conditional in `worker.py`**

The `copy_query` f-string keeps its current text when `self.spatial_sort` is true. When it is false, build `COPY ( SELECT * FROM {table_name} ) TO {sql_str(self.output_file)}` instead — no `WITH bbox` CTE and no `ORDER BY` — for every format, not only Parquet.

Replace the four-branch `format_options` assignment with a single call to `copy_options.format_options(self.output_file, codec=self.compression_codec, compression_level=self.compression_level, row_group_size=self.row_group_size)`, and when it returns `None` emit `"Unsupported file format."` and **return**. Today that branch falls through to `conn.execute(copy_query + format_options)` with `format_options` unbound, which raises `NameError` inside the `except Exception` and reports that instead of the real problem.

Wrap only the `conn.execute(copy_query + format_options)` call so a codec failure is readable: catch `Exception`, pass `str(e)` and `self.compression_codec` to `copy_options.codec_error_advice()`, and `raise` unchanged when it returns `None`; otherwise `self.error.emit(advice)` and return.

- [ ] **Step 5: Pass the job's settings in `plugin.py:_make_worker`**

Add the four keyword arguments, each from the matching `job` field.

- [ ] **Step 6: Run the worker tests to verify they pass**

Run: `$PYTEST tests/core/test_worker_golden_sql.py tests/core/test_worker_helpers.py tests/core/test_download_queue.py tests/qgis/test_worker.py tests/qgis/test_worker_qgis_seams.py -q -p no:cacheprovider --no-cov`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gpq_downloader/core/plan.py gpq_downloader/qgis_io/worker.py gpq_downloader/plugin.py tests/core/
git commit -m "Carry the write settings on the job instead of hardcoding them"
```

---

### Task 3: The settings keys and their loaders

**Files:**
- Modify: `gpq_downloader/gui/settings_store.py`
- Test: `tests/qgis/test_settings_page.py` (the loader half; the page half arrives in Task 4)

**Interfaces:**
- Consumes: `core.copy_options` and `core.output_formats` defaults from Task 1.
- Produces, in `settings_store`:
  - Keys `OUTPUT_FORMAT`, `OUTPUT_FOLDER`, `REMEMBER_LAST_FOLDER`, `LAST_OUTPUT_FOLDER`, `COMPRESSION_CODEC`, `COMPRESSION_LEVEL`, `ROW_GROUP_SIZE`, `SPATIAL_SORT`, `LOG_LEVEL` — each `"gpq_downloader/<snake_case name>"`.
  - `load_int(key, default)` — the int stored under key, `default` when absent or not convertible.
  - `load_choice(key, allowed, default)` — the stored str when it is in `allowed`, else `default`.
  - `load_download_settings() -> dict` with keys `compression_codec`, `compression_level`, `row_group_size`, `spatial_sort` — exactly the keyword arguments `DownloadJob`/`Worker` take, already defaulted and clamped.
  - `LOG_LEVELS = ("Debug", "Info", "Warning", "Critical")` and `DEFAULT_LOG_LEVEL = "Info"`.
  - `LAST_OUTPUT_FOLDER` is the remembered folder itself, which the spec's table does not list because it is storage rather than a setting; setting 3 only decides whether it is consulted.

- [ ] **Step 1: Write the failing tests**

```python
# tests/qgis/test_settings_page.py  (loaders)
import pytest
from qgis.core import QgsSettings
from gpq_downloader.core import copy_options, output_formats
from gpq_downloader.gui import settings_store

PREFIXES = ("plugins/gpq_downloader/", "gpq_downloader/")

@pytest.fixture
def clean_settings(qgs_app):
    """No gpq_downloader settings during the test; the user's own back after."""
    def ours():
        return sorted(k for k in QgsSettings().allKeys() if k.startswith(PREFIXES))
    settings = QgsSettings()
    saved = {k: settings.value(k) for k in ours()}
    for key in saved:
        settings.remove(key)
    yield settings
    for key in ours():
        settings.remove(key)
    for key, value in saved.items():
        settings.setValue(key, value)

def test_nothing_stored_gives_the_documented_defaults(clean_settings):
    assert settings_store.load_download_settings() == {
        "compression_codec": "ZSTD",
        "compression_level": 15,
        "row_group_size": None,
        "spatial_sort": True,
    }

def test_stored_values_round_trip(clean_settings):
    settings_store.save_value(settings_store.COMPRESSION_CODEC, "SNAPPY")
    settings_store.save_value(settings_store.COMPRESSION_LEVEL, 9)
    settings_store.save_value(settings_store.ROW_GROUP_SIZE, 15000)
    settings_store.save_value(settings_store.SPATIAL_SORT, False)
    assert settings_store.load_download_settings() == {
        "compression_codec": "SNAPPY",
        "compression_level": 9,
        "row_group_size": 15000,
        "spatial_sort": False,
    }

@pytest.mark.parametrize("stored, expected", [(0, 1), (99, 22), (-5, 1), ("fifteen", 15)])
def test_a_level_outside_the_range_is_clamped(clean_settings, stored, expected):
    settings_store.save_value(settings_store.COMPRESSION_LEVEL, stored)
    assert settings_store.load_download_settings()["compression_level"] == expected

def test_an_unknown_stored_codec_reads_as_zstd(clean_settings):
    settings_store.save_value(settings_store.COMPRESSION_CODEC, "SQUEEZE")
    assert settings_store.load_download_settings()["compression_codec"] == "ZSTD"

@pytest.mark.parametrize("stored", [0, -1, "none"])
def test_a_non_positive_row_group_size_reads_as_unset(clean_settings, stored):
    settings_store.save_value(settings_store.ROW_GROUP_SIZE, stored)
    assert settings_store.load_download_settings()["row_group_size"] is None

def test_an_unknown_stored_output_format_reads_as_the_default(clean_settings):
    settings_store.save_value(settings_store.OUTPUT_FORMAT, "Shapefile")
    name = settings_store.load_choice(
        settings_store.OUTPUT_FORMAT, output_formats.format_names(),
        output_formats.DEFAULT_FORMAT_NAME,
    )
    assert name == "GeoParquet"

def test_remember_last_folder_is_on_when_nothing_is_stored(clean_settings):
    assert settings_store.load_flag(settings_store.REMEMBER_LAST_FOLDER, True) is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PYTEST tests/qgis/test_settings_page.py -q -p no:cacheprovider --no-cov`
Expected: FAIL — `AttributeError: module ... has no attribute 'COMPRESSION_CODEC'`

- [ ] **Step 3: Add the keys and loaders to `settings_store.py`**

Follow the module's existing shape: one constant per key with a comment where the key is not self-explanatory, and a `load_*` helper per type. `load_int` reads with `type=int` and guards the way `load_text` guards a non-`str`, because a test that stands a `MagicMock` in for `QgsSettings` must not put a mock into a `QSpinBox`. `load_download_settings()` composes the four reads and does the clamping, so `plugin.py` has one call and the clamp has one home.

- [ ] **Step 4: Run to verify they pass**

Run: the command from Step 2.
Expected: PASS

- [ ] **Step 5: Run the existing settings characterization to be sure the saved-key set is untouched**

Run: `$PYTEST tests/qgis/test_dialog_settings.py -q -p no:cacheprovider --no-cov`
Expected: PASS — the new keys are written by the options page and by `plugin.py`, never by `dialog.save_checkbox_states()`, so `ALL_SAVED_KEYS` is still the exact set that call writes. If this fails, something wrote a settings key during dialog construction; fix that rather than widening `ALL_SAVED_KEYS`.

- [ ] **Step 6: Commit**

```bash
git add gpq_downloader/gui/settings_store.py tests/qgis/test_settings_page.py
git commit -m "Add the settings-panel keys and their loaders"
```

---

### Task 4: The options page and its factory

**Files:**
- Create: `gpq_downloader/gui/settings_page.py`
- Modify: `gpq_downloader/plugin.py` (`initGui`, `unload`)
- Modify: `tests/qgis/conftest.py` (`MockIface`)
- Test: `tests/qgis/test_settings_page.py`

**Interfaces:**
- Consumes: every key and loader from Task 3; `core.output_formats.format_names()`; `core.copy_options.CODECS` and its level bounds.
- Produces:
  - `settings_page.PAGE_OBJECT_NAME = "gpqDownloaderOptions"` — the page widget's `objectName`, and what `iface.showOptionsDialog()` is given to open it.
  - `settings_page.SettingsPage(parent=None)` — a `QgsOptionsPageWidget` whose `apply()` writes every key. Public attributes, for the tests and for Task 7: `format_combo`, `folder_widget`, `remember_checkbox`, `codec_combo`, `level_spin`, `row_group_spin`, `sort_checkbox`, `log_combo`.
  - `settings_page.SettingsPageFactory()` — a `QgsOptionsWidgetFactory` titled `"GeoParquet Downloader"`; `createWidget(parent)` returns a `SettingsPage`.
  - `plugin.settings_factory` — the registered factory, `None` before `initGui()`.

- [ ] **Step 1: Add the options-dialog methods to `MockIface`**

In `tests/qgis/conftest.py`, record the calls so tests can assert them:

```python
        self.options_factories = []       # in __init__
        self.options_dialog_calls = []

    def registerOptionsWidgetFactory(self, factory):
        self.options_factories.append(factory)

    def unregisterOptionsWidgetFactory(self, factory):
        if factory in self.options_factories:
            self.options_factories.remove(factory)

    def showOptionsDialog(self, parent=None, currentPage=""):
        self.options_dialog_calls.append(currentPage)
```

- [ ] **Step 2: Write the failing page tests**

```python
# appended to tests/qgis/test_settings_page.py
from gpq_downloader.gui.settings_page import PAGE_OBJECT_NAME, SettingsPage, SettingsPageFactory
from gpq_downloader.plugin import QgisPluginGeoParquet

def test_the_page_offers_every_format_and_codec(clean_settings, qgs_app):
    page = SettingsPage()
    assert [page.format_combo.itemText(i) for i in range(page.format_combo.count())] == \
        list(output_formats.format_names())
    assert [page.codec_combo.itemText(i) for i in range(page.codec_combo.count())] == \
        list(copy_options.CODECS)
    assert page.level_spin.minimum() == 1 and page.level_spin.maximum() == 22

def test_the_page_opens_on_the_stored_values(clean_settings, qgs_app):
    settings_store.save_value(settings_store.COMPRESSION_CODEC, "GZIP")
    settings_store.save_value(settings_store.SPATIAL_SORT, False)
    page = SettingsPage()
    assert page.codec_combo.currentText() == "GZIP"
    assert page.sort_checkbox.isChecked() is False

def test_the_page_opens_on_the_defaults_when_nothing_is_stored(clean_settings, qgs_app):
    page = SettingsPage()
    assert page.codec_combo.currentText() == "ZSTD"
    assert page.level_spin.value() == 15
    assert page.sort_checkbox.isChecked() is True
    assert page.remember_checkbox.isChecked() is True
    assert page.log_combo.currentText() == "Info"

def test_the_level_control_is_only_enabled_for_zstd(clean_settings, qgs_app):
    page = SettingsPage()
    assert page.level_spin.isEnabled() is True
    page.codec_combo.setCurrentText("SNAPPY")
    assert page.level_spin.isEnabled() is False
    page.codec_combo.setCurrentText("ZSTD")
    assert page.level_spin.isEnabled() is True

def test_apply_writes_what_the_download_settings_read_back(clean_settings, qgs_app):
    page = SettingsPage()
    page.codec_combo.setCurrentText("SNAPPY")
    page.level_spin.setValue(9)
    page.row_group_spin.setValue(15000)
    page.sort_checkbox.setChecked(False)
    page.apply()
    assert settings_store.load_download_settings() == {
        "compression_codec": "SNAPPY", "compression_level": 9,
        "row_group_size": 15000, "spatial_sort": False,
    }

def test_apply_stores_nothing_under_a_key_the_dialog_also_writes(clean_settings, qgs_app):
    SettingsPage().apply()
    written = sorted(k for k in QgsSettings().allKeys() if k.startswith(PREFIXES))
    assert not any(k.endswith(("clip_to_area", "aoi_enabled", "attribute_filter")) for k in written)

def test_the_page_carries_the_name_the_gear_button_opens(clean_settings, qgs_app):
    assert SettingsPage().objectName() == PAGE_OBJECT_NAME

def test_the_plugin_registers_the_factory_and_takes_it_away_again(qgs_app, mock_iface):
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.initGui()
    assert len(mock_iface.options_factories) == 1
    assert isinstance(mock_iface.options_factories[0], SettingsPageFactory)
    plugin.unload()
    assert mock_iface.options_factories == []
```

- [ ] **Step 3: Run to verify they fail**

Run: `$PYTEST tests/qgis/test_settings_page.py -q -p no:cacheprovider --no-cov`
Expected: FAIL — `ModuleNotFoundError: No module named 'gpq_downloader.gui.settings_page'`

- [ ] **Step 4: Implement `gui/settings_page.py`**

`SettingsPage.__init__` builds the widgets, loads every value through `settings_store`, sets `self.setObjectName(PAGE_OBJECT_NAME)`, and connects the codec combo to a slot that enables `level_spin` only for `"ZSTD"`. `apply()` writes each widget's value with `settings_store.save_value`.

Layout: a `QFormLayout` of settings 1–3 and 8, then a `QgsCollapsibleGroupBox` titled "Advanced" holding the codec, level, row group size and spatial sort, as the owner asked for compression and row group size to sit together. Use `QgsFileWidget` with `QgsFileWidget.StorageMode.GetDirectory` for the folder, and give `row_group_spin` a maximum of 10_000_000, a minimum of 0 and `setSpecialValueText("DuckDB default")` so 0 reads as unset. Every Qt enum fully scoped.

- [ ] **Step 5: Register and unregister the factory in `plugin.py`**

`__init__` sets `self.settings_factory = None`. `initGui()` builds a `SettingsPageFactory`, keeps it on `self.settings_factory`, and calls `self.iface.registerOptionsWidgetFactory(...)`. `unload()` calls `self.iface.unregisterOptionsWidgetFactory(self.settings_factory)` when it is not `None`, before `removeToolBarIcon`, and clears the attribute — `unload()` returns early while a download is running, and the existing ordering must be preserved.

- [ ] **Step 6: Run the page and plugin tests to verify they pass**

Run: `$PYTEST tests/qgis/test_settings_page.py tests/qgis/test_plugin.py tests/qgis/test_integration.py tests/qgis/test_duckdb_setup.py -q -p no:cacheprovider --no-cov`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gpq_downloader/gui/settings_page.py gpq_downloader/plugin.py tests/qgis/conftest.py tests/qgis/test_settings_page.py
git commit -m "Add the GeoParquet Downloader options page"
```

---

### Task 5: The gear button in the download dialog

**Files:**
- Modify: `gpq_downloader/gui/dialog.py` (the button row at lines 673–680)
- Test: `tests/qgis/test_settings_page.py`

**Interfaces:**
- Consumes: `settings_page.PAGE_OBJECT_NAME`; `MockIface.options_dialog_calls` from Task 4.
- Produces: `DataSourceDialog.settings_button` (a `QToolButton`) and `DataSourceDialog.open_settings()`.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/qgis/test_settings_page.py
from gpq_downloader.gui.dialog import DataSourceDialog

def test_the_gear_opens_the_plugins_own_options_page(qgs_app, mock_iface):
    dialog = DataSourceDialog(mock_iface.mainWindow(), mock_iface)
    dialog.settings_button.click()
    assert mock_iface.options_dialog_calls == [PAGE_OBJECT_NAME]

def test_the_gear_does_nothing_without_an_iface(qgs_app):
    DataSourceDialog(None, None).settings_button.click()  # must not raise
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PYTEST tests/qgis/test_settings_page.py -q -p no:cacheprovider --no-cov -k gear`
Expected: FAIL — `AttributeError: 'DataSourceDialog' object has no attribute 'settings_button'`

- [ ] **Step 3: Add the button and its slot**

A `QToolButton` with the gear glyph as its text and the tooltip "Settings for downloads: format, folder, compression", added to `button_layout` before `ok_button` with a `addStretch()` between them so it sits at the left. `open_settings()` returns immediately when `self.iface` is `None` — the dialog is constructed with `iface=None` in tests — and otherwise calls `self.iface.showOptionsDialog(self, PAGE_OBJECT_NAME)`.

- [ ] **Step 4: Run the dialog suites to verify they pass and nothing shifted**

Run: `$PYTEST tests/qgis/test_settings_page.py tests/qgis/test_dialog.py tests/qgis/test_dialog_lifecycle.py -q -p no:cacheprovider --no-cov`
Expected: PASS, apart from `test_dialog.py::test_building_parts_checkbox`, which fails on `main` on this Mac for the `QgsSettings` reason in Global Constraints. Confirm against a `main` baseline if anything else fails: the dialog has a width invariant (#204) that a new button in the bottom row can push.

- [ ] **Step 5: Commit**

```bash
git add gpq_downloader/gui/dialog.py tests/qgis/test_settings_page.py
git commit -m "Open the settings from a gear beside the dialog's OK"
```

---

### Task 6: The plugin honours the format, folder and write settings

**Files:**
- Modify: `gpq_downloader/plugin.py` (`handle_dialog_accepted` lines 240–285, `process_download_queue`)
- Modify: `tests/qgis/test_plugin.py` (the `getSaveFileName` stub at line 104)
- Test: `tests/qgis/test_plugin_settings.py`

**Interfaces:**
- Consumes: `settings_store.load_download_settings()`, `load_choice`, `load_flag`, `load_text`, and the keys from Task 3; `core.output_formats`; the `DownloadJob` fields from Task 2.
- Produces:
  - `plugin.starting_dir() -> Path` — the folder the next save dialog opens in: the remembered folder when setting 3 is on and one is stored, otherwise the configured folder, created on demand; `~/Downloads` with a message-bar warning when neither can be created.
  - `plugin.download_dir` keeps its meaning as the fallback folder and is still created in `__init__`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/qgis/test_plugin_settings.py
from pathlib import Path
from unittest.mock import patch
import pytest
from gpq_downloader.gui import settings_store
from gpq_downloader.plugin import QgisPluginGeoParquet
# clean_settings: import or re-create the fixture used in test_settings_page.py

def test_the_configured_folder_and_format_set_the_default_save_path(clean_settings, mock_iface, tmp_path, accepted):
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(tmp_path))
    settings_store.save_value(settings_store.OUTPUT_FORMAT, "GeoPackage")
    path, initial_filter = accepted(QgisPluginGeoParquet(mock_iface))
    assert Path(path).parent == tmp_path
    assert Path(path).suffix == ".gpkg"
    assert initial_filter == "GeoPackage (*.gpkg)"

def test_a_configured_folder_that_does_not_exist_yet_is_created(clean_settings, mock_iface, tmp_path, accepted):
    wanted = tmp_path / "new" / "place"
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(wanted))
    path, _ = accepted(QgisPluginGeoParquet(mock_iface))
    assert wanted.is_dir() and Path(path).parent == wanted

def test_a_folder_that_cannot_be_created_falls_back_and_warns(clean_settings, mock_iface, tmp_path, accepted):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(blocker / "under"))
    plugin = QgisPluginGeoParquet(mock_iface)
    path, _ = accepted(plugin)
    assert Path(path).parent == plugin.download_dir
    assert mock_iface.messages  # the message bar was told; see MockIface

def test_the_folder_last_saved_to_wins_when_remembering_is_on(clean_settings, mock_iface, tmp_path, accepted):
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(tmp_path / "configured"))
    settings_store.save_value(settings_store.LAST_OUTPUT_FOLDER, str(tmp_path))
    path, _ = accepted(QgisPluginGeoParquet(mock_iface))
    assert Path(path).parent == tmp_path

def test_the_configured_folder_wins_when_remembering_is_off(clean_settings, mock_iface, tmp_path, accepted):
    configured = tmp_path / "configured"
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(configured))
    settings_store.save_value(settings_store.LAST_OUTPUT_FOLDER, str(tmp_path))
    settings_store.save_value(settings_store.REMEMBER_LAST_FOLDER, False)
    path, _ = accepted(QgisPluginGeoParquet(mock_iface))
    assert Path(path).parent == configured

def test_saving_remembers_the_folder_that_was_used(clean_settings, mock_iface, tmp_path, accepted):
    accepted(QgisPluginGeoParquet(mock_iface), chosen=str(tmp_path / "picked" / "x.parquet"))
    assert settings_store.load_text(settings_store.LAST_OUTPUT_FOLDER) == str(tmp_path / "picked")

def test_every_job_in_one_batch_carries_the_settings_as_they_were_at_enqueue(clean_settings, mock_iface, tmp_path, accepted):
    settings_store.save_value(settings_store.COMPRESSION_CODEC, "SNAPPY")
    settings_store.save_value(settings_store.COMPRESSION_LEVEL, 9)
    settings_store.save_value(settings_store.SPATIAL_SORT, False)
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.process_download_queue([("https://e.com/a.parquet", str(tmp_path / "a.parquet"))], None)
    job = plugin.downloads.queue.active or plugin.downloads.queue.pending[0]
    assert (job.compression_codec, job.compression_level, job.spatial_sort) == ("SNAPPY", 9, False)
```

Both files use the `clean_settings` fixture: move it from `test_settings_page.py` into `tests/qgis/conftest.py` as part of this task rather than writing it twice.

The `accepted` fixture drives one `handle_dialog_accepted()` with a `MagicMock` dialog, patching `gpq_downloader.plugin.QFileDialog.getSaveFileName` to record `(directory, initialFilter)` and return `chosen` or the offered path; model it on `test_plugin.py::test_plugin_queues_buildings_and_building_parts_separately`, and patch `process_download_queue` where the test does not need it. `MockIface` has no `messageBar()` at all today, so this task adds one: a stub whose `pushMessage(*args)` appends to `mock_iface.messages`, plus the `createMessage`/`pushWidget`/`popWidget` members `check_duckdb()` and `install_duckdb()` already call, so no existing test starts going through a `MagicMock` instead.

- [ ] **Step 2: Run to verify they fail**

Run: `$PYTEST tests/qgis/test_plugin_settings.py -q -p no:cacheprovider --no-cov`
Expected: FAIL — the save path is still under `~/Downloads` and still `.parquet`

- [ ] **Step 3: Implement `starting_dir()` and use the format in the save dialog**

`starting_dir()` resolves as the Interfaces block describes; the warning goes through `self.iface.messageBar().pushMessage(...)` with `message_level("Warning")`, as the DuckDB messages already do. In the save-dialog loop, build the filename with `output_formats.format_for(...).extension` instead of the literal `".parquet"`, pass `output_formats.dialog_filters()` as the filter and the configured format's `dialog_filter` as `initialFilter`, and start from `self.starting_dir()`. After the loop collects a path, store `str(Path(output_file).parent)` under `LAST_OUTPUT_FOLDER`.

- [ ] **Step 4: Read the download settings once per batch in `process_download_queue`**

Call `settings_store.load_download_settings()` once before the comprehension and spread it onto every `DownloadJob` — one read per batch is what makes the snapshot a snapshot.

- [ ] **Step 5: Update the save-dialog stub in `tests/qgis/test_plugin.py`**

`mock_save_dialog.side_effect = lambda parent, title, path, filters: ...` takes four positional arguments and will not take the new `initialFilter`. Change it to `lambda *args, **kwargs: (args[2], "GeoParquet (*.parquet)")`.

- [ ] **Step 6: Run the plugin suites to verify they pass**

Run: `$PYTEST tests/qgis/test_plugin_settings.py tests/qgis/test_plugin.py tests/qgis/test_integration.py tests/qgis/test_dialog_settings.py -q -p no:cacheprovider --no-cov`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gpq_downloader/plugin.py tests/qgis/test_plugin_settings.py tests/qgis/test_plugin.py tests/qgis/conftest.py
git commit -m "Honour the output format, folder and write settings"
```

---

### Task 7: The logging level (#93)

**Files:**
- Modify: `gpq_downloader/logger.py`
- Modify: `gpq_downloader/plugin.py` (`initGui`)
- Modify: `gpq_downloader/gui/settings_page.py` (`apply`)
- Modify: `gpq_downloader/qgis_io/worker.py:119` (the one commented-out statement)
- Test: `tests/qgis/test_logger.py`

**Interfaces:**
- Consumes: `settings_store.LOG_LEVEL`, `LOG_LEVELS`, `DEFAULT_LOG_LEVEL` from Task 3; `SettingsPage.log_combo` from Task 4.
- Produces:
  - `logger.set_level(name: str) -> None` — sets the threshold and `_core_logger`'s level together; an unknown name sets Info.
  - `logger.current_level() -> str`
  - `logger.debug(message: str) -> None` — the new rung below Info, emitted to the QGIS log at `Qgis.MessageLevel.Info` because QGIS has no Debug level, and suppressed unless the threshold is Debug.
  - `logger.log(message, level_in=0)` keeps its signature and its 0/1/2 → Info/Warning/Critical mapping; it now returns without logging when that level is below the threshold.
- `logger.py` must not import `gui` (see Global Constraints): the threshold is module state that `plugin.py` and the page push into it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/qgis/test_logger.py (added to the existing tests)
import logging
import pytest
from gpq_downloader import logger

@pytest.fixture
def restore_level():
    before = logger.current_level()
    yield
    logger.set_level(before)

@pytest.fixture
def logged(monkeypatch):
    """Every (message, level) that reaches QgsMessageLog."""
    seen = []
    monkeypatch.setattr(
        logger, "QgsMessageLog",
        type("Stub", (), {"logMessage": staticmethod(lambda m, t, lvl: seen.append((m, lvl)))}),
    )
    return seen

def test_info_is_the_level_when_nothing_has_set_one():
    assert logger.current_level() == "Info"

def test_debug_is_silent_at_info(restore_level, logged):
    logger.set_level("Info")
    logger.debug("quiet")
    assert logged == []

def test_debug_speaks_at_debug(restore_level, logged):
    logger.set_level("Debug")
    logger.debug("loud")
    assert [m for m, _ in logged] == ["loud"]

def test_warning_turns_the_info_chatter_off_but_keeps_warnings(restore_level, logged):
    logger.set_level("Warning")
    logger.log("chatter")
    logger.log("careful", 1)
    logger.log("broken", 2)
    assert [m for m, _ in logged] == ["careful", "broken"]

def test_the_core_logger_moves_with_it(restore_level):
    logger.set_level("Debug")
    assert logger._core_logger.level == logging.DEBUG
    logger.set_level("Warning")
    assert logger._core_logger.level == logging.WARNING

def test_an_unknown_level_reads_as_info(restore_level):
    logger.set_level("Chatty")
    assert logger.current_level() == "Info"
```

Plus one test each, in `tests/qgis/test_settings_page.py`, that `SettingsPage.apply()` leaves `logger.current_level()` equal to the combo's text, and that `plugin.initGui()` applies the stored level — both wrapped in the `restore_level` fixture so a test does not leave the level changed for the rest of the run.

- [ ] **Step 2: Run to verify they fail**

Run: `$PYTEST tests/qgis/test_logger.py -q -p no:cacheprovider --no-cov`
Expected: FAIL — `AttributeError: module 'gpq_downloader.logger' has no attribute 'current_level'`

- [ ] **Step 3: Add the threshold, `set_level()` and `debug()` to `logger.py`**

A module-level rank map `{"Debug": 0, "Info": 1, "Warning": 2, "Critical": 3}` and a module-level current name defaulting to `"Info"`. `set_level()` also calls `_core_logger.setLevel()` with the matching `logging` constant (Critical maps to `logging.ERROR`, which is what `_MessageLogHandler` already treats as Critical). Keep `_core_logger`'s existing reload-safe handler block as it is.

- [ ] **Step 4: Apply the stored level from both places**

`plugin.initGui()` calls `logger.set_level(settings_store.load_choice(settings_store.LOG_LEVEL, settings_store.LOG_LEVELS, settings_store.DEFAULT_LOG_LEVEL))` before `check_duckdb(startup=True)`, so startup messages obey it. `SettingsPage.apply()` calls `logger.set_level(self.log_combo.currentText())` after saving, which is what makes the change take effect without a restart.

- [ ] **Step 5: Turn the one commented-out statement into a Debug call**

`qgis_io/worker.py:119` becomes `logger.debug(f"Full validation_results at start of run: {self.validation_results}")`. This is the whole of #93's "uncomment the log statements" half — the Phase-2 refactor removed the rest.

- [ ] **Step 6: Run the logger, page and worker tests to verify they pass**

Run: `$PYTEST tests/qgis/test_logger.py tests/qgis/test_settings_page.py tests/core/test_worker_golden_sql.py -q -p no:cacheprovider --no-cov`
Expected: PASS

- [ ] **Step 7: Run the whole suite and the contracts, and compare with a `main` baseline**

Run: `$PYTEST -q` and `python -m lint_imports`
Expected: both contracts KEPT, and the failure *set* no larger than the `main` baseline's (see Global Constraints; the DuckDB-extension tests cannot run on this Mac at all, and CI is authoritative for them).

- [ ] **Step 8: Commit**

```bash
git add gpq_downloader/logger.py gpq_downloader/plugin.py gpq_downloader/gui/settings_page.py gpq_downloader/qgis_io/worker.py tests/qgis/test_logger.py tests/qgis/test_settings_page.py
git commit -m "Make the logging level a setting, with a Debug rung (#93)"
```

---

## Not in this plan

From the spec's non-goals, so a reviewer does not look for them: user-defined presets (#84), S3 credentials (#81), the GeoParquet best-practices rewrite, struct flattening (#79), and pinning an Overture release. The CHANGELOG entry and the version bump belong to the release, not here.

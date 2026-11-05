# Settings panel — design

Issue #1, milestone v0.11. Written 2026-11-02.

## Intent

Everything the plugin decides about *how* a download is written is currently
hardcoded, in one line of `qgis_io/worker.py`. People who download the same
data repeatedly have no way to say "always GeoPackage", "put it here", or
"stop spending thirteen seconds on compression I don't need". This gives them
one place to say it.

The panel is also the lever for a default we now know is wrong. The plugin
writes Parquet at ZSTD level 22. Measured on 445,794 real Overture buildings
(Calgary extent, release 2026-10-21.0, DuckDB 1.5.3):

| level | size | time |
|---|---|---|
| 1 | 65.15 MB | 0.51s |
| 9 | 51.23 MB | 1.28s |
| 15 | 48.23 MB | 5.17s |
| 19 | 42.60 MB | 11.69s |
| 22 | 42.56 MB | 13.30s |

Level 22 buys 0.04 MB over level 19 for 1.6 extra seconds. It is strictly
wasteful, and nobody chose it deliberately for this workload — it arrived
from the SQL snippet in #78. **The new default is 15**, chosen by the owner:
2.6× faster than 22 for 13% more size.

## Goals

- One settings page holding the eight settings below, with sensible defaults
  that make the plugin behave better than it does today for someone who never
  opens it.
- The ZSTD level default moves 22 → 15 for everyone, including people who
  never open the panel.
- Changing a setting takes effect on the next download, without reopening the
  dialog or restarting QGIS.
- Settings persist through `gui/settings_store.py`, the one module that talks
  to `QgsSettings`, keeping the existing `Section.Plugins` convention.

### How the download settings reach the worker

`pyproject.toml` enforces `qgis_io does not import the GUI`, so
`qgis_io/worker.py` **cannot** import `gui/settings_store.py` — directly or
through `logger.py`. The contract has no `allow_indirect_imports`, so a chain
fails CI just as a direct import does.

Therefore settings 4–7 are **read in `plugin.py` when a download is
enqueued** and passed through `DownloadJob` into `Worker`, alongside the
other per-job values already carried there. This keeps the contract intact
and keeps `tests/core/test_worker_golden_sql.py` pure: those tests drive the
real `Worker` headlessly against stand-ins in `tests/core/core_support.py`
that have no `QgsSettings`, and a worker that read settings for itself would
drag a settings stand-in through the whole golden-SQL matrix.

A queued batch **snapshots its settings at enqueue**. Changing the
compression level while five downloads are queued must not produce five files
written three different ways; the change applies to the next batch. This is
the builder's reading of "takes effect on the next download" and is a
deliberate narrowing of it.

## Non-goals

These were considered for this panel and deliberately left out. Each is its
own piece of work and the panel can gain it later.

- **User-defined presets (#84)** — a whole editor UI, not a stored value.
- **S3 / remote credentials (#81)** — needs QGIS auth-manager integration.
- **GeoParquet best-practices rewrite** (bbox covering metadata) — needs
  pyarrow, which a QGIS plugin cannot ship.
- **Flattening GeoParquet structs to JSON (#79)** — changes output schemas,
  so it is a data decision rather than a preference.
- **Pinning an Overture release** — proposed and rejected by the owner:
  Overture deletes old releases, so a pinned release becomes a broken
  download rather than a stable one.

## Anti-goals

What would make this a failure even if it worked:

- A panel that silently changes what existing downloads produce beyond the
  compression default. The format, folder and sort defaults must reproduce
  today's behaviour for someone who never opens the panel.
- A settings read that reaches `QgsSettings` from `core/` or from
  `qgis_io/`. Both are enforced by import-linter contracts; a settings value
  arrives in either layer as a function argument or not at all.
- Settings that are only honoured on a fresh QGIS session.

## Constraints

- **Qt5 and Qt6 both supported.** Every Qt enum fully scoped
  (`QDialogButtonBox.StandardButton.Ok`, never `QDialogButtonBox.Ok`). CI runs
  QGIS 3.34/Qt5 and QGIS 4.2/Qt6 and v0.9.3 was cut specifically to fix this
  class of break.
- **Layering.** `core/` Qt-free and QGIS-free (import-linter contract),
  `qgis_io/` for workers and QGIS I/O, `gui/` for widgets, thin `plugin.py`.
- **`gui/settings_store.py` is the only module that constructs `QgsSettings`.**
  New keys and loaders go there, following the existing shape: one constant
  per key, a `load_*` helper per type.
- **DuckDB: `COMPRESSION_LEVEL` is only valid with ZSTD.** Verified on DuckDB
  1.5.3: passing it with SNAPPY gives `Binder Error: Compression level is only
  supported for the ZSTD compression codec`. The COPY builder must omit the
  clause entirely for other codecs, or every non-ZSTD download breaks. This is
  the single most likely way to get this feature wrong.

## Where it lives

Decided by the owner: a **QGIS Options page**, registered through
`QgsOptionsWidgetFactory` so it appears under *Settings → Options →
GeoParquet Downloader*, where QGIS users already look for settings — **plus a
gear button in the download dialog** that opens that page directly, so the
settings are reachable from where people are when they care about them.

The plugin currently registers only a toolbar icon and has no menu entry at
all; `portolan-registry-qgis`, the architectural prior art for this plugin,
has no settings panel to copy. Both the factory registration and its teardown
in `unload()` are new ground here.

## The settings

Keys live under the existing `gpq_downloader/` prefix in
`QgsSettings.Section.Plugins`.

| # | Setting | Type | Default | Read by |
|---|---|---|---|---|
| 1 | Output format | choice | GeoParquet | `plugin.py` save dialog |
| 2 | Output folder | path | `~/Downloads` | `plugin.py` save dialog |
| 3 | Remember last folder used | bool | **on** | `plugin.py` save dialog |
| 4 | Compression codec | choice | ZSTD | `qgis_io/worker.py` COPY |
| 5 | Compression level | int 1–22 | **15** | `qgis_io/worker.py` COPY |
| 6 | Row group size (advanced) | int | DuckDB default | `qgis_io/worker.py` COPY |
| 7 | Spatial sort | bool | on | `qgis_io/worker.py` COPY |
| 8 | Logging level (#93) | choice | Info | `logger.py` |

Notes per setting:

1. **Output format.** Today `plugin.py` always builds a `.parquet` filename
   and shows the filter list GeoParquet / DuckDB / GeoPackage / FlatGeobuf /
   GeoJSON. The setting changes the default extension and which filter is
   preselected. It does not remove any option from the dialog.
2. **Output folder.** `plugin.py:50` sets `download_dir = Path.home() /
   "Downloads"` once in `__init__` and never updates it. The setting replaces
   that constant, and is **read per save dialog rather than once at plugin
   load**. *Builder's call:* a configured folder that is missing is created on
   demand; one that cannot be created (unmounted drive, no permission) falls
   back to `~/Downloads` with a message-bar warning. Reading it at save time
   rather than in `initGui()` is what keeps a stale saved path from breaking
   plugin load entirely.
3. **Remember last folder used.** Default **on**, decided by the owner. The
   configured folder is the starting point; the last folder actually saved to
   wins thereafter. This does change behaviour for existing users — today
   every save starts in `~/Downloads` — but in the direction the owner already
   believed the plugin worked (#1: "I think we do default to the last save
   location"), so it corrects a surprise rather than introducing one. Off,
   every save starts in the configured folder, which is today's behaviour.
4. **Compression codec.** ZSTD, SNAPPY, GZIP, BROTLI, LZ4, UNCOMPRESSED — all
   verified accepted by DuckDB **1.5.3** for Parquet. Parquet only; the
   GDAL-driver formats take no compression options.
   *Builder's call:* the plugin supports DuckDB 1.1.0+ (`duckdb>=1.1.0,<2.0`),
   and the codec list was not verified against 1.1/1.2 — BROTLI and LZ4 are
   the likely gaps. Rather than gate the list by version, the COPY builder
   stays pure string logic in `core/` and the worker catches the DuckDB error
   and reports it as "this DuckDB does not support <codec>; choose another in
   Settings". A version-gated list is the fallback if that proves common.
5. **Compression level.** ZSTD only (see Constraints). The control is disabled
   or hidden unless the codec is ZSTD. Range 1–22 in the UI; DuckDB itself
   accepts −131072..22 but nothing below 1 is useful here.
6. **Row group size.** Advanced, grouped with compression as the owner asked.
   `ROW_GROUP_SIZE` is accepted by DuckDB for Parquet. #78 used 15000.
   Leaving it unset must emit no clause at all, so DuckDB's own default
   applies and today's output is unchanged.
7. **Spatial sort.** The Hilbert `ORDER BY` at `worker.py:395` is currently
   unconditional **and applies to every output format, not just Parquet** —
   turning it off drops the `ORDER BY` and its `WITH bbox` CTE for all of them.
   Note the gap against #1's wording: rows pass through a temp table and a
   parallel COPY, so dropping the clause makes the output order **unspecified**
   rather than guaranteed to match the source. "Do not deliberately reorder" is
   what this delivers; preserving source order exactly would need more.
8. **Logging level (#93).** One selector — Debug / Info / Warning / Critical,
   default Info — decided by the owner. It does two jobs with one control:
   Debug adds detail normally suppressed, Warning turns down the Info chatter
   the plugin already emits. Default Info leaves today's output unchanged.

   `logger.py` currently maps 0/1/2 to Info/Warning/Critical, so this needs a
   **new Debug rung below Info**, not just a threshold. `core/` logs through
   the standard `logging` module into `_MessageLogHandler`, so the level must
   apply to both paths — set it on `_core_logger` as well as checking it in
   `log()`.

   Note on #93's other half: it also asks to "uncomment all the log statements
   that are commented out". The Phase-2 refactor removed nearly all of them —
   **one** survives, at `qgis_io/worker.py:119`. So that part of the issue is
   now a single line, not a sweep. New Debug-level calls are worth adding
   where the flow is genuinely hard to follow, but that is a judgement call
   per site rather than a mechanical uncommenting.

## Testing

Matching the suite's existing shape: `tests/core/` headless for anything
Qt-free, `tests/qgis/` for widgets.

- The COPY-options builder is pure string logic and belongs in `core/`, tested
  without QGIS. It must have a test proving **no `COMPRESSION_LEVEL` clause is
  emitted for a non-ZSTD codec**, and one proving no `ROW_GROUP_SIZE` clause is
  emitted when the setting is unset.
- Defaults round-trip through `settings_store`, including the "never stored"
  case — `load_flag(key, default)` already exists for exactly this, because
  "never stored" must not read as "user unticked".
- `tests/qgis/test_dialog_settings.py` asserts the **exact set** of saved keys
  (`ALL_SAVED_KEYS`); new keys go in there or it fails.
- A test that the options page registers and unregisters cleanly, since a
  factory left registered on `unload()` leaks a page into QGIS.
- **Local test caveat:** on this machine `HOME=$(mktemp -d)` does *not*
  isolate `QgsSettings` — macOS serves preferences per user via cfprefsd, so
  tests see the developer's real QGIS profile. Any test calling
  `save_checkbox_states()` must snapshot and restore every key matching
  `plugins/gpq_downloader/` and `gpq_downloader/`.

## Left to the builder

- The widget layout within the options page, beyond grouping compression
  settings together with row group size under an advanced disclosure.
- Whether the COPY-options builder is a new `core/` module or joins an
  existing one.
- The exact key names, following the existing `gpq_downloader/<name>` style.
- How the level control is disabled for non-ZSTD codecs (greyed vs hidden).

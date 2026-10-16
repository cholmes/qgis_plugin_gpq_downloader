# Changelog

All notable changes to this plugin are documented in this file. Each release
section is generated from the merged pull requests by `scripts/release_notes.py`;
see [DEVELOPING.md](DEVELOPING.md#making-a-release).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Releases before 0.9.0 are described on the
[GitHub releases page](https://github.com/cholmes/qgis_plugin_gpq_downloader/releases).

## v0.9.0 (2026-10-16)

Overture downloads work again. Apologies to everyone for the delay on this not
working. Overture retired the custom release index we were reading, so we
switched to using their STAC to get the latest. The performance of Overture
downloads isn't great, but we aim to improve that for the next release.

**More ways to set the download area.**

This work was driven by a couple of great collaborators, first
[@GISAM](https://github.com/GISAM) in
[#118](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/118), with
additions from [@jcmwinterdal](https://github.com/jcmwinterdal) in
[#153](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/153):

- Type bbox coordinates.
- Drag a rectangle on the map.
- Draw a polygon.
- Select features from a layer.
- Use a layer's extent.

**Also new:**

- **Clip features to the area.** Cuts features at the edge of the area instead of
  downloading everything that overlaps it. Off by default.
- **Projected coordinate systems.** Files in a projected CRS, such as state plane
  feet, now download. Before, they returned nothing.
- **MinIO.** Use self-hosted, S3-compatible storage as a source.
- **All three Overture divisions types.** Previously only division areas.

**Main fixes:**

- The area filter kept a feature only if its lower-left corner was inside your
  window. It dropped features larger than the window, or crossing its west or
  south edge. A Seattle water download missed Lake Washington, and a small
  Boulder window returned no divisions.
- On QGIS 4, the dialog failed when you drew a bbox, drew a polygon, selected
  features, or opened the extent menu.
- GeoPackage output had the wrong coordinate system, or none. It now uses
  EPSG:4326.
- Cancel froze QGIS instead of stopping the query.
- Downloads failed when the geometry column was not named `geometry`.
- The Source Cooperative presets could not connect.
- Features stayed selected on the map after you closed the dialog.

**Before you upgrade:**

- Clip features to the area is off by default. Downloads behave as before until
  you turn it on. The setting is remembered.
- The plugin now loads and shows its toolbar button when DuckDB is not
  installed, and offers to install it.
- The VIDA buildings preset reads an S2-partitioned copy of the data, which is
  faster. If you saved the old URL by hand, use the preset instead.

**Thanks to:**

- [@GISAM](https://github.com/GISAM), who built the area-of-interest system that
  most of the new download-area tools come from
  ([#118](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/118)), and
  [@jcmwinterdal](https://github.com/jcmwinterdal), who added the bbox entry
  panel, the rectangle drag and the keyboard shortcuts
  ([#153](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/153)).
  Between them they are most of what is new here.
- [@mtravis](https://github.com/mtravis), who stopped features staying selected
  on the map after the dialog closed
  ([#159](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/159)), and
  who has been contributing to this plugin since the DuckDB install prompt and
  the export filename fixes.
- [@lubosmato](https://github.com/lubosmato), on a first contribution, who
  pointed the plugin at Overture's STAC catalog after Overture retired the
  release endpoint
  ([#169](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/169)).
  Without this fix, no Overture download works.
- [@ll3an](https://github.com/ll3an), also a first contribution, who sent two
  changes: Cancel now stops the running DuckDB query instead of freezing QGIS
  ([#171](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/171)), and
  the Source Cooperative presets work again, after an S3 certificate error on
  dotted bucket names, slow globbing of partitioned data, and a Roads path that
  had moved ([#170](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/170)).
- [@nils-grob](https://github.com/nils-grob), on a first contribution, who added
  MinIO support
  ([#122](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/122)).

Thanks for all your great contributions! Hope to see you in the future, and
we're welcoming [@mtravis](https://github.com/mtravis) as a co-maintainer for
the next release.

### Added

- Support all three Overture divisions boundary types by [@cholmes](https://github.com/cholmes) with [@Copilot](https://github.com/Copilot) in [#147](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/147)
- Add Area of Interest (AOI) selection tools by [@cholmes](https://github.com/cholmes) in [#156](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/156)
- Add MinIO URL support for dataset loading by [@nils-grob](https://github.com/nils-grob) in [#122](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/122)
- Support GeoParquet files in a projected CRS by [@cholmes](https://github.com/cholmes) in [#183](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/183)

### Changed

- Make the VIDA buildings preset fast with S2 cell pruning by [@cholmes](https://github.com/cholmes) in [#174](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/174)

### Fixed

- Fix Qt6 enum names that break the dialog and map tools on QGIS 4 by [@cholmes](https://github.com/cholmes) in [#172](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/172)
- Read Overture's STAC catalog to find the latest release by [@lubosmato](https://github.com/lubosmato) in [#169](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/169)
- Fix downloads from files whose geometry column is not called "geometry" by [@cholmes](https://github.com/cholmes) with [@Copilot](https://github.com/Copilot) in [#161](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/161)
- Clear feature selection on AOI dialog exit by [@mtravis](https://github.com/mtravis) in [#159](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/159)
- Fix Source Cooperative presets: S3 SSL error, slow partitioned globs, stale Roads path by [@ll3an](https://github.com/ll3an) in [#170](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/170)
- Make Cancel interrupt the running DuckDB query instead of freezing QGIS by [@ll3an](https://github.com/ll3an) in [#171](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/171)
- Fix the bbox filter, column detection, GeoPackage CRS and loading without DuckDB by [@cholmes](https://github.com/cholmes) in [#181](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/181)
- Fix the message bar and extent menu on QGIS 4 by [@cholmes](https://github.com/cholmes) in [#182](https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/182)

### New Contributors

- [@lubosmato](https://github.com/lubosmato) made their first contribution in https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/169
- [@ll3an](https://github.com/ll3an) made their first contribution in https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/170
- [@nils-grob](https://github.com/nils-grob) made their first contribution in https://github.com/cholmes/qgis_plugin_gpq_downloader/pull/122

_3 internal changes are not listed here; the full changelog has them._

**Full Changelog**: https://github.com/cholmes/qgis_plugin_gpq_downloader/compare/v0.8.6...v0.9.0

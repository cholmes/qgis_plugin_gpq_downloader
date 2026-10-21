"""The plugin's glue between QGIS and DuckDB.

- crs: QGIS-side CRS conversion (the query window, the data's own CRS)
- worker: the download and validation workers the plugin runs on a QThread,
  and the cancellable DuckDB connection they share
- duckdb_setup: finding DuckDB, or installing it into QGIS's Python
- layers: loading a finished download into the project

The code here moved from utils.py, duckdb_setup.py and plugin.py (#195);
utils.py and duckdb_setup.py re-export their old names until 1.0 (#198). It
may import qgis and gpq_downloader.core, but nothing in core may import it.

Nothing is imported here: classFactory() reaches duckdb_setup through this
package before DuckDB is known to be importable.
"""

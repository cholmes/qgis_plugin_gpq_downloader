"""Plugin logic that does not need QGIS.

- overture: partition pruning, from the extents in file names or Overture's STAC
- presets: loading data/presets.json
- plan: download planning (file names)
- sql: quoting for the SQL built as f-strings

They moved here from utils.py and plugin.py (#195); utils.py re-exports its old
names until 1.0 (#198). Core code logs through the standard logging module, and
the plugin's logger.py forwards those records to the QGIS message log.

Nothing in this package may import qgis, PyQt or osgeo, directly or through
another module. import-linter enforces that (see [tool.importlinter] in
pyproject.toml, run by `lint-imports`), so the code here stays testable on
plain Python without a QGIS install.
"""

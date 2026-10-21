"""Plugin logic that does not need QGIS.

Presets, download planning and Overture partition pruning will live here; the
extractions arrive in follow-up changes (#195).

Nothing in this package may import qgis, PyQt or osgeo, directly or through
another module. import-linter enforces that (see [tool.importlinter] in
pyproject.toml, run by `lint-imports`), so the code here stays testable on
plain Python without a QGIS install.
"""

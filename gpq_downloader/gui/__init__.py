"""The plugin's QGIS user interface.

- extent: the area of interest (map tools, draw and select, layer extent)
- settings_store: the QgsSettings keys the dialog and plugin remember

The map tools moved here from map_tools.py (#195), which re-exports their
old names until 1.0 (#198). Code here may import qgis and
gpq_downloader.core, but nothing in core may import it.
"""

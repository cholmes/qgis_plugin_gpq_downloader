"""The plugin's QGIS user interface.

- dialog: the data source dialog (sources, validation, remembered checkboxes)
- extent: the area of interest (map tools, draw and select, layer extent)
- settings_store: the QgsSettings keys the dialog and plugin remember

The code here moved from dialog.py and map_tools.py (#195), which re-export
their old names until 1.0 (#198). It may import qgis and gpq_downloader.core,
but nothing in core may import it.
"""

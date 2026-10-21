"""End-to-end smoke test: the plugin's own path from the dialog to a layer.

plugin.run() opens the dialog; a custom file:// URL of a local fixture file is
entered and OK is clicked. That runs the real ValidationWorker and then the
real Worker on their QThreads, with real DuckDB, against the map canvas
extent, and the Worker's load_layer signal adds the result to the project.
Only the save-file dialog and the message boxes are replaced, so nothing
waits for a click.

Runs in the QGIS CI containers (it needs QGIS, and DuckDB's spatial extension
for the GeoPackage export).
"""

import gc
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsProject, QgsRectangle, QgsSettings, QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication, QEvent

from gpq_downloader.dialog import DataSourceDialog
from gpq_downloader.plugin import QgisPluginGeoParquet

# Its polygons around (10, 10)-(11, 11): three overlap that window, one does not
DATA = os.path.join(os.path.dirname(__file__), "data", "geom_bbox_named.parquet")
CANVAS_EXTENT = QgsRectangle(10, 10, 11, 11)
TIMEOUT = 120


@pytest.fixture
def clean_settings(qgs_app):
    """No remembered dialog state (an AOI box left ticked would stop for a prompt)."""
    settings = QgsSettings()
    keys = [k for k in settings.allKeys() if k.startswith(("plugins/gpq_downloader/", "gpq_downloader/"))]
    saved = {k: settings.value(k) for k in keys}
    for key in keys:
        settings.remove(key)
    yield
    for key in [k for k in settings.allKeys() if k.startswith(("plugins/gpq_downloader/", "gpq_downloader/"))]:
        settings.remove(key)
    for key, value in saved.items():
        settings.setValue(key, value)


@pytest.fixture
def iface():
    iface = MagicMock()
    iface.mainWindow.return_value = None
    canvas = iface.mapCanvas.return_value
    canvas.extent.return_value = CANVAS_EXTENT
    canvas.mapSettings.return_value.destinationCrs.return_value = QgsCoordinateReferenceSystem("EPSG:4326")
    return iface


def wait_for(condition, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QgsApplication.processEvents()
        if condition():
            return True
        time.sleep(0.05)
    return condition()


def layers_from(path):
    return [
        layer for layer in QgsProject.instance().mapLayers().values()
        if os.path.normpath(layer.source().split("|")[0]) == os.path.normpath(path)
    ]


def test_canvas_extent_download_loads_a_layer(qgs_app, clean_settings, iface, tmp_path):
    output = str(tmp_path / "smoke.gpkg")
    dialogs = []

    def make_dialog(*args):
        dialogs.append(DataSourceDialog(*args))
        return dialogs[-1]

    plugin = QgisPluginGeoParquet(iface)
    with patch("gpq_downloader.plugin.DataSourceDialog", side_effect=make_dialog), \
            patch("gpq_downloader.plugin.QFileDialog.getSaveFileName",
                  return_value=(output, "GeoPackage (*.gpkg)")) as save_dialog, \
            patch("gpq_downloader.plugin.QMessageBox") as plugin_boxes, \
            patch("gpq_downloader.qgis_io.layers.QMessageBox", plugin_boxes), \
            patch("gpq_downloader.gui.dialog.QMessageBox") as dialog_boxes:
        plugin.run()
        assert len(dialogs) == 1
        dialog = dialogs[0]
        try:
            dialog.custom_radio.setChecked(True)
            dialog.url_input.setText("file://" + DATA)
            assert not dialog.extent_group.isChecked()  # the canvas extent, not an AOI

            dialog.ok_button.click()

            # Done when the layer is in the project and the download thread is
            # cleaned up, or as soon as a message box would have stopped the user
            loaded = wait_for(lambda: (layers_from(output) and plugin.worker is None)
                              or plugin_boxes.method_calls or dialog_boxes.method_calls)
            assert not plugin_boxes.method_calls, plugin_boxes.method_calls
            assert not dialog_boxes.method_calls, dialog_boxes.method_calls
            assert loaded, "no layer was loaded"

            # The default file name is what the custom-URL path offers
            save_dialog.assert_called_once()
            assert os.path.basename(save_dialog.call_args[0][2]).startswith("geom_bbox_named_")

            assert os.path.exists(output)
            exported = QgsVectorLayer(output, "check", "ogr")
            assert exported.isValid()
            assert exported.featureCount() == 3

            (layer,) = layers_from(output)
            assert layer.isValid()
            assert layer.name() == "smoke"
            assert layer.featureCount() == 3
            assert layer.crs().authid() == "EPSG:4326"
        finally:
            for layer in layers_from(output):
                QgsProject.instance().removeMapLayer(layer.id())
            plugin.cleanup_thread()
            # The plugin keeps its progress dialog, and the data source dialog
            # is held by its own signal connections. Left to the garbage
            # collector they are destroyed after QgsApplication.exitQgis(),
            # which crashes the interpreter on its way out (both Qt 5 and 6).
            dialog.close()
            if hasattr(plugin, "progress_dialog"):
                plugin.progress_dialog.close()
                plugin.progress_dialog.deleteLater()
                del plugin.progress_dialog  # update_progress() checks for the attribute
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            gc.collect()

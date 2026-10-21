"""Loading a finished download into the QGIS project.

load_layer() moved here from QgisPluginGeoParquet.load_layer in plugin.py
(#195), which now calls it with the plugin's iface.
"""

from pathlib import Path

from qgis.PyQt.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout
from qgis.core import QgsProject, QgsVectorLayer


def load_layer(iface, output_file):
    """Load the layer into QGIS if GeoParquet is supported"""
    if output_file.lower().endswith(".parquet"):
        # Try to create a test layer to check GeoParquet support
        test_layer = QgsVectorLayer(output_file, "test", "ogr")
        if not test_layer.isValid():
            dialog = QDialog(iface.mainWindow())
            dialog.setWindowTitle("GeoParquet Support Not Available")
            dialog.setMinimumWidth(400)

            layout = QVBoxLayout()

            message = QLabel(
                "Data has been successfully saved to GeoParquet file.\n\n"
                "Note: Your current QGIS installation does not support reading GeoParquet files directly. You can select GeoPackage for your output format to view immediately.\n\n"
                "To view GeoParquet files in QGIS, you'll need to install QGIS with GDAL 3.8 "
                "or higher with 'libgdal-arrow-parquet'. You can find instructions at:"
            )
            message.setWordWrap(True)
            layout.addWidget(message)

            link = QLabel()
            link.setText(
                '<a href="https://github.com/cholmes/qgis_plugin_gpq_downloader/wiki/Installing-GeoParquet-Support-in-QGIS">Installing GeoParquet Support in QGIS</a>'
            )
            link.setOpenExternalLinks(True)
            layout.addWidget(link)

            button_box = QPushButton("OK")
            button_box.clicked.connect(dialog.accept)
            layout.addWidget(button_box)

            dialog.setLayout(layout)
            dialog.exec()
            return

    layer_name = Path(output_file).stem  # Get filename without extension
    # Create the layer
    layer = QgsVectorLayer(output_file, layer_name, "ogr")
    if not layer.isValid():
        QMessageBox.critical(
            iface.mainWindow(),
            "Error",
            f"Failed to load the layer from {output_file}",
        )
        return
    # Add the layer to the QGIS project
    QgsProject.instance().addMapLayer(layer)

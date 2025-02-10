from qgis.PyQt.QtWidgets import (
    QAction,
    QFileDialog,
    QMessageBox,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QProgressDialog,
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt, QThread
from qgis.core import QgsProject, QgsVectorLayer, QgsSettings
import os
import datetime
from pathlib import Path

from gpq_downloader.dialog import DataSourceDialog
from gpq_downloader.utils import Worker


class QgisPluginGeoParquet:
    def __init__(self, iface):
        self.iface = iface
        self.worker = None
        self.worker_thread = None
        self.action = None
        self.output_file = None
        # Create a default downloads directory in user's home directory
        self.download_dir = Path.home() / "Downloads"
        # Create the directory if it doesn't exist
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def initGui(self):
        # Create the action with the icon and tooltip
        base_path = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_path, "icons", "parquet-download.svg")
        self.action = QAction(
            QIcon(icon_path), "Download GeoParquet Data", self.iface.mainWindow()
        )
        self.action.setToolTip("Download GeoParquet Data")
        self.action.triggered.connect(self.run)

        # Add the actions to the toolbar
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        # Clean up worker and thread when plugin is unloaded
        self.cleanup_thread()
        # Remove all actions from the toolbar
        self.iface.removeToolBarIcon(self.action)

    def run(self, default_source=None):
        # Reset any existing worker
        self.worker = None
        self.worker_thread = None

        dialog = DataSourceDialog(self.iface.mainWindow(), self.iface)

        selected_name = QgsSettings().value(
            "gpq_downloader/radio_selection", section=QgsSettings.Plugins
        )
        for button in [
            dialog.overture_radio,
            dialog.sourcecoop_radio,
            dialog.other_radio,
            dialog.custom_radio,
        ]:
            if button.text() == selected_name:
                button.setChecked(True)
        if not selected_name:
            dialog.overture_radio.setChecked(True)

        # Connect validation complete signal to handle the result
        dialog.validation_complete.connect(
            lambda success, message, results: self.handle_validation_complete(
                success,
                message,
                results,
                dialog.get_url(),
                self.iface.mapCanvas().extent(),
                dialog,
            )
        )

        if dialog.exec_() != QDialog.Accepted:
            return

    def handle_validation_complete(
        self, success, message, validation_results, url, extent, dialog
    ):
        """Handle validation completion and start download if successful."""
        if success:
            # Get current date for filename
            current_date = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

            # Generate the default filename based on dialog selection
            if dialog.overture_radio.isChecked():
                theme = dialog.overture_combo.currentText().lower()
                if theme == "base":
                    subtype = dialog.base_subtype_combo.currentText()
                    filename = f"overture_base_{subtype}_{current_date}.parquet"
                else:
                    filename = f"overture_{theme}_{current_date}.parquet"

            elif dialog.sourcecoop_radio.isChecked():
                selection = dialog.sourcecoop_combo.currentText()
                # Convert display name to safe filename format
                safe_name = selection.lower().replace(" ", "_").replace("/", "_")
                filename = f"sourcecoop_{safe_name}_{current_date}.parquet"

            else:  # custom URL
                filename = f"custom_download_{current_date}.parquet"

            default_save_path = str(self.download_dir / filename)

            # Show save file dialog
            output_file, selected_filter = QFileDialog.getSaveFileName(
                self.iface.mainWindow(),
                "Save Data",
                default_save_path,
                "GeoParquet (*.parquet);;DuckDB Database (*.duckdb);;GeoPackage (*.gpkg);;FlatGeobuf (*.fgb);;GeoJSON (*.geojson)",
            )

            if output_file:
                self.output_file = output_file
                self.download_and_save(url, extent, output_file, validation_results)
        else:
            QMessageBox.warning(self.iface.mainWindow(), "Validation Error", message)

    def download_and_save(self, dataset_url, extent, output_file, validation_results):
        # Ensure we start with a fresh worker
        self.cleanup_thread()

        # Create progress dialog
        self.progress_dialog = self.create_progress_dialog("Downloading Data")

        # Create worker with validation results
        self.worker, self.worker_thread = self.setup_worker(
            dataset_url, extent, output_file, validation_results
        )

        # Show the progress dialog and start the thread
        self.progress_dialog.show()
        self.worker_thread.start()

    def handle_error(self, message):
        self.progress_dialog.close()
        QMessageBox.critical(self.iface.mainWindow(), "Error", message)

    def update_progress(self, message):
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.setLabelText(message)

    def cancel_download(self):
        if self.worker:
            self.worker.kill()
        self.cleanup_thread()

    def cleanup_thread(self):
        if self.worker_thread is not None:
            if self.worker:
                self.worker.kill()
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None
            self.worker = None
        if hasattr(self, "progress_dialog"):
            self.progress_dialog.close()

    def load_layer(self, output_file):
        """Load the layer into QGIS if GeoParquet is supported"""
        if output_file.lower().endswith(".parquet"):
            # Try to create a test layer to check GeoParquet support
            test_layer = QgsVectorLayer(output_file, "test", "ogr")
            if not test_layer.isValid():
                dialog = QDialog(self.iface.mainWindow())
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
                dialog.exec_()
                return

        layer_name = Path(output_file).stem  # Get filename without extension
        # Create the layer
        layer = QgsVectorLayer(output_file, layer_name, "ogr")
        if not layer.isValid():
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Error",
                f"Failed to load the layer from {output_file}",
            )
            return
        # Add the layer to the QGIS project
        QgsProject.instance().addMapLayer(layer)

    def show_info(self, message):
        """Show an information message to the user"""
        QMessageBox.information(self.iface.mainWindow(), "Success", message)

    def handle_large_file_warning(self, estimated_size):
        """Handle warning about large GeoJSON file size with a more streamlined UI"""
        # Store worker info before any cleanup
        if not hasattr(self, "worker") or self.worker is None:
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Error",
                "Download session lost. Please try again.",
            )
            return

        # Store all necessary info before any cleanup
        worker_info = {
            "dataset_url": self.worker.dataset_url,
            "extent": self.worker.extent,
            "iface": self.worker.iface,
            "validation_results": self.worker.validation_results,
            "output_file": self.output_file,
            "size_warning_accepted": False,
        }

        # Now we can safely close the progress dialog
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.close()

        dialog = QDialog(self.iface.mainWindow())
        dialog.setWindowTitle("Large File Warning")
        dialog.setMinimumWidth(400)
        layout = QVBoxLayout()

        # Format size for display
        if estimated_size >= 1024:
            size_str = f"{estimated_size / 1024:.2f} GB"
        else:
            size_str = f"{estimated_size:.0f} MB"

        msg = QLabel(
            f"The estimated file size is {size_str}. Large GeoJSON files can be slow to process and load.\n\n"
        )
        msg.setWordWrap(True)
        layout.addWidget(msg)

        # Format selection
        format_group = QVBoxLayout()
        recommended_label = QLabel(
            "Alternative formats (recommended for large datasets):"
        )
        format_group.addWidget(recommended_label)

        # Create horizontal layout for dropdown and button
        format_row = QHBoxLayout()

        # Create dropdown instead of radio buttons
        format_combo = QComboBox()
        format_combo.addItems(
            ["FlatGeobuf (*.fgb)", "GeoPackage (*.gpkg)", "GeoParquet (*.parquet)"]
        )
        format_row.addWidget(format_combo)

        # Add Save As button next to dropdown
        save_button = QPushButton("Save As...")
        format_row.addWidget(save_button)

        format_group.addLayout(format_row)
        layout.addLayout(format_group)

        # Buttons (now just Proceed and Cancel)
        button_box = QHBoxLayout()
        proceed_button = QPushButton("Proceed with GeoJSON anyway")
        cancel_button = QPushButton("Cancel")
        button_box.addWidget(proceed_button)
        button_box.addWidget(cancel_button)
        layout.addLayout(button_box)

        dialog.setLayout(layout)

        # Connect buttons
        cancel_button.clicked.connect(dialog.reject)
        save_button.clicked.connect(
            lambda: dialog.done(1)
        )  # Custom return code for Save As
        proceed_button.clicked.connect(
            lambda: dialog.done(2)
        )  # Custom return code for Proceed

        while True:
            result = dialog.exec_()
            if result == 1:  # Save As
                # Get selected format
                selected_format = format_combo.currentText()
                extension = selected_format.split("*")[1].rstrip(")")

                # Update output file path with new extension
                new_output_file = (
                    os.path.splitext(worker_info["output_file"])[0] + extension
                )

                # Show save file dialog with selected format
                output_file, _ = QFileDialog.getSaveFileName(
                    self.iface.mainWindow(),
                    "Save Data",
                    new_output_file,
                    selected_format,
                )

                if output_file:
                    # Create new progress dialog
                    self.progress_dialog = QProgressDialog(
                        "Starting download...", "Cancel", 0, 0, self.iface.mainWindow()
                    )
                    self.progress_dialog.setWindowTitle("Downloading Data")
                    self.progress_dialog.setWindowModality(Qt.NonModal)
                    self.progress_dialog.setMinimumDuration(0)

                    # Update output file
                    self.output_file = output_file

                    # Create new worker and thread
                    self.worker = Worker(
                        worker_info["dataset_url"],
                        worker_info["extent"],
                        output_file,
                        worker_info["iface"],
                        worker_info["validation_results"],
                    )
                    self.worker_thread = QThread()
                    self.worker.moveToThread(self.worker_thread)

                    # Connect signals
                    self.worker_thread.started.connect(self.worker.run)
                    self.worker.error.connect(self.handle_error)
                    self.worker.load_layer.connect(self.load_layer)
                    self.worker.info.connect(self.show_info)
                    self.worker.file_size_warning.connect(
                        self.handle_large_file_warning
                    )
                    self.worker.finished.connect(self.cleanup_thread)
                    self.worker.progress.connect(self.update_progress)
                    self.progress_dialog.canceled.connect(self.cancel_download)

                    # Show progress dialog and start thread
                    self.progress_dialog.show()
                    self.worker_thread.start()
                    return
                continue  # If Save As was cancelled, continue the loop

            elif result == 2:  # Proceed with GeoJSON
                # Create new progress dialog
                self.progress_dialog = QProgressDialog(
                    "Starting download...", "Cancel", 0, 0, self.iface.mainWindow()
                )
                self.progress_dialog.setWindowTitle("Downloading Data")
                self.progress_dialog.setWindowModality(Qt.NonModal)
                self.progress_dialog.setMinimumDuration(0)

                # Create new worker with original settings
                self.worker = Worker(
                    worker_info["dataset_url"],
                    worker_info["extent"],
                    worker_info["output_file"],
                    worker_info["iface"],
                    worker_info["validation_results"],
                )
                self.worker_thread = QThread()
                self.worker.moveToThread(self.worker_thread)

                # Connect signals
                self.worker_thread.started.connect(self.worker.run)
                self.worker.error.connect(self.handle_error)
                self.worker.load_layer.connect(self.load_layer)
                self.worker.info.connect(self.show_info)
                self.worker.file_size_warning.connect(self.handle_large_file_warning)
                self.worker.finished.connect(self.cleanup_thread)
                self.worker.progress.connect(self.update_progress)
                self.progress_dialog.canceled.connect(self.cancel_download)

                # Set size warning accepted
                self.worker.size_warning_accepted = True

                # Show progress dialog and start thread
                self.progress_dialog.show()
                self.worker_thread.start()
                return

            else:  # Cancel
                self.cleanup_thread()
                return

    def create_progress_dialog(
        self, title="Downloading Data", message="Starting download..."
    ):
        """Create and return a configured progress dialog"""
        progress_dialog = QProgressDialog(
            message, "Cancel", 0, 0, self.iface.mainWindow()
        )
        progress_dialog.setWindowTitle(title)
        progress_dialog.setWindowModality(Qt.NonModal)
        progress_dialog.setMinimumDuration(0)
        return progress_dialog

    def setup_worker(self, dataset_url, extent, output_file, validation_results):
        """Create and setup a worker thread with all connections"""
        self.worker = Worker(
            dataset_url, extent, output_file, self.iface, validation_results
        )
        self.worker_thread = QThread()
        self.worker.moveToThread(self.worker_thread)

        # Connect signals
        self.worker_thread.started.connect(self.worker.run)
        self.worker.error.connect(self.handle_error)
        self.worker.load_layer.connect(self.load_layer)
        self.worker.info.connect(self.show_info)
        self.worker.file_size_warning.connect(self.handle_large_file_warning)
        self.worker.finished.connect(self.cleanup_thread)
        self.worker.progress.connect(self.update_progress)
        self.progress_dialog.canceled.connect(self.cancel_download)

        return self.worker, self.worker_thread


def classFactory(iface):
    return QgisPluginGeoParquet(iface)

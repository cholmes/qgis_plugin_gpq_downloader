import json

from qgis.PyQt.QtWidgets import (
    QMessageBox,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QComboBox,
    QProgressDialog,
    QRadioButton,
    QStackedWidget,
    QWidget,
)
from qgis.PyQt.QtCore import pyqtSignal, Qt, QThread
from qgis.core import QgsSettings
import os
from gpq_downloader.utils import ValidationWorker


class DataSourceDialog(QDialog):
    validation_complete = pyqtSignal(bool, str, dict)

    def __init__(self, parent=None, iface=None):
        super().__init__(parent)
        self.iface = iface
        self.validation_thread = None
        self.validation_worker = None
        self.progress_message = None
        self.requires_validation = True
        self.setWindowTitle("GeoParquet Data Source")
        self.setMinimumWidth(500)

        base_path = os.path.dirname(os.path.abspath(__file__))
        presets_path = os.path.join(base_path, "data", "presets.json")
        with open(presets_path, "r") as f:
            self.PRESET_DATASETS = json.load(f)

        # Create main layout
        layout = QVBoxLayout()

        # Create horizontal layout for radio buttons
        radio_layout = QHBoxLayout()

        # Create radio buttons
        self.overture_radio = QRadioButton("Overture Maps")
        self.sourcecoop_radio = QRadioButton("Source Cooperative")
        self.other_radio = QRadioButton("Hugging Face")
        self.custom_radio = QRadioButton("Custom URL")

        # Add radio buttons to horizontal layout
        radio_layout.addWidget(self.overture_radio)
        radio_layout.addWidget(self.sourcecoop_radio)
        radio_layout.addWidget(self.other_radio)
        radio_layout.addWidget(self.custom_radio)

        # Connect to save state
        self.overture_radio.released.connect(self.save_radio_button_state)
        self.sourcecoop_radio.released.connect(self.save_radio_button_state)
        self.other_radio.released.connect(self.save_radio_button_state)
        self.custom_radio.released.connect(self.save_radio_button_state)

        # Add radio button layout to main layout
        layout.addLayout(radio_layout)

        # Add some spacing between radio buttons and content
        layout.addSpacing(10)

        # Create and setup the stacked widget for different options
        self.stack = QStackedWidget()

        # Custom URL page
        custom_page = QWidget()
        custom_layout = QVBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText(
            "Enter URL to Parquet file or folder (s3:// or https://)"
        )
        custom_layout.addWidget(self.url_input)
        custom_page.setLayout(custom_layout)

        # Overture Maps page
        overture_page = QWidget()
        overture_layout = QVBoxLayout()
        self.overture_combo = QComboBox()
        self.overture_combo.addItems(
            [
                dataset.get("display_name", key.title())
                for key, dataset in self.PRESET_DATASETS["overture"].items()
            ]
        )
        overture_layout.addWidget(self.overture_combo)

        # Add base subtype combo
        self.base_subtype_widget = QWidget()
        base_subtype_layout = QVBoxLayout()
        base_subtype_layout.setContentsMargins(
            20, 0, 0, 0
        )  # Add left margin for indentation
        self.base_subtype_label = QLabel("Base Layer Type:")
        self.base_subtype_combo = QComboBox()
        self.base_subtype_combo.addItems(
            ["infrastructure", "land", "land_cover", "land_use", "water", "bathymetry"]
        )
        base_subtype_layout.addWidget(self.base_subtype_label)
        base_subtype_layout.addWidget(self.base_subtype_combo)
        self.base_subtype_widget.setLayout(base_subtype_layout)
        self.base_subtype_widget.hide()  # Initially hidden

        overture_layout.addWidget(self.base_subtype_widget)
        overture_page.setLayout(overture_layout)

        # Connect the overture combo change signal
        self.overture_combo.currentTextChanged.connect(self.handle_overture_selection)

        # Source Cooperative page
        sourcecoop_page = QWidget()
        sourcecoop_layout = QVBoxLayout()
        self.sourcecoop_combo = QComboBox()
        self.sourcecoop_combo.addItems(
            [
                dataset["display_name"]
                for dataset in self.PRESET_DATASETS["source_cooperative"].values()
            ]
        )
        sourcecoop_layout.addWidget(self.sourcecoop_combo)

        # Add link label
        self.sourcecoop_link = QLabel()
        self.sourcecoop_link.setOpenExternalLinks(True)
        self.sourcecoop_link.setWordWrap(True)
        sourcecoop_layout.addWidget(self.sourcecoop_link)

        # Connect combo box change to update link
        self.sourcecoop_combo.currentTextChanged.connect(self.update_sourcecoop_link)
        sourcecoop_page.setLayout(sourcecoop_layout)

        # Other sources page
        other_page = QWidget()
        other_layout = QVBoxLayout()
        self.other_combo = QComboBox()
        self.other_combo.addItems(
            [
                dataset["display_name"]
                for dataset in self.PRESET_DATASETS["other"].values()
            ]
        )
        other_layout.addWidget(self.other_combo)

        # Add link label for other sources
        self.other_link = QLabel()
        self.other_link.setOpenExternalLinks(True)
        self.other_link.setWordWrap(True)
        other_layout.addWidget(self.other_link)

        # Connect combo box change to update link
        self.other_combo.currentTextChanged.connect(self.update_other_link)
        other_page.setLayout(other_layout)

        # Add initial link update for other sources
        self.update_other_link(self.other_combo.currentText())

        # Add pages to stack
        self.stack.addWidget(custom_page)
        self.stack.addWidget(overture_page)
        self.stack.addWidget(sourcecoop_page)
        self.stack.addWidget(other_page)

        layout.addWidget(self.stack)

        # Buttons
        button_layout = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.cancel_button = QPushButton("Cancel")
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)

        # Connect signals
        self.custom_radio.toggled.connect(lambda: self.stack.setCurrentIndex(0))
        self.overture_radio.toggled.connect(lambda: self.stack.setCurrentIndex(1))
        self.sourcecoop_radio.toggled.connect(lambda: self.stack.setCurrentIndex(2))
        self.other_radio.toggled.connect(lambda: self.stack.setCurrentIndex(3))
        self.ok_button.clicked.connect(self.validate_and_accept)
        self.cancel_button.clicked.connect(self.reject)

        # Add after setting up the sourcecoop_combo
        self.update_sourcecoop_link(self.sourcecoop_combo.currentText())

    def save_radio_button_state(self) -> None:
        if self.custom_radio.isChecked():
            button_name = self.custom_radio.text()
        elif self.overture_radio.isChecked():
            button_name = self.overture_radio.text()
        elif self.sourcecoop_radio.isChecked():
            button_name = self.sourcecoop_radio.text()
        elif self.other_radio.isChecked():
            button_name = self.other_radio.text()
        elif self.custom_radio.isChecked():
            button_name = self.custom_radio.text()

        QgsSettings().setValue(
            "gpq_downloader/radio_selection",
            button_name,
            section=QgsSettings.Plugins,
        )

    def handle_overture_selection(self, text):
        """Show/hide base subtype combo based on selection"""
        self.base_subtype_widget.setVisible(text == "Base")

    def validate_and_accept(self):
        """Validate the input and accept the dialog if valid"""
        url = self.get_url()
        if not url:
            QMessageBox.warning(
                self, "Validation Error", "Please enter a URL or select a dataset"
            )
            return

        # For custom URLs, do some basic validation
        if self.custom_radio.isChecked():
            if not (
                url.startswith("http://")
                or url.startswith("https://")
                or url.startswith("s3://")
                or url.startswith("file://")
                or url.startswith("hf://")
            ):
                QMessageBox.warning(
                    self,
                    "Validation Error",
                    "URL must start with http://, https://, s3://, hf://,or file://",
                )
                return

        # Set requires_validation based on the selected dataset
        self.requires_validation = True
        if self.overture_radio.isChecked() or (self.sourcecoop_radio.isChecked()):
            self.requires_validation = False

        # Create progress dialog
        self.progress_dialog = QProgressDialog(
            "Starting validation...", "Cancel", 0, 0, self
        )
        self.progress_dialog.setWindowTitle("Validating Data Source")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(0)

        # Get the current canvas extent
        extent = self.iface.mapCanvas().extent()

        # Setup validation worker
        self.validation_worker = ValidationWorker(url, self.iface, extent)
        self.validation_thread = QThread()
        self.validation_worker.moveToThread(self.validation_thread)

        # Connect signals
        self.validation_thread.started.connect(self.validation_worker.run)
        self.validation_worker.progress.connect(self.update_progress)
        self.validation_worker.needs_bbox_warning.connect(self.show_bbox_warning)
        self.validation_worker.finished.connect(self.handle_validation_result)
        self.validation_worker.finished.connect(lambda: self.cleanup_validation(True))
        self.progress_dialog.canceled.connect(lambda: self.cleanup_validation(False))

        # Start validation
        self.validation_thread.start()
        self.progress_dialog.show()

    def update_progress(self, message):
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.setLabelText(message)

    def handle_validation_result(self, success, message, validation_results):
        """Handle validation result in the dialog"""
        if success:
            self.validation_complete.emit(True, message, validation_results)
            self.accept()
        else:
            QMessageBox.warning(self, "Validation Error", message)
            self.validation_complete.emit(False, message, validation_results)

    def cleanup_validation(self, success):
        if self.validation_worker:
            self.validation_worker.deleteLater()
            self.validation_worker = None

        if self.validation_thread:
            self.validation_thread.quit()
            self.validation_thread.wait()
            self.validation_thread.deleteLater()
            self.validation_thread = None

        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        if not success:
            self.reject()

    def closeEvent(self, event):
        """Handle dialog closing"""
        self.cleanup_validation(False)
        super().closeEvent(event)

    def get_url(self):
        if self.custom_radio.isChecked():
            return self.url_input.text().strip()
        elif self.overture_radio.isChecked():
            theme = self.overture_combo.currentText().lower()
            dataset = self.PRESET_DATASETS["overture"][theme]
            if theme == "transportation":
                type_str = "segment"
            elif theme == "divisions":
                type_str = "division_area"
            elif theme == "addresses":
                type_str = "*"
            elif theme == "base":
                type_str = self.base_subtype_combo.currentText()
            else:
                type_str = theme.rstrip("s")  # remove trailing 's' for singular form
            return dataset["url_template"].format(subtype=type_str)
        elif self.sourcecoop_radio.isChecked():
            selection = self.sourcecoop_combo.currentText()
            dataset = next(
                (
                    dataset
                    for dataset in self.PRESET_DATASETS["source_cooperative"].values()
                    if dataset["display_name"] == selection
                ),
                None,
            )
            if dataset:
                return dataset["url"]
        elif self.other_radio.isChecked():
            selection = self.other_combo.currentText()
            dataset = next(
                (
                    dataset
                    for dataset in self.PRESET_DATASETS["other"].values()
                    if dataset["display_name"] == selection
                ),
                None,
            )
            if dataset:
                return dataset["url"]
        return ""

    def update_sourcecoop_link(self, selection):
        """Update the link based on the selected dataset"""
        if selection == "Planet EU Field Boundaries (2022)":
            self.sourcecoop_link.setText(
                '<a href="https://source.coop/repositories/planet/eu-field-boundaries/description">View dataset info</a>'
            )
        elif selection == "USDA Crop Sequence Boundaries":
            self.sourcecoop_link.setText(
                '<a href="https://source.coop/fiboa/us-usda-cropland/description">View dataset info</a>'
            )
        elif selection == "California Crop Mapping":
            self.sourcecoop_link.setText(
                '<a href="https://source.coop/repositories/fiboa/us-ca-scm/description">View dataset info</a>'
            )
        elif selection == "VIDA Google/Microsoft/OSM Buildings":
            self.sourcecoop_link.setText(
                '<a href="https://source.coop/repositories/vida/google-microsoft-osm-open-buildings/description">View dataset info</a>'
            )
        else:
            self.sourcecoop_link.setText("")

    def update_other_link(self, selection):
        """Update the link based on the selected dataset"""
        for dataset in self.PRESET_DATASETS["other"].values():
            if dataset["display_name"] == selection:
                self.other_link.setText(
                    f'<a href="{dataset["info_url"]}">View dataset info</a>'
                )
                return
        self.other_link.setText("")

    def show_bbox_warning(self):
        """Show bbox warning dialog in main thread"""
        # Close the progress dialog if it exists
        if hasattr(self, "progress_dialog") and self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        reply = QMessageBox.warning(
            self,
            "No bbox Column Detected",
            "This dataset doesn't have a bbox column, which means downloads will be slower. "
            "GeoParquet 1.1 files with a bbox column work much better - tell your data provider to upgrade!\n\n"
            "Do you want to continue with the download?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        validation_results = {"has_bbox": False, "schema": None}
        if reply == QMessageBox.No:
            self.validation_complete.emit(
                False, "Download cancelled by user.", validation_results
            )
        else:
            self.validation_complete.emit(
                True, "Validation successful", validation_results
            )

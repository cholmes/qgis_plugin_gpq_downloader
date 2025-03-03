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
    QCheckBox,
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

        # Create horizontal layout for main checkboxes
        checkbox_layout = QHBoxLayout()

        # Create a widget to hold checkboxes
        self.overture_checkboxes = {}
        for key in self.PRESET_DATASETS['overture'].keys():
            if key != 'base':  # Handle base separately
                checkbox = QCheckBox(key.title())
                self.overture_checkboxes[key] = checkbox
                checkbox_layout.addWidget(checkbox)

        # Add the horizontal checkbox layout to main layout
        overture_layout.addLayout(checkbox_layout)

        # Add base layer section
        base_group = QWidget()
        base_layout = QVBoxLayout()
        base_layout.setContentsMargins(0, 10, 0, 0)  # Add some top margin

        self.base_checkbox = QCheckBox("Base")
        self.overture_checkboxes['base'] = self.base_checkbox
        base_layout.addWidget(self.base_checkbox)

        # Add base subtype checkboxes
        self.base_subtype_widget = QWidget()
        base_subtype_layout = QHBoxLayout()  # Horizontal layout for subtypes
        base_subtype_layout.setContentsMargins(20, 0, 0, 0)  # Add left margin for indentation

        # Replace combo box with checkboxes
        self.base_subtype_checkboxes = {}
        subtype_display_names = {
            'infrastructure': 'Infrastructure',
            'land': 'Land',
            'land_cover': 'Land Cover',
            'land_use': 'Land Use',
            'water': 'Water',
            'bathymetry': 'Bathymetry'
        }

        for subtype in self.PRESET_DATASETS['overture']['base']['subtypes']:
            checkbox = QCheckBox(subtype_display_names[subtype])
            self.base_subtype_checkboxes[subtype] = checkbox
            base_subtype_layout.addWidget(checkbox)

        self.base_subtype_widget.setLayout(base_subtype_layout)
        self.base_subtype_widget.hide()

        base_layout.addWidget(self.base_subtype_widget)
        base_group.setLayout(base_layout)
        overture_layout.addWidget(base_group)

        # Connect base checkbox to show/hide subtype checkboxes and resize dialog
        self.base_checkbox.toggled.connect(self.base_subtype_widget.setVisible)
        self.base_checkbox.toggled.connect(lambda checked: self.adjust_dialog_width(checked, 100))
        

        overture_page.setLayout(overture_layout)

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

        # Load checkbox states during initialization
        self.load_checkbox_states()

        # Connect each checkbox to save its state when toggled
        for checkbox in self.overture_checkboxes.values():
            checkbox.toggled.connect(self.save_checkbox_states)
        for checkbox in self.base_subtype_checkboxes.values():
            checkbox.toggled.connect(self.save_checkbox_states)

        # Ensure to call save_checkbox_states when the dialog is accepted
        self.ok_button.clicked.connect(self.save_checkbox_states)

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
        urls = self.get_urls()
        if not urls:
            QMessageBox.warning(self, "Validation Error", "Please select at least one dataset")
            return

        # For Overture datasets, we know they're valid so we can skip validation
        if self.overture_radio.isChecked():
            self.selected_urls = urls
            self.accept()
            return

        # For custom URLs, do validation
        if self.custom_radio.isChecked():
            for url in urls:
                if not (url.startswith('http://') or url.startswith('https://') or 
                       url.startswith('s3://') or url.startswith('file://') or url.startswith('hf://')):
                    QMessageBox.warning(self, "Validation Error", 
                        "URL must start with http://, https://, s3://, hf://, or file://")
                    return

                # Create progress dialog for validation
                progress_dialog = QProgressDialog("Validating URL...", "Cancel", 0, 0, self)
                progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)

                # Create validation worker
                self.validation_worker = ValidationWorker(url, self.iface, self.iface.mapCanvas().extent())
                self.validation_thread = QThread()
                self.validation_worker.moveToThread(self.validation_thread)

                # Connect signals
                self.validation_thread.started.connect(self.validation_worker.run)
                self.validation_worker.progress.connect(progress_dialog.setLabelText)
                self.validation_worker.finished.connect(
                    lambda success, message, results: self.handle_validation_result(
                        success, message, results, progress_dialog, urls
                    )
                )
                self.validation_worker.needs_bbox_warning.connect(self.show_bbox_warning)

                # Start validation
                self.validation_thread.start()
                progress_dialog.exec()
                return

        # For other preset sources, we can skip validation
        self.selected_urls = urls
        self.accept()

    def handle_validation_result(self, success, message, validation_results, progress_dialog, urls):
        """Handle validation result in the dialog"""
        if success:
            self.validation_complete.emit(True, message, validation_results)
            self.accept()
        else:
            QMessageBox.warning(self, "Validation Error", message)
            self.validation_complete.emit(False, message, validation_results)

        if progress_dialog:
            progress_dialog.close()
            self.progress_dialog = None

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

    def get_urls(self):
        """Returns a list of URLs for selected datasets"""
        urls = []
        if self.custom_radio.isChecked():
            return [self.url_input.text().strip()]
        elif self.overture_radio.isChecked():
            for theme, checkbox in self.overture_checkboxes.items():
                if checkbox.isChecked():
                    dataset = self.PRESET_DATASETS['overture'][theme]
                    if theme == "transportation":
                        type_str = "segment"
                    elif theme == "divisions":
                        type_str = "division_area"
                    elif theme == "addresses":
                        type_str = "*"
                    elif theme == "base":
                        # Handle multiple base subtypes
                        for subtype, subtype_checkbox in self.base_subtype_checkboxes.items():
                            if subtype_checkbox.isChecked():
                                urls.append(dataset['url_template'].format(subtype=subtype))
                        continue  # Skip the normal URL append for base
                    else:
                        type_str = theme.rstrip('s')  # remove trailing 's' for singular form
                    urls.append(dataset['url_template'].format(subtype=type_str))
        elif self.sourcecoop_radio.isChecked():
            selection = self.sourcecoop_combo.currentText()
            dataset = next((dataset for dataset in self.PRESET_DATASETS['source_cooperative'].values() 
                           if dataset['display_name'] == selection), None)
            return [dataset['url']] if dataset else []
        elif self.other_radio.isChecked():
            selection = self.other_combo.currentText()
            dataset = next((dataset for dataset in self.PRESET_DATASETS['other'].values() 
                           if dataset['display_name'] == selection), None)
            return [dataset['url']] if dataset else []
        return urls

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
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        validation_results = {"has_bbox": False, "schema": None}
        if reply == QMessageBox.StandardButton.No:
            self.validation_complete.emit(
                False, "Download cancelled by user.", validation_results
            )
        else:
            self.validation_complete.emit(
                True, "Validation successful", validation_results
            )

    def adjust_dialog_width(self, checked, width):
        """Adjust the dialog width based on the base checkbox state."""
        if checked:
            self.resize(self.width() + width, self.height())
        else:
            self.resize(self.width() - width, self.height())

    def save_checkbox_states(self) -> None:
        # Save main checkboxes
        for key, checkbox in self.overture_checkboxes.items():
            QgsSettings().setValue(
                f"gpq_downloader/checkbox_{key}",
                checkbox.isChecked(),
                section=QgsSettings.Plugins,
            )
        
        # Save base subtype checkboxes
        for key, checkbox in self.base_subtype_checkboxes.items():
            QgsSettings().setValue(
                f"gpq_downloader/base_subtype_checkbox_{key}",
                checkbox.isChecked(),
                section=QgsSettings.Plugins,
            )

    def load_checkbox_states(self) -> None:
        # Load main checkboxes
        for key, checkbox in self.overture_checkboxes.items():
            checked = QgsSettings().value(
                f"gpq_downloader/checkbox_{key}",
                False,
                type=bool,
                section=QgsSettings.Plugins,
            )
            checkbox.setChecked(checked)
        
        # Load base subtype checkboxes
        for key, checkbox in self.base_subtype_checkboxes.items():
            checked = QgsSettings().value(
                f"gpq_downloader/base_subtype_checkbox_{key}",
                False,
                type=bool,
                section=QgsSettings.Plugins,
            )
            checkbox.setChecked(checked)
            
        # Update base subtype widget visibility based on base checkbox state
        self.base_subtype_widget.setVisible(self.base_checkbox.isChecked())

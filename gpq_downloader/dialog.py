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
    QToolButton,
    QMenu,
    QAction,
    QGroupBox,
    QTextEdit,
)
from qgis.PyQt.QtCore import pyqtSignal, Qt, QThread, QPoint
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsSettings, QgsRectangle, QgsGeometry
import os
from .utils import ValidationWorker
from .map_tools import PolygonMapTool, AoiHighlighter
from qgis.core import QgsApplication


class DataSourceDialog(QDialog):
    validation_complete = pyqtSignal(bool, str, dict)

    def __init__(self, parent=None, iface=None):
        super().__init__(parent)
        self.iface = iface
        self.validation_thread = None
        self.validation_worker = None
        self.progress_message = None
        self.requires_validation = True
        self.extent_group = None
        self.extent_button = None
        self.extent_display = None
        self.current_extent = None
        self.aoi_geometry = None
        self.aoi_geometry_crs = None
        self.polygon_tool = None
        
        # Create the AOI highlighter
        self.aoi_highlighter = None
        
        # Create dictionary to store all the checkboxes
        self.overture_checkboxes = {}
        self.base_subtype_checkboxes = {}
        self.base_checkbox = None
        
        # Setup the UI
        self.setup_ui()
        
        # Create AOI highlighter
        if self.iface:
            self.aoi_highlighter = AoiHighlighter(self.iface.mapCanvas())
            
            # Connect to extent changes
            #self.iface.mapCanvas().extentChanged.connect(self.on_map_extent_changed)
            
            # Connect to layer changes
            from qgis.core import QgsProject
            QgsProject.instance().layersAdded.connect(self.on_layers_changed)
            QgsProject.instance().layersRemoved.connect(self.on_layers_changed)
            QgsProject.instance().layerWasAdded.connect(self.on_layers_changed)
            QgsProject.instance().layerWillBeRemoved.connect(self.on_layers_changed)
        
        # Load last used extent if available
        self.load_last_extent()
        
        # Load checkbox states
        self.load_checkbox_states()
        
        # Update the layer combo box
        self.populate_layer_combo()
        
        # Load the AOI checkbox state after all UI is created
        if hasattr(self, 'use_aoi_checkbox'):
            checked = QgsSettings().value(
                "gpq_downloader/use_aoi_checkbox",
                True,
                type=bool,
                section=QgsSettings.Plugins,
            )
            self.use_aoi_checkbox.setChecked(checked)
            self.toggle_aoi_controls(checked)

    def setup_ui(self):
        self.setWindowTitle("GeoParquet Data Source")
        self.setMinimumWidth(500)
        
        # Make dialog non-modal and keep it on top
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        
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

        # Add Area of Interest group
        layout.addWidget(self.setup_area_of_interest())

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
            
        # Check if the user selected an Area of Interest, only if the AOI checkbox is checked
        if hasattr(self, 'use_aoi_checkbox') and self.use_aoi_checkbox.isChecked() and not self.current_extent:
            reply = QMessageBox.warning(
                self,
                "No Area of Interest Selected",
                "No area of interest has been explicitly selected. The current map canvas extent will be used instead, which may result in a larger download than intended.\n\n"
                "Do you want to continue using the current map canvas extent?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return

        # For Overture datasets, we know they're valid so we can skip validation
        if self.overture_radio.isChecked():
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
                self.progress_dialog = QProgressDialog("Validating URL...", "Cancel", 0, 0, self)
                self.progress_dialog.setWindowModality(Qt.WindowModality.NonModal)
                self.progress_dialog.canceled.connect(self.cancel_validation)

                # If AOI is enabled, use custom extent if set, otherwise use map canvas extent
                # If AOI is disabled, always use map canvas extent
                extent = None
                if hasattr(self, 'use_aoi_checkbox') and self.use_aoi_checkbox.isChecked():
                    extent = self.current_extent if self.current_extent else self.iface.mapCanvas().extent()
                else:
                    extent = self.iface.mapCanvas().extent()
                
                # Create validation worker
                self.validation_worker = ValidationWorker(url, self.iface, extent)
                self.validation_thread = QThread()
                self.validation_worker.moveToThread(self.validation_thread)

                # Connect signals
                self.validation_thread.started.connect(self.validation_worker.run)
                self.validation_worker.progress.connect(self.progress_dialog.setLabelText)
                self.validation_worker.finished.connect(
                    lambda success, message, results: self.handle_validation_result(
                        success, message, results
                    )
                )
                self.validation_worker.needs_bbox_warning.connect(self.show_bbox_warning)

                # Start validation
                self.validation_thread.start()
                self.progress_dialog.show()
                return

        # For other preset sources, we can skip validation
        self.accept()

    def handle_validation_result(self, success, message, validation_results):
        """Handle validation result in the dialog"""
        self.cleanup_validation()
        
        if success:
            self.validation_complete.emit(True, message, validation_results)
            self.accept()
        else:
            QMessageBox.warning(self, "Validation Error", message)
            self.validation_complete.emit(False, message, validation_results)

    def cancel_validation(self):
        """Handle validation cancellation"""
        if self.validation_worker:
            self.validation_worker.killed = True
        self.cleanup_validation()

    def cleanup_validation(self):
        """Clean up validation resources"""
        if hasattr(self, 'progress_dialog') and self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None

        if self.validation_worker:
            self.validation_worker.deleteLater()
            self.validation_worker = None

        if self.validation_thread:
            self.validation_thread.quit()
            self.validation_thread.wait()
            self.validation_thread.deleteLater()
            self.validation_thread = None

    def closeEvent(self, event):
        """Handle dialog close event"""
        # Clean up validation if running
        if self.validation_thread and self.validation_thread.isRunning():
            self.cancel_validation()
            
        # Disconnect from layer changes
        if self.iface:
            from qgis.core import QgsProject
            QgsProject.instance().layersAdded.disconnect(self.on_layers_changed)
            QgsProject.instance().layersRemoved.disconnect(self.on_layers_changed)
            QgsProject.instance().layerWasAdded.disconnect(self.on_layers_changed)
            QgsProject.instance().layerWillBeRemoved.disconnect(self.on_layers_changed)
            
        # Reset the map tool to default
        if self.polygon_tool and self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
            self.polygon_tool = None
            
        # Clear any AOI highlighting
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()
            
        # Disconnect from any active layer selection signals
        if self.iface and self.iface.activeLayer():
            try:
                self.iface.activeLayer().selectionChanged.disconnect(self.on_selection_changed)
            except:
                pass
                
        # Restore the default map tool
        if self.iface:
            self.iface.actionPan().trigger()
            
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

        validation_results = {"has_bbox": False, "schema": None, "bbox_column": None, "geometry_column": "geometry"}
        if reply == QMessageBox.StandardButton.No:
            self.validation_complete.emit(
                False, "Download cancelled by user.", validation_results
            )
        else:
            # Accept the dialog when user clicks Yes
            self.validation_complete.emit(
                True, "Validation successful", validation_results
            )
            self.accept()

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
            
        # Save the AOI checkbox state
        if hasattr(self, 'use_aoi_checkbox'):
            QgsSettings().setValue(
                "gpq_downloader/use_aoi_checkbox",
                self.use_aoi_checkbox.isChecked(),
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

    def on_validation_finished(self, success, message, results):
        # This method should handle the validation results
        # Check how it's setting validation_results
        pass

    def setup_area_of_interest(self):
        """Create and setup the Area of Interest group with Extent button"""
        # Create group box with a horizontal layout for the title
        self.extent_group = QGroupBox()
        extent_layout = QVBoxLayout()
        
        # Create a horizontal layout for the title and checkbox
        title_layout = QHBoxLayout()
        title_label = QLabel("Area of Interest")
        title_label.setStyleSheet("font-weight: bold;")
        
        # Add the checkbox next to the title, with no text
        self.use_aoi_checkbox = QCheckBox()
        self.use_aoi_checkbox.setChecked(True)
        self.use_aoi_checkbox.setToolTip("Uncheck to disable area of interest filtering")
        self.use_aoi_checkbox.toggled.connect(self.toggle_aoi_controls)
        
        title_layout.addWidget(title_label)
        title_layout.addWidget(self.use_aoi_checkbox)
        title_layout.addStretch()
        extent_layout.addLayout(title_layout)
        
        # Add layer selection dropdown
        layer_layout = QHBoxLayout()
        layer_label = QLabel("Active Layer:")
        self.layer_combo = QComboBox()
        self.layer_combo.setToolTip("Select the active layer to use for extent and feature selection")
        self.populate_layer_combo()
        layer_layout.addWidget(layer_label)
        layer_layout.addWidget(self.layer_combo)
        layer_layout.addStretch()
        extent_layout.addLayout(layer_layout)
        
        # Create tool button with dropdown menu
        button_layout = QHBoxLayout()
        
        # Extent button
        self.extent_button = QToolButton()
        self.extent_button.setText(" Extent")
        self.extent_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.extent_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.extent_button.setToolTip("Click the dropdown arrow to select an existing extent")
        self.extent_button.setCheckable(True)  # Make button checkable
        
        # Use the extents.svg icon from the icons folder
        base_path = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_path, "icons", "extents.svg")
        self.extent_button.setIcon(QIcon(icon_path))
        
        # Create menu for the extent button
        extent_menu = QMenu()
        
        # Add actions to menu
        canvas_action = QAction("Use current map canvas extent", self)
        canvas_action.triggered.connect(self.use_canvas_extent)
        
        layer_action = QAction("Use extent of the active layer", self)
        layer_action.triggered.connect(self.use_active_layer_extent)
        
        extent_menu.addAction(canvas_action)
        extent_menu.addAction(layer_action)
        
        # Set the menu to the button
        self.extent_button.setMenu(extent_menu)
        
        # Connect button click to show the menu
        self.extent_button.clicked.connect(self.show_extent_menu)
        
        # Draw button
        self.draw_button = QToolButton()
        self.draw_button.setText(" Draw")
        self.draw_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.draw_button.setToolTip("Draw a custom polygon on the map")
        self.draw_button.setCheckable(True)  # Make button checkable
        # Use the extents.svg icon from the icons folder
        base_path = os.path.dirname(os.path.abspath(__file__))
        icon_path = os.path.join(base_path, "icons", "extent-draw-polygon.svg")
        self.draw_button.setIcon(QIcon(icon_path))
        
        # Connect button click directly to polygon drawing
        self.draw_button.clicked.connect(self.start_polygon_draw)
        
        # Select Features button
        self.select_button = QToolButton()
        self.select_button.setText(" Select")
        self.select_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.select_button.setToolTip("Select features from active layer to define area of interest")
        self.select_button.setCheckable(True)  # Make button checkable
        # Use the selection icon or fall back to standard icon
        try:
            # Try to use a QGIS selection icon if available
            selection_icon = QgsApplication.getThemeIcon("/mActionSelectRectangle.svg")
            if not selection_icon.isNull():
                self.select_button.setIcon(selection_icon)
            else:
                # If QGIS theme icon not available, use standard QStyle icon
                from qgis.PyQt.QtWidgets import QStyle
                self.select_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogListView))
        except:
            # If there's any error, use a generic icon
            from qgis.PyQt.QtWidgets import QStyle
            self.select_button.setIcon(self.style().standardIcon(QStyle.SP_FileDialogListView))
        
        # Connect select button to selection tool
        self.select_button.clicked.connect(self.start_feature_selection)
        
        # Clear button
        self.clear_button = QToolButton()
        self.clear_button.setText(" Clear")
        self.clear_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.clear_button.setToolTip("Clear the current area of interest and selected features")
        # Use the clear icon if available or another suitable icon
        icon_path = os.path.join(base_path, "icons", "extent-clear.svg")
        if os.path.exists(icon_path):
            self.clear_button.setIcon(QIcon(icon_path))
        else:
            # Fallback to a standard icon if the custom one isn't available
            from qgis.PyQt.QtWidgets import QStyle
            self.clear_button.setIcon(self.style().standardIcon(QStyle.SP_DialogResetButton))
        
        # Connect clear button to clear function
        self.clear_button.clicked.connect(self.clear_extent)
        
        # Add buttons to layout
        button_layout.addWidget(self.extent_button)
        button_layout.addWidget(self.draw_button)
        button_layout.addWidget(self.select_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addStretch()
        extent_layout.addLayout(button_layout)
        
        # Add text display for extent
        self.extent_display = QTextEdit()
        self.extent_display.setReadOnly(True)
        self.extent_display.setMaximumHeight(40)
        self.extent_display.setPlaceholderText("No area of interest selected. Use the buttons above to select one.")
        extent_layout.addWidget(self.extent_display)
        
        # Set the layout to the group
        self.extent_group.setLayout(extent_layout)
        
        # Don't update the extent display with initial extent
        # if self.current_extent:
        #     self.update_extent_display("Initial Map Canvas")
        
        return self.extent_group
    
    def toggle_aoi_controls(self, enabled):
        """Enable or disable the AOI controls based on the checkbox state"""
        # Enable/disable all controls except the checkbox
        self.layer_combo.setEnabled(enabled)
        self.extent_button.setEnabled(enabled)
        self.draw_button.setEnabled(enabled)
        self.select_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self.extent_display.setEnabled(enabled)
        
        # If disabled, clear the current extent
        if not enabled:
            self.clear_extent()

    def use_canvas_extent(self):
        """Use the current map canvas extent as Area of Interest"""
        if self.iface and self.iface.mapCanvas():
            # Reset the polygon tool if active
            if self.polygon_tool:
                self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
                self.polygon_tool = None
                
            # Disconnect from any active layer selection signals
            if self.iface.activeLayer():
                try:
                    self.iface.activeLayer().selectionChanged.disconnect(self.on_selection_changed)
                except:
                    pass
                
            # Clear any previously drawn geometry
            self.aoi_geometry = None
            self.aoi_geometry_crs = None
            self.current_extent = self.iface.mapCanvas().extent()
            self.update_extent_display("Current Map Canvas")
            
            # Update the AOI highlighting
            if self.aoi_highlighter:
                self.aoi_highlighter.highlight_aoi(extent=self.current_extent)
                
            # Update button states
            self.extent_button.setChecked(True)
            self.draw_button.setChecked(False)
            self.select_button.setChecked(False)
    
    def use_active_layer_extent(self):
        """Use the active layer extent as Area of Interest"""
        if self.iface and self.iface.activeLayer():
            # Reset the polygon tool if active
            if self.polygon_tool and self.iface.mapCanvas():
                self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
                self.polygon_tool = None
                
            # Disconnect from any active layer selection signals
            try:
                self.iface.activeLayer().selectionChanged.disconnect(self.on_selection_changed)
            except:
                pass
                
            # Clear any previously drawn geometry
            self.aoi_geometry = None
            self.aoi_geometry_crs = None
            
            # Get the active layer and its extent
            active_layer = self.iface.activeLayer()
            layer_extent = active_layer.extent()
            
            # Get the layer's CRS and the map canvas CRS
            layer_crs = active_layer.crs()
            map_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
            
            # Check if the layer CRS is different from the map canvas CRS
            if layer_crs.authid() != map_crs.authid():
                # Create a coordinate transform from layer CRS to map canvas CRS
                from qgis.core import QgsCoordinateTransform, QgsProject
                transform = QgsCoordinateTransform(
                    layer_crs,
                    map_crs,
                    QgsProject.instance()
                )
                
                # Transform the extent to the map canvas CRS
                layer_extent = transform.transformBoundingBox(layer_extent)
                
                # Store the transformed extent
                self.current_extent = layer_extent
                
                # Also store the geometry with its CRS for later use
                extent_geom = QgsGeometry.fromRect(layer_extent)
                self.aoi_geometry = extent_geom
                self.aoi_geometry_crs = map_crs
            else:
                # No transformation needed, use the layer extent directly
                self.current_extent = layer_extent
            
            # Update the display with the layer name
            layer_name = active_layer.name()
            self.update_extent_display(f"Layer: {layer_name}")
            
            # Ensure the AOI highlighter is properly initialized
            if not self.aoi_highlighter and self.iface.mapCanvas():
                from .map_tools import AoiHighlighter
                self.aoi_highlighter = AoiHighlighter(self.iface.mapCanvas())
                
            # Update the AOI highlighting
            if self.aoi_highlighter:
                self.aoi_highlighter.highlight_aoi(extent=self.current_extent)
                
            # Update button states
            self.extent_button.setChecked(True)
            self.draw_button.setChecked(False)
            self.select_button.setChecked(False)

    def update_extent_display(self, source):
        """Update the extent display with the current extent information"""
        if self.current_extent:
            # Show either drawn geometry WKT or extent WKT
            wkt = ""
            if source == "Drawn Polygon" and self.aoi_geometry:
                # Ensure we're getting a standard WKT format
                from qgis.core import QgsWkbTypes
                geom = QgsGeometry(self.aoi_geometry)
                if geom.wkbType() == QgsWkbTypes.MultiSurface:
                    geom = QgsGeometry.fromMultiPolygonXY(geom.asMultiPolygon())
                elif geom.wkbType() == QgsWkbTypes.CurvePolygon:
                    geom = QgsGeometry.fromPolygonXY(geom.asPolygon())
                wkt = geom.asWkt()
            else:
                extent_geom = QgsGeometry.fromRect(self.current_extent)
                wkt = extent_geom.asWkt()
            
            extent_str = (f"Source: {source}\n"
                         f"WKT: {wkt}")
            self.extent_display.setText(extent_str)
        else:
            self.extent_display.clear()
            self.extent_display.setPlaceholderText("No area of interest selected. Use the buttons above to select one.")

    def get_current_extent(self):
        """Returns the current selected extent or None if not set"""
        return self.current_extent
    
    def accept(self):
        """Override accept to store the current extent"""
        # Store the extent to be used by the plugin only if AOI is enabled
        if hasattr(self, 'use_aoi_checkbox') and self.use_aoi_checkbox.isChecked():
            if hasattr(self, 'current_extent') and self.current_extent:
                QgsSettings().setValue(
                    "gpq_downloader/last_used_extent",
                    self.current_extent.toString(),
                    section=QgsSettings.Plugins,
                )
        
        # Reset the map tool to default when accepting dialog
        if self.polygon_tool and self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
            self.polygon_tool = None
        
        # Clear any AOI highlighting when dialog is accepted
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()
            
        # Disconnect from any active layer selection signals
        if self.iface and self.iface.activeLayer():
            try:
                self.iface.activeLayer().selectionChanged.disconnect(self.on_selection_changed)
            except:
                pass
            
        super().accept()
    
    def reject(self):
        """Override reject to clean up resources"""
        # Reset the map tool to default when rejecting dialog
        if self.polygon_tool and self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
            self.polygon_tool = None
            
        # Clear any AOI highlighting when dialog is rejected
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()
            
        # Disconnect from any active layer selection signals
        if self.iface and self.iface.activeLayer():
            try:
                self.iface.activeLayer().selectionChanged.disconnect(self.on_selection_changed)
            except:
                pass
                
        # Restore the default map tool
        if self.iface:
            self.iface.actionPan().trigger()
            
        super().reject()

    def load_last_extent(self):
        """Load the last used extent from QgsSettings if available"""
        last_extent_str = QgsSettings().value("gpq_downloader/last_used_extent", "", type=str)
        if last_extent_str:
            try:
                self.current_extent = QgsRectangle.fromString(last_extent_str)
                if self.current_extent and self.current_extent.isNull() == False:
                    self.update_extent_display("Last Used Extent")
                    
                    # Update the AOI highlighting
                    if self.aoi_highlighter:
                        self.aoi_highlighter.highlight_aoi(extent=self.current_extent)
            except Exception:
                self.current_extent = None

    def on_map_extent_changed(self):
        """Handle map canvas extent changes"""
        if self.iface and self.iface.mapCanvas():
            self.current_extent = self.iface.mapCanvas().extent()
            self.update_extent_display("Map Canvas")
            
            # Update the AOI highlighting (only if we're using the canvas extent)
            if self.aoi_geometry is None and self.aoi_highlighter:
                self.aoi_highlighter.highlight_aoi(extent=self.current_extent)

    def start_polygon_draw(self):
        """Start drawing a polygon on the map canvas"""
        if self.iface and self.iface.mapCanvas():
            # Clean up existing polygon tool if there is one
            if self.polygon_tool:
                self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
                self.polygon_tool = None
                
            # Disconnect from any active layer selection signals
            if self.iface.activeLayer():
                try:
                    self.iface.activeLayer().selectionChanged.disconnect(self.on_selection_changed)
                except:
                    pass
                
                # Clear any selected features in the active layer
                self.iface.activeLayer().removeSelection()
                
            # Create and set the polygon map tool
            self.polygon_tool = PolygonMapTool(self.iface.mapCanvas())
            self.iface.mapCanvas().setMapTool(self.polygon_tool)
            
            # Connect the signal
            self.polygon_tool.polygonSelected.connect(self.on_polygon_drawn)
            
            # Connect the deactivated signal to clean up the tool when another tool is selected
            self.polygon_tool.deactivated.connect(self.handle_polygon_tool_deactivated)
            
            # Show instructions
            self.iface.messageBar().pushMessage(
                "Draw Polygon", 
                "Click to add vertices. Right-click to finish.", 
                level=0, 
                duration=5
            )
            
            # Update button states
            self.extent_button.setChecked(False)
            self.draw_button.setChecked(True)
            self.select_button.setChecked(False)
            
    def handle_polygon_tool_deactivated(self):
        """Handle the polygon tool being deactivated by another tool"""
        self.polygon_tool = None

    def on_polygon_drawn(self, geometry):
        """Handle when a polygon is drawn"""
        # Store the full geometry
        self.aoi_geometry = QgsGeometry(geometry)
        
        # Get the current map CRS
        map_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        
        # Store the CRS with the geometry for later reprojection if needed
        self.aoi_geometry_crs = map_crs
        
        # Convert polygon geometry to extent for display
        self.current_extent = geometry.boundingBox()
        
        # Update the display
        self.update_extent_display("Drawn Polygon")
        
        # Update the AOI highlighting
        if self.aoi_highlighter:
            self.aoi_highlighter.highlight_aoi(geometry=self.aoi_geometry)
            
        # Reset the map tool after drawing is complete (but keep the tool reference)
        if self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
            
        # Set focus back to the dialog
        self.activateWindow()

    def get_reprojected_geometry(self, target_crs):
        """Return the geometry reprojected to the target CRS if needed"""
        if not self.aoi_geometry:
            return None
            
        # If target CRS is not specified, return the original geometry
        if not target_crs:
            return self.aoi_geometry
            
        # Check if we need to reproject
        if self.aoi_geometry_crs.authid() != target_crs.authid():
            from qgis.core import QgsCoordinateTransform, QgsProject
            
            # Create a coordinate transform
            transform = QgsCoordinateTransform(
                self.aoi_geometry_crs,
                target_crs,
                QgsProject.instance()
            )
            
            # Create a copy of the geometry and transform it
            reprojected_geom = QgsGeometry(self.aoi_geometry)
            reprojected_geom.transform(transform)
            
            # Ensure the geometry is in a standard format
            from qgis.core import QgsWkbTypes
            if reprojected_geom.wkbType() == QgsWkbTypes.MultiSurface:
                reprojected_geom = QgsGeometry.fromMultiPolygonXY(reprojected_geom.asMultiPolygon())
            elif reprojected_geom.wkbType() == QgsWkbTypes.CurvePolygon:
                reprojected_geom = QgsGeometry.fromPolygonXY(reprojected_geom.asPolygon())
            
            return reprojected_geom
        
        # No reprojection needed, but still ensure the geometry is in a standard format
        from qgis.core import QgsWkbTypes
        geom = QgsGeometry(self.aoi_geometry)
        if geom.wkbType() == QgsWkbTypes.MultiSurface:
            geom = QgsGeometry.fromMultiPolygonXY(geom.asMultiPolygon())
        elif geom.wkbType() == QgsWkbTypes.CurvePolygon:
            geom = QgsGeometry.fromPolygonXY(geom.asPolygon())
        
        return geom

    def clear_extent(self):
        """Clear the current area of interest"""
        # Reset the polygon tool if active
        if self.polygon_tool and self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
            self.polygon_tool = None
            
        # Clear any previously drawn geometry
        self.aoi_geometry = None
        self.aoi_geometry_crs = None
        self.current_extent = None
        
        # Clear the extent display
        self.extent_display.clear()
        self.extent_display.setPlaceholderText("No area of interest selected. Use the buttons above to select one.")
        
        # Clear the AOI highlighting
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()
            
        # Clear selected features in all layers and disconnect signals
        if self.iface:
            # Get all layers from the project
            from qgis.core import QgsProject
            layers = QgsProject.instance().mapLayers().values()
            
            for layer in layers:
                # Only process vector layers
                if layer.type() == 0:  # Vector layer
                    # Disconnect from the selection changed signal first to avoid loops
                    try:
                        layer.selectionChanged.disconnect(self.on_selection_changed)
                    except:
                        pass  # If it wasn't connected, just continue
                        
                    # Clear the selection
                    layer.removeSelection()
            
            # If there's an active layer, connect to its selection changed signal
            active_layer = self.iface.activeLayer()
            if active_layer and active_layer.type() == 0:  # Vector layer
                try:
                    active_layer.selectionChanged.disconnect(self.on_selection_changed)
                except:
                    pass  # Make sure it's not already connected
                
            # Deactivate feature selection mode
            if self.iface.mapCanvas():
                # Get the current map tool
                current_tool = self.iface.mapCanvas().mapTool()
                
                # Check if the current tool is a selection tool
                if current_tool and hasattr(current_tool, 'name') and 'select' in current_tool.name().lower():
                    # Switch back to the pan tool
                    self.iface.actionPan().trigger()
                
                # Explicitly deactivate the select rectangle tool
                self.iface.actionSelectRectangle().trigger()
                
                # Ensure we're using the pan tool
                self.iface.actionPan().trigger()
            
            # Refresh the canvas
            self.iface.mapCanvas().refresh()
            
        # Update button states - uncheck all buttons
        self.extent_button.setChecked(False)
        self.draw_button.setChecked(False)
        self.select_button.setChecked(False)

    def start_feature_selection(self):
        """Start selecting features from the active layer"""
        if self.iface and self.iface.mapCanvas():
            # Check if there's an active layer first
            active_layer = self.iface.activeLayer()
            if not active_layer or not active_layer.isSpatial():
                self.iface.messageBar().pushMessage(
                    "Selection Error", 
                    "Please select a vector layer first", 
                    level=1,  # Warning level
                    duration=5
                )
                return
                
            # Clean up existing polygon tool if there is one
            if self.polygon_tool:
                self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
                self.polygon_tool = None
                
            # Clear any previous highlighter to start fresh
            if self.aoi_highlighter:
                self.aoi_highlighter.clear()
                
            # Clear any previous selection
            active_layer.removeSelection()
                
            # Disconnect any existing selection signal first to avoid multiple connections
            try:
                active_layer.selectionChanged.disconnect(self.on_selection_changed)
            except:
                pass
                
            # Connect to the selection changed signal
            active_layer.selectionChanged.connect(self.on_selection_changed)
            
            # Use QGIS's built-in selection tool
            self.iface.actionSelectRectangle().trigger()
            
            # Show instructions
            self.iface.messageBar().pushMessage(
                "Select Features", 
                "Use the selection tool to select features from the active layer. Selected features will define the area of interest.", 
                level=0,  # Info level
                duration=5
            )
            
            # Update button states
            self.extent_button.setChecked(False)
            self.draw_button.setChecked(False)
            self.select_button.setChecked(True)

    def on_selection_changed(self):
        """Handle when the selection in the active layer changes"""
        active_layer = self.iface.activeLayer()
        if not active_layer:
            return
            
        # If no features are selected, clear the highlighting but keep selection mode active
        if active_layer.selectedFeatureCount() == 0:
            if self.aoi_highlighter:
                self.aoi_highlighter.clear()
            self.aoi_geometry = None
            self.aoi_geometry_crs = None
            self.current_extent = None
            
            # Clear the extent display
            if hasattr(self, 'extent_display') and self.extent_display is not None:
                self.extent_display.clear()
                self.extent_display.setPlaceholderText("No features selected. Select features to define the area of interest.")
            return
            
        # Get the layer's CRS and the map canvas CRS
        layer_crs = active_layer.crs()
        map_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        
        # Get the combined geometry of all selected features
        selected_features = active_layer.selectedFeatures()
        combined_geometry = None
        
        for feature in selected_features:
            geom = feature.geometry()
            if geom and not geom.isEmpty():
                # Check if we need to transform the geometry
                if layer_crs.authid() != map_crs.authid():
                    # Create a coordinate transform from layer CRS to map canvas CRS
                    from qgis.core import QgsCoordinateTransform, QgsProject
                    transform = QgsCoordinateTransform(
                        layer_crs,
                        map_crs,
                        QgsProject.instance()
                    )
                    
                    # Create a copy of the geometry and transform it
                    transformed_geom = QgsGeometry(geom)
                    transformed_geom.transform(transform)
                    geom = transformed_geom
                
                if combined_geometry is None:
                    # Use copy constructor instead of clone method
                    combined_geometry = QgsGeometry(geom)
                else:
                    combined_geometry = combined_geometry.combine(geom)
        
        if combined_geometry:
            # Convert MultiSurface to MultiPolygon if needed
            from qgis.core import QgsWkbTypes
            if combined_geometry.wkbType() == QgsWkbTypes.MultiSurface:
                # Force convert to a standard format (MultiPolygon)
                combined_geometry = QgsGeometry.fromMultiPolygonXY(combined_geometry.asMultiPolygon())
            elif combined_geometry.wkbType() == QgsWkbTypes.CurvePolygon:
                # Convert CurvePolygon to standard Polygon
                combined_geometry = QgsGeometry.fromPolygonXY(combined_geometry.asPolygon())
            
            # Store the geometry
            self.aoi_geometry = combined_geometry
            
            # Store the CRS with the geometry for later reprojection if needed
            self.aoi_geometry_crs = map_crs
            
            # Convert combined geometry to extent
            self.current_extent = combined_geometry.boundingBox()
            
            # Update the display
            self.update_extent_display("Selected Features")
            
            # Ensure the AOI highlighter exists
            if not self.aoi_highlighter and self.iface.mapCanvas():
                from .map_tools import AoiHighlighter
                self.aoi_highlighter = AoiHighlighter(self.iface.mapCanvas())
                
            # Update the AOI highlighting - first clear to avoid stacking highlighters
            if self.aoi_highlighter:
                self.aoi_highlighter.clear()
                self.aoi_highlighter.highlight_aoi(geometry=self.aoi_geometry)

    def show_extent_menu(self):
        """Show the extent menu"""
        # Get the menu from the extent button
        menu = self.extent_button.menu()
        if menu:
            # Get the button's position in global coordinates
            button_pos = self.extent_button.mapToGlobal(QPoint(0, 20))
            # Show the menu below the button
            menu.exec_(button_pos)

    def populate_layer_combo(self):
        """Populate the layer combo box with available layers"""
        if not self.iface:
            return
            
        # Store the current selection
        current_layer = None
        if self.layer_combo.currentIndex() >= 0:
            current_layer = self.layer_combo.itemData(self.layer_combo.currentIndex())
            
        # Clear existing items
        self.layer_combo.clear()
        
        # Get all layers from the project in the correct order
        from qgis.core import QgsProject
        root = QgsProject.instance().layerTreeRoot()
        
        # Get all layers in the order they appear in the layer tree
        layers = []
        for layer in root.findLayers():
            if layer.layer() and layer.layer().type() == 0:  # Vector layer
                layers.append(layer.layer())
        
        # Add layers to combo box in the same order as the layer tree
        for layer in layers:
            self.layer_combo.addItem(layer.name(), layer)
            
        # Connect to layer changed signal
        self.layer_combo.currentIndexChanged.connect(self.on_layer_changed)
        
        # Restore the previous selection or select the active layer
        if current_layer and current_layer in layers:
            index = self.layer_combo.findData(current_layer)
            if index >= 0:
                self.layer_combo.setCurrentIndex(index)
        else:
            active_layer = self.iface.activeLayer()
            if active_layer and active_layer in layers:
                index = self.layer_combo.findData(active_layer)
                if index >= 0:
                    self.layer_combo.setCurrentIndex(index)

    def on_layer_changed(self, index):
        """Handle layer selection change"""
        if not self.iface or index < 0:
            return
            
        # Get selected layer
        layer = self.layer_combo.itemData(index)
        if layer:
            # Store whether we were in selection mode
            was_in_selection_mode = False
            if hasattr(self, 'select_button') and self.select_button is not None:
                was_in_selection_mode = self.select_button.isChecked()
            
            # Clear the AOI highlighting to prevent "stuck" highlights
            if self.aoi_highlighter:
                self.aoi_highlighter.clear()
            
            # Reset our tracking variables to ensure a clean state
            self.aoi_geometry = None
            self.aoi_geometry_crs = None
            
            # Clear selection from previous layer if any and disconnect signals
            if self.iface.activeLayer():
                try:
                    self.iface.activeLayer().selectionChanged.disconnect(self.on_selection_changed)
                except:
                    pass
                self.iface.activeLayer().removeSelection()
            
            # Set as active layer
            self.iface.setActiveLayer(layer)
            
            # Uncheck all AOI buttons if they exist and are not None
            if hasattr(self, 'extent_button') and self.extent_button is not None:
                self.extent_button.setChecked(False)
            if hasattr(self, 'draw_button') and self.draw_button is not None:
                self.draw_button.setChecked(False)
            if hasattr(self, 'select_button') and self.select_button is not None:
                self.select_button.setChecked(False)
            
            # Update the extent display to show no selection
            if hasattr(self, 'extent_display') and self.extent_display is not None:
                self.extent_display.clear()
                self.extent_display.setPlaceholderText("No area of interest selected. Use the buttons above to select one.")
            
            # If we were in selection mode, restart it for the new layer
            if was_in_selection_mode and hasattr(self, 'select_button') and self.select_button is not None:
                self.start_feature_selection()

    def on_layers_changed(self):
        """Handle when layers are added or removed from the project"""
        if hasattr(self, 'layer_combo'):
            # Store current active layer
            current_active = self.iface.activeLayer()
            
            # Update the combo box
            self.populate_layer_combo()
            
            # If we had an active layer before, try to restore it
            if current_active:
                index = self.layer_combo.findData(current_active)
                if index >= 0:
                    self.layer_combo.setCurrentIndex(index)
                    # Ensure the layer is still active
                    self.iface.setActiveLayer(current_active)
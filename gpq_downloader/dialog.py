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
from qgis.PyQt.QtCore import pyqtSignal, Qt, QThread
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsSettings, QgsRectangle, QgsGeometry
import os
from .utils import ValidationWorker
from .map_tools import RectangleMapTool, PolygonMapTool


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
        self.setWindowTitle("GeoParquet Data Source")
        self.setMinimumWidth(500)
        
        # Make dialog non-modal and keep it on top
        self.setWindowFlags(Qt.Window | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        
        # Load last used extent if available
        self.load_last_extent()
        
        # Connect to map canvas extent changes
        if self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().extentsChanged.connect(self.on_map_extent_changed)
            # Set initial extent from map canvas
            self.current_extent = self.iface.mapCanvas().extent()

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

                # Use custom extent if set, otherwise use canvas extent
                extent = self.current_extent if self.current_extent else self.iface.mapCanvas().extent()
                
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
        """Handle dialog closing"""
        self.cleanup_validation()
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
        # Create group box
        self.extent_group = QGroupBox("Area of Interest")
        extent_layout = QVBoxLayout()
        
        # Create tool button with dropdown menu
        button_layout = QHBoxLayout()
        
        # Extent button
        self.extent_button = QToolButton()
        self.extent_button.setText("Area of Interest")
        self.extent_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.extent_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        
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
        
        # Connect button click to default action (use canvas extent)
        self.extent_button.clicked.connect(self.use_canvas_extent)
        
        # Draw button
        self.draw_button = QToolButton()
        self.draw_button.setText("Draw")
        self.draw_button.setPopupMode(QToolButton.MenuButtonPopup)
        self.draw_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        
        # Use the extent-draw-polygon.svg icon from the icons folder
        draw_icon_path = os.path.join(base_path, "icons", "extent-draw-polygon.svg")
        self.draw_button.setIcon(QIcon(draw_icon_path))
        
        # Create menu for the draw button
        draw_menu = QMenu()
        
        # Add actions to menu
        rectangle_action = QAction("Rectangle", self)
        rectangle_action.triggered.connect(self.start_rectangle_draw)
        
        polygon_action = QAction("Polygon", self)
        polygon_action.triggered.connect(self.start_polygon_draw)
        
        draw_menu.addAction(rectangle_action)
        draw_menu.addAction(polygon_action)
        
        # Set the menu to the button
        self.draw_button.setMenu(draw_menu)
        
        # Add buttons to layout
        button_layout.addWidget(self.extent_button)
        button_layout.addWidget(self.draw_button)
        button_layout.addStretch()
        extent_layout.addLayout(button_layout)
        
        # Add text display for extent
        self.extent_display = QTextEdit()
        self.extent_display.setReadOnly(True)
        self.extent_display.setMaximumHeight(60)
        self.extent_display.setPlaceholderText("No extent selected. Default is full dataset extent.")
        extent_layout.addWidget(self.extent_display)
        
        # Set the layout to the group
        self.extent_group.setLayout(extent_layout)
        
        # Update the extent display with initial extent if available
        if self.current_extent:
            self.update_extent_display("Map Canvas")
        
        return self.extent_group
    
    def use_canvas_extent(self):
        """Use the current map canvas extent as Area of Interest"""
        if self.iface and self.iface.mapCanvas():
            self.current_extent = self.iface.mapCanvas().extent()
            self.update_extent_display("Map Canvas")
    
    def use_active_layer_extent(self):
        """Use the active layer extent as Area of Interest"""
        if self.iface and self.iface.activeLayer():
            self.current_extent = self.iface.activeLayer().extent()
            layer_name = self.iface.activeLayer().name()
            self.update_extent_display(f"Layer: {layer_name}")
    
    def update_extent_display(self, source):
        """Update the extent display with the current extent information"""
        if self.current_extent:
            extent_str = (f"Source: {source}\n"
                         f"Xmin: {self.current_extent.xMinimum():.6f}, "
                         f"Ymin: {self.current_extent.yMinimum():.6f}, "
                         f"Xmax: {self.current_extent.xMaximum():.6f}, "
                         f"Ymax: {self.current_extent.yMaximum():.6f}")
            self.extent_display.setText(extent_str)
        else:
            self.extent_display.clear()
            self.extent_display.setPlaceholderText("No extent selected. Default is full dataset extent.")

    def get_current_extent(self):
        """Returns the current selected extent or None if not set"""
        return self.current_extent
    
    def accept(self):
        """Override accept to store the current extent"""
        # Store the extent to be used by the plugin
        if hasattr(self, 'current_extent') and self.current_extent:
            QgsSettings().setValue(
                "gpq_downloader/last_used_extent",
                self.current_extent.toString(),
                section=QgsSettings.Plugins,
            )
        super().accept()

    def load_last_extent(self):
        """Load the last used extent from QgsSettings if available"""
        last_extent_str = QgsSettings().value("gpq_downloader/last_used_extent", "", type=str)
        if last_extent_str:
            try:
                self.current_extent = QgsRectangle.fromString(last_extent_str)
                if self.current_extent and self.current_extent.isNull() == False:
                    self.update_extent_display("Last Used Extent")
            except Exception:
                self.current_extent = None

    def on_map_extent_changed(self):
        """Handle map canvas extent changes"""
        if self.iface and self.iface.mapCanvas():
            self.current_extent = self.iface.mapCanvas().extent()
            self.update_extent_display("Map Canvas")

    def start_rectangle_draw(self):
        """Start drawing a rectangle on the map canvas"""
        if self.iface and self.iface.mapCanvas():
            # Create and set the rectangle map tool
            self.rectangle_tool = RectangleMapTool(self.iface.mapCanvas())
            self.iface.mapCanvas().setMapTool(self.rectangle_tool)
            
            # Connect the signal
            self.rectangle_tool.extentSelected.connect(self.on_rectangle_drawn)
            
            # Show instructions
            self.iface.messageBar().pushMessage(
                "Draw Rectangle", 
                "Click and drag to draw a rectangle. Release to finish.", 
                level=0, 
                duration=5
            )
    
    def start_polygon_draw(self):
        """Start drawing a polygon on the map canvas"""
        if self.iface and self.iface.mapCanvas():
            # Create and set the polygon map tool
            self.polygon_tool = PolygonMapTool(self.iface.mapCanvas())
            self.iface.mapCanvas().setMapTool(self.polygon_tool)
            
            # Connect the signal
            self.polygon_tool.polygonSelected.connect(self.on_polygon_drawn)
            
            # Show instructions
            self.iface.messageBar().pushMessage(
                "Draw Polygon", 
                "Click to add vertices. Right-click to finish.", 
                level=0, 
                duration=5
            )
    
    def on_rectangle_drawn(self, extent):
        """Handle when a rectangle is drawn"""
        self.current_extent = extent
        self.update_extent_display("Drawn Rectangle")
    
    def on_polygon_drawn(self, geometry):
        """Handle when a polygon is drawn"""
        # Convert polygon geometry to extent for display
        self.current_extent = geometry.boundingBox()
        self.update_extent_display("Drawn Polygon")

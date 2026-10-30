"""The area of interest: where on the map a download is limited to.

- AoiHighlighter and the polygon and rectangle map tools draw on the canvas
- AreaOfInterestMixin is the data source dialog's Area of Interest group:
  the extent, draw, select-features and bbox buttons, the layer combo, the
  clip checkbox, and the AOI geometry those leave on the dialog

The map tools moved here from map_tools.py and the mixin's methods from
dialog.py (#195), unchanged. The methods still keep their state on the
dialog itself (aoi_geometry, current_extent, the buttons), so the dialog,
plugin.py and the tests reach them as before.
"""

import os

from qgis.PyQt.QtWidgets import (
    QMessageBox,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QWidget,
    QCheckBox,
    QToolButton,
    QMenu,
    QAction,
    QGroupBox,
    QTextEdit,
    QDoubleSpinBox,
    QGridLayout,
)
from qgis.PyQt.QtCore import Qt, QPoint, QObject, QEvent, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.core import (
    QgsApplication,
    QgsGeometry,
    QgsMapLayerType,
    QgsRectangle,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand

from ..logger import message_level
from . import settings_store

# Define colors for the rubber band
RB_STROKE = QColor(0, 120, 215)  # Blue color
RB_FILL = QColor(204, 235, 239, 100)  # Light blue with transparency
HIGHLIGHT_STROKE = QColor(255, 165, 0)  # Orange color for highlighting
HIGHLIGHT_FILL = QColor(255, 223, 186, 150)  # Light orange with transparency


class AoiHighlighter:
    """Class to highlight the selected AOI on the map canvas"""
    
    def __init__(self, canvas):
        """Initialize the highlighter"""
        self.canvas = canvas
        self.rubber_band = None
        
    def highlight_aoi(self, geometry=None, extent=None):
        """Display a highlighted AOI on the map canvas
        
        Args:
            geometry (QgsGeometry): The geometry to highlight
            extent (QgsRectangle): The extent to highlight if no geometry is provided
        """
        # Clear any existing rubber band
        if self.rubber_band:
            self.rubber_band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        else:
            # Create a new rubber band if needed
            self.rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        
        # Configure the rubber band
        self.rubber_band.setFillColor(HIGHLIGHT_FILL)
        self.rubber_band.setStrokeColor(HIGHLIGHT_STROKE)
        self.rubber_band.setWidth(2)
        
        if geometry:
            # Use provided geometry
            self.rubber_band.setToGeometry(geometry, None)
        elif extent:
            # Convert extent to polygon geometry
            rect_geom = QgsGeometry.fromRect(extent)
            self.rubber_band.setToGeometry(rect_geom, None)
        else:
            # No geometry or extent provided, just clear
            self.clear()
            return
            
        # Make sure the rubber band is visible
        self.rubber_band.show()
        
    def clear(self):
        """Clear the highlighted area"""
        if self.rubber_band:
            self.rubber_band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
            self.rubber_band.hide()
            # Remove rubber band from scene and delete it
            self.canvas.scene().removeItem(self.rubber_band)
            self.rubber_band = None
            # Refresh the canvas to show the change
            self.canvas.refresh()


class PolygonMapTool(QgsMapTool):
    """Map tool for drawing polygons"""
    polygonSelected = pyqtSignal(object)

    def __init__(self, canvas):
        QgsMapTool.__init__(self, canvas)

        self.canvas = canvas
        self.extent = None
        self.rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self.rubber_band.setFillColor(RB_FILL)
        self.rubber_band.setStrokeColor(RB_STROKE)
        self.rubber_band.setWidth(1)
        self.vertex_count = 1  # two points are dropped initially

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            if self.rubber_band is None or self.extent is None:
                return
            # Add the right-click point as the final vertex
            self.rubber_band.addPoint(event.mapPoint())
            # Update the geometry with the new point
            self.extent = self.rubber_band.asGeometry()
            # Validate geometry before firing signal
            self.extent.removeDuplicateNodes()
            self.polygonSelected.emit(self.extent)
            self.rubber_band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
            del self.rubber_band
            self.rubber_band = None
            self.vertex_count = 1  # two points are dropped initially
            return
        elif event.button() == Qt.MouseButton.LeftButton:
            if self.rubber_band is None:
                self.rubber_band = QgsRubberBand(
                    self.canvas, QgsWkbTypes.GeometryType.PolygonGeometry
                )
                self.rubber_band.setFillColor(RB_FILL)
                self.rubber_band.setStrokeColor(RB_STROKE)
                self.rubber_band.setWidth(1)
            self.rubber_band.addPoint(event.mapPoint())
            self.extent = self.rubber_band.asGeometry()
            self.vertex_count += 1

    def canvasMoveEvent(self, event):
        if self.rubber_band is None:
            pass
        elif not self.rubber_band.numberOfVertices():
            pass
        elif self.rubber_band.numberOfVertices() == self.vertex_count:
            if self.vertex_count == 2:
                mouse_vertex = self.rubber_band.numberOfVertices() - 1
                self.rubber_band.movePoint(mouse_vertex, event.mapPoint())
            else:
                self.rubber_band.addPoint(event.mapPoint())
        else:
            mouse_vertex = self.rubber_band.numberOfVertices() - 1
            self.rubber_band.movePoint(mouse_vertex, event.mapPoint())

    def finishPolygon(self):
        """Finish the polygon without adding a new point (for Enter key)"""
        if self.rubber_band is None or self.extent is None:
            return False
        if self.rubber_band.numberOfVertices() < 3:
            # Need at least 3 vertices for a valid polygon
            return False
        # Get the current geometry
        self.extent = self.rubber_band.asGeometry()
        # Validate geometry before firing signal
        self.extent.removeDuplicateNodes()
        self.polygonSelected.emit(self.extent)
        self.rubber_band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        del self.rubber_band
        self.rubber_band = None
        self.vertex_count = 1
        return True

    def cancelPolygon(self):
        """Cancel polygon drawing and clean up"""
        if self.rubber_band:
            self.rubber_band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
            del self.rubber_band
            self.rubber_band = None
        self.extent = None
        self.vertex_count = 1

    def deactivate(self):
        QgsMapTool.deactivate(self)
        # Emit deactivated signal
        self.deactivated.emit()


class RectangleMapTool(QgsMapTool):
    """Map tool for drawing a rectangular bounding box"""
    rectangleSelected = pyqtSignal(QgsRectangle)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self.start_point = None

        self.rubber_band = QgsRubberBand(canvas, QgsWkbTypes.GeometryType.PolygonGeometry)
        self.rubber_band.setFillColor(RB_FILL)
        self.rubber_band.setStrokeColor(RB_STROKE)
        self.rubber_band.setWidth(1)

    def canvasPressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.start_point = event.mapPoint()
        self.rubber_band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)

    def canvasMoveEvent(self, event):
        if not self.start_point:
            return

        end_point = event.mapPoint()
        rect = QgsRectangle(self.start_point, end_point)

        geom = QgsGeometry.fromRect(rect)
        self.rubber_band.setToGeometry(geom, None)

    def canvasReleaseEvent(self, event):
        if not self.start_point or event.button() != Qt.MouseButton.LeftButton:
            return

        end_point = event.mapPoint()
        rect = QgsRectangle(self.start_point, end_point)

        self.start_point = None
        self.rubber_band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)

        # Emit the rectangle
        self.rectangleSelected.emit(rect)

    def deactivate(self):
        QgsMapTool.deactivate(self)
        self.rubber_band.reset(QgsWkbTypes.GeometryType.PolygonGeometry)
        self.start_point = None
        self.deactivated.emit()


class AreaOfInterestMixin:
    """The Area of Interest methods of DataSourceDialog, which inherits them.

    Not usable on its own: the methods are the dialog's, and use its widgets,
    iface and QDialog methods through self.
    """

    class _CanvasKeyFilter(QObject):
        def __init__(self, dialog):
            super().__init__()
            self.dialog = dialog

        def eventFilter(self, obj, event):
            if event.type() != QEvent.Type.KeyPress:
                return False

            in_select_mode = getattr(self.dialog, "_in_feature_select_mode", False)
            in_draw_mode = getattr(self.dialog, "_in_polygon_draw_mode", False)

            if not in_select_mode and not in_draw_mode:
                return False

            if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
                if in_select_mode:
                    self.dialog.finish_feature_selection()
                elif in_draw_mode:
                    self.dialog.finish_polygon_draw()
                return True
            elif event.key() == Qt.Key.Key_Escape:
                if in_select_mode:
                    self.dialog.cancel_feature_selection()
                elif in_draw_mode:
                    self.dialog.cancel_polygon_draw()
                return True
            return False

    def _install_canvas_key_filter(self):
        if self._canvas_key_filter is None and self.iface and self.iface.mapCanvas():
            self._canvas_key_filter = self._CanvasKeyFilter(self)
            self.iface.mapCanvas().installEventFilter(self._canvas_key_filter)

    def _remove_canvas_key_filter(self):
        if self._canvas_key_filter is not None and self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().removeEventFilter(self._canvas_key_filter)
            self._canvas_key_filter = None

    def _release_selection_layer(self):
        """Disconnect from the layer used for Select Features and clear the
        selection the plugin made on it. Selections the user made on other
        layers, or before opening the dialog, are left alone."""
        layer = self._selection_layer
        self._selection_layer = None
        if layer is None:
            return
        try:
            layer.selectionChanged.disconnect(self.on_selection_changed)
        except (TypeError, RuntimeError):
            pass
        try:
            layer.removeSelection()
        except RuntimeError:
            pass  # layer was removed from the project

    def finish_feature_selection(self):
        """Confirm the current feature selection and use it as AOI"""
        self._in_feature_select_mode = False
        self._remove_canvas_key_filter()

        if self.iface and self.iface.mapCanvas():
            # The AOI has already been copied into dialog state; clear the
            # transient layer selection so it does not remain highlighted.
            self._release_selection_layer()

            # Restore pan tool
            self.iface.actionPan().trigger()

        # Re-show the dialog
        self.show()
        self.raise_()
        self.activateWindow()

    def cancel_feature_selection(self):
        """Cancel feature selection and clear any AOI"""
        self._in_feature_select_mode = False
        self._remove_canvas_key_filter()

        if self.iface and self.iface.mapCanvas():
            self._release_selection_layer()

            self.iface.actionPan().trigger()

        # Clear any AOI that was set
        self.aoi_geometry = None
        self.aoi_geometry_crs = None
        self.current_extent = None
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()
        self.update_extent_display("No area of interest selected")

        # Uncheck select button
        if self.select_button:
            self.select_button.setChecked(False)

        # Re-show the dialog
        self.show()
        self.raise_()
        self.activateWindow()

    def setup_area_of_interest(self):
        """Create and setup the Area of Interest group with Extent button"""
        # Create a container widget to hold the group box and extent display
        self.aoi_container = QWidget()
        container_layout = QVBoxLayout()
        container_layout.setContentsMargins(0, 0, 0, 0)

        # Create checkable group box - unchecked by default means use current view
        self.extent_group = QGroupBox("Area of Interest")
        self.extent_group.setCheckable(True)
        self.extent_group.setChecked(False)
        self.extent_group.toggled.connect(self.on_aoi_checkbox_toggled)
        extent_layout = QVBoxLayout()
        
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
        self.extent_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.extent_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.extent_button.setToolTip("Click the dropdown arrow to select an existing extent")
        self.extent_button.setCheckable(True)  # Make button checkable
        
        # Use the extents.svg icon from the icons folder
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
        self.draw_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.draw_button.setToolTip("Draw a custom polygon on the map")
        self.draw_button.setCheckable(True)  # Make button checkable
        # Use the extents.svg icon from the icons folder
        base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        icon_path = os.path.join(base_path, "icons", "extent-draw-polygon.svg")
        self.draw_button.setIcon(QIcon(icon_path))
        
        # Connect button click directly to polygon drawing
        self.draw_button.clicked.connect(self.start_polygon_draw)
        
        # Select Features button
        self.select_button = QToolButton()
        self.select_button.setText(" Select")
        self.select_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
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
                self.select_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView))
        except Exception:
            # If there's any error, use a generic icon
            from qgis.PyQt.QtWidgets import QStyle
            self.select_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView))
        
        # Connect select button to selection tool
        self.select_button.clicked.connect(self.start_feature_selection)

        # BBox button
        self.bbox_button = QToolButton()
        self.bbox_button.setText(" BBox")
        self.bbox_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.bbox_button.setToolTip("Enter a bounding box manually or draw one on the map")
        self.bbox_button.setCheckable(True)
        bbox_icon = QgsApplication.getThemeIcon("/mActionAddBasicRectangle.svg")
        if not bbox_icon.isNull():
            self.bbox_button.setIcon(bbox_icon)
        self.bbox_button.clicked.connect(self.start_bbox_mode)

        # Clear button
        self.clear_button = QToolButton()
        self.clear_button.setText(" Clear")
        self.clear_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.clear_button.setToolTip("Clear the current area of interest and selected features")
        # Use the clear icon if available or another suitable icon
        icon_path = os.path.join(base_path, "icons", "extent-clear.svg")
        if os.path.exists(icon_path):
            self.clear_button.setIcon(QIcon(icon_path))
        else:
            # Fallback to a standard icon if the custom one isn't available
            from qgis.PyQt.QtWidgets import QStyle
            self.clear_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        
        # Connect clear button to clear function
        self.clear_button.clicked.connect(self.clear_extent)
        
        # Add buttons to layout
        button_layout.addWidget(self.extent_button)
        button_layout.addWidget(self.draw_button)
        button_layout.addWidget(self.select_button)
        button_layout.addWidget(self.bbox_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addStretch()
        extent_layout.addLayout(button_layout)
        
        # Manual BBox panel (hidden by default)
        self.bbox_group = QGroupBox("Bounding Box (Map CRS)")
        bbox_layout = QVBoxLayout()
        grid = QGridLayout()

        self.xmin_spin = QDoubleSpinBox()
        self.ymin_spin = QDoubleSpinBox()
        self.xmax_spin = QDoubleSpinBox()
        self.ymax_spin = QDoubleSpinBox()

        for spin in (self.xmin_spin, self.ymin_spin, self.xmax_spin, self.ymax_spin):
            spin.setDecimals(6)
            spin.setRange(-99999999, 99999999)

        grid.addWidget(QLabel("X min:"), 0, 0)
        grid.addWidget(self.xmin_spin, 0, 1)
        grid.addWidget(QLabel("Y min:"), 0, 2)
        grid.addWidget(self.ymin_spin, 0, 3)
        grid.addWidget(QLabel("X max:"), 1, 0)
        grid.addWidget(self.xmax_spin, 1, 1)
        grid.addWidget(QLabel("Y max:"), 1, 2)
        grid.addWidget(self.ymax_spin, 1, 3)

        bbox_layout.addLayout(grid)

        bbox_btn_row = QHBoxLayout()
        self.bbox_draw_btn = QPushButton("Draw on map")
        self.bbox_draw_btn.clicked.connect(self.start_bbox_draw)
        self.bbox_apply_btn = QPushButton("Apply")
        self.bbox_apply_btn.clicked.connect(self.apply_manual_bbox)
        bbox_btn_row.addWidget(self.bbox_draw_btn)
        bbox_btn_row.addWidget(self.bbox_apply_btn)
        bbox_layout.addLayout(bbox_btn_row)

        self.bbox_group.setLayout(bbox_layout)
        self.bbox_group.setVisible(False)
        extent_layout.addWidget(self.bbox_group)

        # Set the layout to the group
        self.extent_group.setLayout(extent_layout)

        # Add the group to the container
        container_layout.addWidget(self.extent_group)

        # Add text display for extent OUTSIDE the QGroupBox so it won't be grayed out
        self.extent_display = QTextEdit()
        self.extent_display.setReadOnly(True)
        self.extent_display.setMaximumHeight(40)
        self.extent_display.setPlaceholderText(
            "Using current map view. Check the box above to define a custom area of interest."
        )
        container_layout.addWidget(self.extent_display)

        # Outside the group box too: clipping applies to the map view as well
        self.clip_checkbox = QCheckBox("Clip features to the area")
        self.clip_checkbox.setToolTip(
            "Cut features that extend past the area (such as large water or land "
            "cover polygons) at its edge. Unchecked, they are downloaded whole."
        )
        self.clip_checkbox.setChecked(
            settings_store.load_flag(settings_store.CLIP_TO_AREA)
        )
        container_layout.addWidget(self.clip_checkbox)

        self.aoi_container.setLayout(container_layout)
        return self.aoi_container

    def on_aoi_checkbox_toggled(self, checked):
        """Handle the Area of Interest checkbox being toggled"""
        if checked:
            # Clear any previous state and show instructions
            self.extent_display.clear()
            self.extent_display.setPlaceholderText(
                "No area of interest selected. Use the buttons above to select one."
            )
        else:
            # Clear AOI and show current view message
            self.clear_extent()
            self.extent_display.clear()
            self.extent_display.setPlaceholderText(
                "Using current map view. Check the box above to define a custom area of interest."
            )

    def start_bbox_mode(self):
        """Show the manual bbox entry panel"""
        # Clear any previous AOI first
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()
        self.aoi_geometry = None
        self.aoi_geometry_crs = None
        self.current_extent = None
        self.extent_display.clear()
        self.extent_display.setPlaceholderText("Enter bounding box coordinates or draw on map...")

        if self.bbox_group:
            self.bbox_group.setVisible(True)

        # Update button states
        self.extent_button.setChecked(False)
        self.draw_button.setChecked(False)
        self.select_button.setChecked(False)
        self.bbox_button.setChecked(True)

    def start_bbox_draw(self):
        """Activate the rectangle map tool to draw a bbox on the map"""
        if not self.iface or not self.iface.mapCanvas():
            return

        self.rectangle_tool = RectangleMapTool(self.iface.mapCanvas())
        self.rectangle_tool.rectangleSelected.connect(self.on_bbox_drawn)
        self.rectangle_tool.deactivated.connect(self.handle_bbox_tool_deactivated)
        self.iface.mapCanvas().setMapTool(self.rectangle_tool)

        # Hide dialog while drawing
        self.hide()

        self.iface.messageBar().pushMessage(
            "Draw BBox",
            "Click and drag to draw a bounding box. Release to confirm.",
            message_level("Info"),
            5,
        )

    def on_bbox_drawn(self, rect):
        """Handle a drawn rectangle from the map tool"""
        if self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().unsetMapTool(self.rectangle_tool)
            self.iface.actionPan().trigger()

        # Populate spin boxes
        self.xmin_spin.setValue(rect.xMinimum())
        self.ymin_spin.setValue(rect.yMinimum())
        self.xmax_spin.setValue(rect.xMaximum())
        self.ymax_spin.setValue(rect.yMaximum())

        # Apply it immediately
        self.apply_manual_bbox()

        # Re-show dialog
        self.show()
        self.raise_()
        self.activateWindow()

    def handle_bbox_tool_deactivated(self):
        """Re-show the dialog if the bbox tool is deactivated without drawing"""
        self.show()
        self.raise_()
        self.activateWindow()

    def apply_manual_bbox(self):
        """Apply manually entered bounding box values"""
        xmin = self.xmin_spin.value()
        ymin = self.ymin_spin.value()
        xmax = self.xmax_spin.value()
        ymax = self.ymax_spin.value()

        if xmin >= xmax or ymin >= ymax:
            QMessageBox.warning(
                self,
                "Invalid BBox",
                "Min values must be less than max values.",
            )
            return

        rect = QgsRectangle(xmin, ymin, xmax, ymax)
        self.aoi_geometry = None
        self.aoi_geometry_crs = None
        self.current_extent = rect
        self.update_extent_display("Manual BBox")

        if self.aoi_highlighter:
            self.aoi_highlighter.highlight_aoi(extent=rect)

        # Update button states
        self.extent_button.setChecked(False)
        self.draw_button.setChecked(False)
        self.select_button.setChecked(False)

    def use_canvas_extent(self):
        """Use the current map canvas extent as Area of Interest"""
        if self.iface and self.iface.mapCanvas():
            # Reset the polygon tool if active
            if self.polygon_tool:
                self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
                self.polygon_tool = None

            # Hide bbox panel
            if self.bbox_group:
                self.bbox_group.setVisible(False)

            # Disconnect from any active layer selection signals
            layer = self.iface.activeLayer()
            if layer and layer.type() == QgsMapLayerType.VectorLayer:
                self._disconnect_selection_changed(layer)
                
            # Clear any previous AOI visualization first
            if self.aoi_highlighter:
                self.aoi_highlighter.clear()

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
            self.bbox_button.setChecked(False)

    def use_active_layer_extent(self):
        """Use the active layer extent as Area of Interest"""
        if self.bbox_group:
            self.bbox_group.setVisible(False)

        layer = self.iface.activeLayer() if self.iface else None
        if not layer or layer.type() != QgsMapLayerType.VectorLayer:
            QMessageBox.warning(
                self,
                "Vector layer required",
                "Use Layer Extent requires an active vector layer.\n\n"
                "Tip: Click a vector layer in the Layers panel, "
                "or use Draw/BBox/current map viewpoint AOI instead."
            )
            self.extent_button.setChecked(False)
            return

        if self.iface and self.iface.activeLayer():
            # Reset the polygon tool if active
            if self.polygon_tool and self.iface.mapCanvas():
                self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
                self.polygon_tool = None

            # Disconnect from any active layer selection signals
            self._disconnect_selection_changed(self.iface.activeLayer())

            # Clear any previous AOI visualization first
            if self.aoi_highlighter:
                self.aoi_highlighter.clear()

            # Clear any previously drawn geometry
            self.aoi_geometry = None
            self.aoi_geometry_crs = None
            self.current_extent = None
            
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
                self.aoi_highlighter = AoiHighlighter(self.iface.mapCanvas())
                
            # Update the AOI highlighting
            if self.aoi_highlighter:
                self.aoi_highlighter.highlight_aoi(extent=self.current_extent)
                
            # Update button states
            self.extent_button.setChecked(True)
            self.draw_button.setChecked(False)
            self.select_button.setChecked(False)
            self.bbox_button.setChecked(False)

    def update_extent_display(self, source):
        """Update the extent display with the current extent information"""
        if self.current_extent:
            # Show either drawn geometry WKT or extent WKT
            wkt = ""
            if source == "Drawn Polygon" and self.aoi_geometry:
                # Ensure we're getting a standard WKT format
                from qgis.core import QgsWkbTypes
                geom = QgsGeometry(self.aoi_geometry)
                if geom.wkbType() == QgsWkbTypes.Type.MultiSurface:
                    geom = QgsGeometry.fromMultiPolygonXY(geom.asMultiPolygon())
                elif geom.wkbType() == QgsWkbTypes.Type.CurvePolygon:
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
    
    def load_last_extent(self):
        """Load the last used extent from QgsSettings if available"""
        last_extent_str = settings_store.load_last_used_extent()
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
        # Clear any previous AOI first
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()
        self.aoi_geometry = None
        self.aoi_geometry_crs = None
        self.current_extent = None
        self.extent_display.clear()
        self.extent_display.setPlaceholderText("Draw a polygon on the map...")

        if self.bbox_group:
            self.bbox_group.setVisible(False)

        if self.iface and self.iface.mapCanvas():
            # Clean up existing polygon tool if there is one
            if self.polygon_tool:
                self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
                self.polygon_tool = None

            # Disconnect from any active layer selection signals
            layer = self.iface.activeLayer()
            if layer and layer.type() == QgsMapLayerType.VectorLayer:
                self._disconnect_selection_changed(layer)

                # Clear any selected features in the active layer
                layer.removeSelection()

            # Create and set the polygon map tool
            self.polygon_tool = PolygonMapTool(self.iface.mapCanvas())
            self.iface.mapCanvas().setMapTool(self.polygon_tool)

            # Connect the signal
            self.polygon_tool.polygonSelected.connect(self.on_polygon_drawn)

            # Connect the deactivated signal to clean up the tool when another tool is selected
            self.polygon_tool.deactivated.connect(self.handle_polygon_tool_deactivated)

            # Enter polygon draw mode and install key filter
            self._in_polygon_draw_mode = True
            self._install_canvas_key_filter()

            # Hide dialog while drawing
            self.hide()

            # Show instructions
            self.iface.messageBar().pushMessage(
                "Draw Polygon",
                "Click to add vertices. Press Enter or right-click to finish (Esc to cancel).",
                message_level("Info"),
                10,
            )

            # Update button states
            self.extent_button.setChecked(False)
            self.draw_button.setChecked(True)
            self.select_button.setChecked(False)
            self.bbox_button.setChecked(False)

    def handle_polygon_tool_deactivated(self):
        """Handle the polygon tool being deactivated by another tool"""
        self._in_polygon_draw_mode = False
        self._remove_canvas_key_filter()
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

        # Clear draw mode and key filter
        self._in_polygon_draw_mode = False
        self._remove_canvas_key_filter()

        # Update button states - keep Draw checked to show current AOI source
        self.extent_button.setChecked(False)
        self.draw_button.setChecked(True)
        self.select_button.setChecked(False)
        self.bbox_button.setChecked(False)

        # Re-show the dialog
        self.show()
        self.raise_()
        self.activateWindow()

    def finish_polygon_draw(self):
        """Finish polygon drawing when Enter is pressed"""
        if self.polygon_tool:
            if self.polygon_tool.finishPolygon():
                # The polygonSelected signal will be emitted and on_polygon_drawn will be called
                pass
            else:
                # Not enough vertices yet, show message
                if self.iface:
                    self.iface.messageBar().pushMessage(
                        "Draw Polygon",
                        "Need at least 3 points to create a polygon. Keep clicking to add more points.",
                        message_level("Warning"),
                        3,
                    )

    def cancel_polygon_draw(self):
        """Cancel polygon drawing when Escape is pressed"""
        self._in_polygon_draw_mode = False
        self._remove_canvas_key_filter()

        if self.polygon_tool:
            self.polygon_tool.cancelPolygon()
            if self.iface and self.iface.mapCanvas():
                self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
                self.iface.actionPan().trigger()
            self.polygon_tool = None

        # Re-show the dialog
        self.show()
        self.raise_()
        self.activateWindow()

        # Uncheck the draw button
        self.draw_button.setChecked(False)

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
            if reprojected_geom.wkbType() == QgsWkbTypes.Type.MultiSurface:
                reprojected_geom = QgsGeometry.fromMultiPolygonXY(reprojected_geom.asMultiPolygon())
            elif reprojected_geom.wkbType() == QgsWkbTypes.Type.CurvePolygon:
                reprojected_geom = QgsGeometry.fromPolygonXY(reprojected_geom.asPolygon())
            
            return reprojected_geom
        
        # No reprojection needed, but still ensure the geometry is in a standard format
        from qgis.core import QgsWkbTypes
        geom = QgsGeometry(self.aoi_geometry)
        if geom.wkbType() == QgsWkbTypes.Type.MultiSurface:
            geom = QgsGeometry.fromMultiPolygonXY(geom.asMultiPolygon())
        elif geom.wkbType() == QgsWkbTypes.Type.CurvePolygon:
            geom = QgsGeometry.fromPolygonXY(geom.asPolygon())
        
        return geom

    def clear_extent(self):
        """Clear the current area of interest"""
        self._in_feature_select_mode = False
        self._in_polygon_draw_mode = False
        self._remove_canvas_key_filter()

        # Reset the polygon tool if active
        if self.polygon_tool and self.iface and self.iface.mapCanvas():
            self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
            self.polygon_tool = None

        # Hide bbox panel
        if self.bbox_group:
            self.bbox_group.setVisible(False)

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
                    self._disconnect_selection_changed(layer)
                        
                    # Clear the selection
                    layer.removeSelection()
            
            # If there's an active layer, connect to its selection changed signal
            active_layer = self.iface.activeLayer()
            if active_layer and active_layer.type() == 0:  # Vector layer
                self._disconnect_selection_changed(active_layer)
                
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
        self.bbox_button.setChecked(False)

    def start_feature_selection(self):
        """Start selecting features from the active layer"""
        # Clear any previous AOI first (before validation)
        if self.aoi_highlighter:
            self.aoi_highlighter.clear()
        self.aoi_geometry = None
        self.aoi_geometry_crs = None
        self.current_extent = None
        self.extent_display.clear()
        self.extent_display.setPlaceholderText("No features selected. Select features to define the area of interest.")

        if self.bbox_group:
            self.bbox_group.setVisible(False)

        layer = self.iface.activeLayer() if self.iface else None
        if not layer or layer.type() != QgsMapLayerType.VectorLayer:
            QMessageBox.warning(
                self,
                "Vector layer required",
                "Select Features requires an active vector layer.\n\n"
                "Tip: Click a vector layer in the Layers panel (or create a temporary AOI layer), "
                "or use Draw/BBox/current map viewpoint AOI instead."
            )
            # Reset UI state so it doesn't look active
            self.select_button.setChecked(False)
            return

        if self.iface and self.iface.mapCanvas():
            # Clean up existing polygon tool if there is one
            if self.polygon_tool:
                self.iface.mapCanvas().unsetMapTool(self.polygon_tool)
                self.polygon_tool = None

            # Clear any previous selection on the layer
            layer.removeSelection()

            # Disconnect any existing selection signal first to avoid multiple connections
            self._disconnect_selection_changed(layer)

            # Connect to the selection changed signal
            layer.selectionChanged.connect(self.on_selection_changed)
            self._selection_layer = layer

            # Enter feature select mode
            self._in_feature_select_mode = True
            self._install_canvas_key_filter()

            # Use QGIS's built-in selection tool
            self.iface.actionSelectRectangle().trigger()

            # Hide dialog while selecting
            self.hide()

            # Show instructions
            self.iface.messageBar().pushMessage(
                "Select Features",
                "Select one or more features (Shift=add). Press Enter when done (Esc to cancel).",
                message_level("Info"),
                10,
            )

            # Update button states
            self.extent_button.setChecked(False)
            self.draw_button.setChecked(False)
            self.select_button.setChecked(True)
            self.bbox_button.setChecked(False)

    def _disconnect_selection_changed(self, layer):
        """Drop our selectionChanged handler from `layer`, whether or not it is connected.

        Callers disconnect before connecting, or while tearing an AOI mode down,
        so none of the cases below is an error: there may be no active layer, or
        one with no selection to change (a raster), and PyQt raises TypeError
        when the signal was never connected to this slot and RuntimeError when
        the layer's C++ object has already been deleted.
        """
        signal = getattr(layer, "selectionChanged", None)
        if signal is None:
            return
        try:
            signal.disconnect(self.on_selection_changed)
        except (TypeError, RuntimeError):
            pass

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
            if combined_geometry.wkbType() == QgsWkbTypes.Type.MultiSurface:
                # Force convert to a standard format (MultiPolygon)
                combined_geometry = QgsGeometry.fromMultiPolygonXY(combined_geometry.asMultiPolygon())
            elif combined_geometry.wkbType() == QgsWkbTypes.Type.CurvePolygon:
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
            menu.exec(button_pos)

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
            
        # Connect to layer changed signal (disconnect first to avoid multiple connections)
        try:
            self.layer_combo.currentIndexChanged.disconnect(self.on_layer_changed)
        except TypeError:
            pass
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
            prev_layer = self.iface.activeLayer()
            if prev_layer and prev_layer.type() == QgsMapLayerType.VectorLayer:
                self._disconnect_selection_changed(prev_layer)
                prev_layer.removeSelection()
            
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

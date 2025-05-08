import os
from math import sqrt

from qgis.core import (
    QgsCircle,
    QgsGeometry,
    QgsPoint,
    QgsPointXY,
    QgsRectangle,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import QPointF, QRect, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

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
            self.rubber_band.reset(QgsWkbTypes.PolygonGeometry)
        else:
            # Create a new rubber band if needed
            self.rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        
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
            self.rubber_band.reset(QgsWkbTypes.PolygonGeometry)
            # Refresh the canvas to show the change
            self.canvas.refresh()


class PolygonMapTool(QgsMapTool):
    """Map tool for drawing polygons"""
    polygonSelected = pyqtSignal(object)

    def __init__(self, canvas):
        QgsMapTool.__init__(self, canvas)

        self.canvas = canvas
        self.extent = None
        self.rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.rubber_band.setFillColor(RB_FILL)
        self.rubber_band.setStrokeColor(RB_STROKE)
        self.rubber_band.setWidth(1)
        self.vertex_count = 1  # two points are dropped initially

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            if self.rubber_band is None or self.extent is None:
                return
            # Add the right-click point as the final vertex
            self.rubber_band.addPoint(event.mapPoint())
            # Update the geometry with the new point
            self.extent = self.rubber_band.asGeometry()
            # Validate geometry before firing signal
            self.extent.removeDuplicateNodes()
            self.polygonSelected.emit(self.extent)
            self.rubber_band.reset(QgsWkbTypes.PolygonGeometry)
            del self.rubber_band
            self.rubber_band = None
            self.vertex_count = 1  # two points are dropped initially
            return
        elif event.button() == Qt.LeftButton:
            if self.rubber_band is None:
                self.rubber_band = QgsRubberBand(
                    self.canvas, QgsWkbTypes.PolygonGeometry
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

    def deactivate(self):
        QgsMapTool.deactivate(self)
        # Emit deactivated signal
        self.deactivated.emit() 
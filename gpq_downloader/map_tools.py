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
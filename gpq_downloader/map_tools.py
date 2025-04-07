import os
from math import sqrt

from qgis.core import (
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


class RectangleMapTool(QgsMapTool):
    """Map tool for drawing rectangles"""
    extentSelected = pyqtSignal(object)

    def __init__(self, canvas):
        QgsMapTool.__init__(self, canvas)

        self.canvas = canvas
        self.extent = None
        self.dragging = False
        self.rubber_band = None
        self.select_rect = QRect()

    def canvasPressEvent(self, event):
        self.select_rect.setRect(0, 0, 0, 0)
        self.rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self.rubber_band.setFillColor(RB_FILL)
        self.rubber_band.setStrokeColor(RB_STROKE)
        self.rubber_band.setWidth(1)

    def canvasMoveEvent(self, event):
        if event.buttons() != Qt.LeftButton:
            return

        if not self.dragging:
            self.dragging = True
            self.select_rect.setTopLeft(event.pos())

        self.select_rect.setBottomRight(event.pos())
        self._set_rubber_band()

    def canvasReleaseEvent(self, event):
        # If the user simply clicked without dragging ignore this
        if not self.dragging:
            return

        # Set valid values for rectangle's width and height
        if self.select_rect.width() == 1:
            self.select_rect.setLeft(self.select_rect.left() + 1)
        if self.select_rect.height() == 1:
            self.select_rect.setBottom(self.select_rect.bottom() + 1)

        if self.rubber_band:
            self._set_rubber_band()

            self.rubber_band.reset(QgsWkbTypes.PolygonGeometry)
            del self.rubber_band
            self.rubber_band = None

        self.dragging = False

        # Emit the selected extent
        self.extentSelected.emit(self.extent)

    def _set_rubber_band(self):
        transform = self.canvas.getCoordinateTransform()

        ll = transform.toMapCoordinates(
            self.select_rect.left(), self.select_rect.bottom()
        )
        ur = transform.toMapCoordinates(
            self.select_rect.right(), self.select_rect.top()
        )

        if self.rubber_band:
            self.rubber_band.reset(QgsWkbTypes.PolygonGeometry)
            self.rubber_band.addPoint(ll, False)
            self.rubber_band.addPoint(QgsPointXY(ur.x(), ll.y()), False)
            self.rubber_band.addPoint(ur, False)
            self.rubber_band.addPoint(QgsPointXY(ll.x(), ur.y()), True)
            self.extent = QgsRectangle(ur, ll)


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
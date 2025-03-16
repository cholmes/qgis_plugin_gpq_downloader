from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.core import QgsWkbTypes, QgsGeometry, QgsPointXY, QgsRectangle, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

from . import logger

class MapToolDrawRectangle(QgsMapTool):
    """Map tool for drawing a rectangle on the map."""
    
    rectangleCreated = pyqtSignal(QgsRectangle)
    drawingCanceled = pyqtSignal()
    
    def __init__(self, canvas, dialog=None):
        QgsMapTool.__init__(self, canvas)
        self.canvas = canvas
        self.dialog = dialog
        self.rubberBand = None
        self.startPoint = None
        self.endPoint = None
        self.isEmittingPoint = False
        self.reset()
        
        # Set cursor
        self.setCursor(Qt.CrossCursor)
    
    def reset(self):
        """Reset the rubber band and points."""
        try:
            if self.rubberBand:
                self.rubberBand.reset()
                try:
                    self.canvas.scene().removeItem(self.rubberBand)
                except Exception as e:
                    logger.log(f"Error removing rubber band: {str(e)}")
                    
            self.rubberBand = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
            self.rubberBand.setColor(QColor(255, 0, 0, 100))
            self.rubberBand.setWidth(1)
            self.startPoint = None
            self.endPoint = None
            
            # Disable OK button when starting new rectangle
            if self.dialog:
                self.dialog.disable_ok_button()
                
        except Exception as e:
            import traceback
            logger.log(f"Error in reset: {str(e)}")
            logger.log(traceback.format_exc())
    
    def canvasPressEvent(self, event):
        """Handle canvas press event."""
        if event.button() == Qt.LeftButton:
            try:
                # First reset the rubber band (this sets startPoint and endPoint to None)
                self.reset()
                
                # Get the map coordinates from the event
                point = self.toMapCoordinates(event.pos())
                if point is None:
                    logger.log("Warning: toMapCoordinates returned None")
                    return
                    
                # Set the start and end points AFTER reset
                self.startPoint = QgsPointXY(point)
                self.endPoint = QgsPointXY(point)
                self.isEmittingPoint = True
                
                # Make sure startPoint is valid before using it
                if self.startPoint is None:
                    logger.log("Warning: startPoint is None")
                    return
                    
                # Get coordinates once to avoid multiple calls
                x = self.startPoint.x()
                y = self.startPoint.y()
                
                # Add the first point
                points = [
                    QgsPointXY(x, y),
                    QgsPointXY(x, y),
                    QgsPointXY(x, y),
                    QgsPointXY(x, y),
                ]
                self.rubberBand.setToGeometry(
                    QgsGeometry.fromPolygonXY([points]), None
                )
                self.rubberBand.show()
            except Exception as e:
                # Log the error and reset
                import traceback
                logger.log(f"Error in canvasPressEvent: {str(e)}")
                logger.log(traceback.format_exc())
                self.reset()
                self.isEmittingPoint = False
    
    def canvasMoveEvent(self, event):
        """Handle canvas move event."""
        if not self.isEmittingPoint or not self.startPoint:
            return
            
        try:
            # Get the map coordinates from the event
            point = self.toMapCoordinates(event.pos())
            if point is None:
                logger.log("Warning: toMapCoordinates returned None in canvasMoveEvent")
                return
                
            self.endPoint = QgsPointXY(point)  # Create a copy
            
            # Make sure both points are valid
            if self.startPoint is None or self.endPoint is None:
                logger.log("Warning: startPoint or endPoint is None in canvasMoveEvent")
                return
            
            # Get coordinates once to avoid multiple calls
            start_x = self.startPoint.x()
            start_y = self.startPoint.y()
            end_x = self.endPoint.x()
            end_y = self.endPoint.y()
            
            # Update the rubber band
            points = [
                QgsPointXY(start_x, start_y),
                QgsPointXY(end_x, start_y),
                QgsPointXY(end_x, end_y),
                QgsPointXY(start_x, end_y),
            ]
            self.rubberBand.setToGeometry(
                QgsGeometry.fromPolygonXY([points]), None
            )
        except Exception as e:
            # Log the error but don't reset - allow user to continue trying
            import traceback
            logger.log(f"Error in canvasMoveEvent: {str(e)}")
            logger.log(traceback.format_exc())
    
    def canvasReleaseEvent(self, event):
        """Handle canvas release event."""
        if event.button() == Qt.LeftButton:
            # Make sure we have valid start and end points
            if self.startPoint is None or self.endPoint is None:
                # Reset and return if points are invalid
                logger.log("Warning: startPoint or endPoint is None in canvasReleaseEvent")
                self.isEmittingPoint = False
                self.reset()
                return
                
            try:
                # Get coordinates once to avoid multiple calls
                start_x = self.startPoint.x()
                start_y = self.startPoint.y()
                end_x = self.endPoint.x()
                end_y = self.endPoint.y()
                
                # Create rectangle in map coordinates
                rectangle = QgsRectangle(
                    min(start_x, end_x),
                    min(start_y, end_y),
                    max(start_x, end_x),
                    max(start_y, end_y)
                )
                
                # Enable the OK button after drawing
                if self.dialog:
                    self.dialog.enable_ok_button()
                
                # Store the rectangle but don't emit signal yet
                self.currentRectangle = rectangle
                self.isEmittingPoint = False
                
            except Exception as e:
                # Log the error and reset
                import traceback
                logger.log(f"Error creating rectangle: {str(e)}")
                logger.log(traceback.format_exc())
                self.isEmittingPoint = False
                self.reset()
    
    def deactivate(self):
        """Deactivate the map tool."""
        self.isEmittingPoint = False
        self.reset()
        # Emit signal when tool is deactivated
        self.drawingCanceled.emit()
        super().deactivate() 
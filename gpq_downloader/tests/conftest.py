import os
import sys
import pytest
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsRectangle
from qgis.PyQt.QtCore import QCoreApplication, QObject
from qgis.PyQt.QtWidgets import QMainWindow

# Add the parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock QGIS Application
@pytest.fixture(scope="session")
def qgs_app():
    """QGIS application fixture"""
    qgs_app = QgsApplication([], False)
    qgs_app.initQgis()
    yield qgs_app
    qgs_app.exitQgis()

# Mock iface
class MockIface(QObject):
    def __init__(self):
        super().__init__()
        self.canvas = MockCanvas()
        self._window = QMainWindow()
        self.toolbar_icons = []  # Add this to track added icons
    
    def mapCanvas(self):
        return self.canvas
    
    def mainWindow(self):
        return self._window
    
    def addToolBarIcon(self, action):  # Add this method
        """Mock method for adding toolbar icons"""
        self.toolbar_icons.append(action)
    
    def removeToolBarIcon(self, action):  # Add this method too
        """Mock method for removing toolbar icons"""
        if action in self.toolbar_icons:
            self.toolbar_icons.remove(action)

class MockCanvas:
    def __init__(self):
        self.settings = MockMapSettings()
    
    def mapSettings(self):
        return self.settings
    
    def extent(self):
        return QgsRectangle(0, 0, 1, 1)

class MockMapSettings:
    def destinationCrs(self):
        return QgsCoordinateReferenceSystem("EPSG:4326")

@pytest.fixture
def mock_iface():
    """Mock iface fixture"""
    return MockIface()

# Sample test data
@pytest.fixture
def sample_bbox():
    """Sample bounding box fixture"""
    return QgsRectangle(1, 2, 3, 4)

@pytest.fixture
def sample_validation_results():
    """Sample validation results fixture"""
    return {
        "has_bbox": True,
        "bbox_column": "bbox",
        "geometry_column": "geometry",
        "schema": [
            ("id", "INTEGER", "YES", None, None, None),
            ("name", "VARCHAR", "YES", None, None, None),
            ("bbox", "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)", "YES", None, None, None),
            ("geometry", "GEOMETRY", "YES", None, None, None)
        ]
    }

@pytest.fixture
def sample_validation_results_no_bbox():
    """Sample validation results with no bbox fixture"""
    return {
        "has_bbox": False,
        "bbox_column": None,
        "geometry_column": "geometry",
        "schema": [
            ("id", "INTEGER", "YES", None, None, None),
            ("name", "VARCHAR", "YES", None, None, None),
            ("geometry", "GEOMETRY", "YES", None, None, None)
        ]
    } 
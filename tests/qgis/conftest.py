import os
import sys
from unittest.mock import MagicMock

import pytest
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsRectangle, QgsSettings
from qgis.PyQt.QtCore import QObject
from qgis.PyQt.QtWidgets import QMainWindow

# Add the repo root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from gpq_downloader import logger  # noqa: E402
from gpq_downloader.gui import user_sources_store  # noqa: E402

# Keys this plugin writes, under either of the two prefixes QSettings can
# render them with (see settings_store.py's module docstring).
_OUR_PREFIXES = ("plugins/gpq_downloader/", "gpq_downloader/")


@pytest.fixture(autouse=True)
def isolated_user_sources(tmp_path, monkeypatch):
    """Every qgis test reads/writes its own user_sources.json, not the
    developer's real one.

    user_sources_store.user_config_path() otherwise points inside this QGIS
    profile (QgsApplication.qgisSettingsDirPath()); autouse so old and new
    tests alike never touch that file, with no per-test fixture to remember.
    """
    monkeypatch.setattr(
        user_sources_store,
        "user_config_path",
        lambda: str(tmp_path / "user_sources.json"),
    )


@pytest.fixture
def restore_level():
    """Put the logging threshold back after a test that calls logger.set_level().

    The threshold is module state (logger.py must not read QgsSettings, so it
    cannot restore itself); any test that changes it leaks into every test
    that runs after it unless it is put back here.
    """
    before = logger.current_level()
    yield
    logger.set_level(before)


@pytest.fixture
def logged(monkeypatch):
    """Every (message, level) that reaches QgsMessageLog, via logger.py."""
    seen = []
    monkeypatch.setattr(
        logger, "QgsMessageLog",
        type("Stub", (), {"logMessage": staticmethod(lambda m, t, lvl: seen.append((m, lvl)))}),
    )
    return seen


@pytest.fixture
def clean_settings(qgs_app):
    """No gpq_downloader settings during the test; the user's own back after.

    Lives here because every tests/qgis file that touches stored settings needs
    it: HOME does not isolate QgsSettings on macOS, so a test that writes keys
    without this fixture writes into the developer's real QGIS profile.
    """
    def ours():
        return sorted(k for k in QgsSettings().allKeys() if k.startswith(_OUR_PREFIXES))

    settings = QgsSettings()
    saved = {k: settings.value(k) for k in ours()}
    for key in saved:
        settings.remove(key)
    yield settings
    for key in ours():
        settings.remove(key)
    for key, value in saved.items():
        settings.setValue(key, value)

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
        self.options_factories = []
        self.options_dialog_calls = []
        self.options_dialog_parents = []
        self.messages = []  # (title, text, level, duration) from pushMessage
        self._message_bar = MockMessageBar(self.messages)

    def mapCanvas(self):
        return self.canvas

    def mainWindow(self):
        return self._window

    def layerTreeView(self):
        # No layer-tree widget to mirror; DataSourceDialog only wires up
        # layer-order signals when this is truthy, so None skips that.
        return None

    def activeLayer(self):
        # populate_layer_combo() asks for this while the dialog is built.
        # Named explicitly rather than served by a catch-all __getattr__:
        # this fixture is shared, and a fixture that answers every call
        # cannot tell a test that the plugin asked for something that
        # QgisInterface does not have.
        return None

    def addToolBarIcon(self, action):  # Add this method
        """Mock method for adding toolbar icons"""
        self.toolbar_icons.append(action)

    def removeToolBarIcon(self, action):  # Add this method too
        """Mock method for removing toolbar icons"""
        if action in self.toolbar_icons:
            self.toolbar_icons.remove(action)

    def registerOptionsWidgetFactory(self, factory):
        self.options_factories.append(factory)

    def unregisterOptionsWidgetFactory(self, factory):
        if factory in self.options_factories:
            self.options_factories.remove(factory)

    def showOptionsDialog(self, parent=None, currentPage=""):
        self.options_dialog_calls.append(currentPage)
        self.options_dialog_parents.append(parent)

    def messageBar(self):
        return self._message_bar


class MockMessageBar:
    """Just enough of QgsMessageBar for check_duckdb()/install_duckdb() and
    plugin.py's folder-fallback warning: pushMessage, plus the
    createMessage/pushWidget/popWidget trio the DuckDB install flow uses.
    """

    def __init__(self, messages):
        self._messages = messages

    def pushMessage(self, *args, **kwargs):
        self._messages.append(args)

    def createMessage(self, *args, **kwargs):
        return MockMessageBarItem()

    def pushWidget(self, widget, level=0, duration=0):
        pass

    def popWidget(self, widget=None):
        pass


class MockMessageBarItem:
    """What createMessage() returns: just enough to add a button to its layout."""

    def __init__(self):
        self._layout = MagicMock()

    def layout(self):
        return self._layout


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
def mock_iface(qgs_app):
    """Mock iface fixture (creates widgets, so it needs the QGIS application)"""
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
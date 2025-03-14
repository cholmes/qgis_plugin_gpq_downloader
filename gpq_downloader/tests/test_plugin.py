import pytest
from unittest.mock import MagicMock, patch
from qgis.PyQt.QtWidgets import QAction
from pathlib import Path

from gpq_downloader.plugin import QgisPluginGeoParquet

def test_plugin_initialization(qgs_app, mock_iface):
    """Test plugin initialization"""
    plugin = QgisPluginGeoParquet(mock_iface)
    assert plugin.iface == mock_iface
    assert plugin.worker is None
    assert plugin.worker_thread is None
    assert isinstance(plugin.download_dir, Path)

def test_plugin_init_gui(qgs_app, mock_iface):
    """Test initGui method"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.initGui()
    
    # Check that action was created
    assert isinstance(plugin.action, QAction)
    assert plugin.action.text() == "Download GeoParquet Data"
    
    # Check that icon was added to toolbar
    assert len(mock_iface.toolbar_icons) == 1
    assert mock_iface.toolbar_icons[0] == plugin.action

def test_plugin_unload(qgs_app, mock_iface):
    """Test plugin unload"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.initGui()  # Add the icon first
    
    # Verify icon was added
    assert len(mock_iface.toolbar_icons) == 1
    
    # Unload plugin
    plugin.unload()
    
    # Check that icon was removed
    assert len(mock_iface.toolbar_icons) == 0

@patch('gpq_downloader.plugin.QThread')
def test_plugin_cleanup_thread(mock_thread, qgs_app, mock_iface):
    """Test thread cleanup"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.worker = MagicMock()
    plugin.worker_thread = MagicMock()
    
    plugin.cleanup_thread()
    assert plugin.worker is None
    assert plugin.worker_thread is None 
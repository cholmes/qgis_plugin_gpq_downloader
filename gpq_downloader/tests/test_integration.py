import pytest
import os
import sys
from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtWidgets import QApplication

from gpq_downloader.plugin import QgisPluginGeoParquet

@pytest.mark.skipif(not os.environ.get('RUN_INTEGRATION_TESTS'), reason="Integration tests not enabled")
def test_plugin_load(qgs_app, mock_iface):
    """Test that plugin loads properly"""
    plugin = QgisPluginGeoParquet(mock_iface)
    assert plugin is not None
    
    # Initialize plugin
    plugin.initGui()
    
    # Check that actions were created
    assert plugin.action is not None 

@pytest.mark.skipif(not os.environ.get('RUN_INTEGRATION_TESTS'), reason="Integration tests not enabled")
def test_plugin_unload(qgs_app, mock_iface):
    """Test that plugin unloads properly"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.initGui()
    
    # Unload the plugin
    plugin.unload()
    
    # Check that cleanup was successful
    assert plugin.worker is None
    assert plugin.worker_thread is None

@pytest.mark.skipif(not os.environ.get('RUN_INTEGRATION_TESTS'), reason="Integration tests not enabled")
def test_plugin_download_dir(qgs_app, mock_iface):
    """Test that plugin creates download directory"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Check that download directory exists
    assert plugin.download_dir.exists()
    assert plugin.download_dir.is_dir() 
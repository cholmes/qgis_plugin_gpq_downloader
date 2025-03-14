import pytest
from unittest.mock import MagicMock, patch, call
from qgis.PyQt.QtWidgets import QAction, QProgressDialog, QMessageBox, QFileDialog, QDialog
from qgis.core import QgsProject, QgsVectorLayer
from pathlib import Path

from gpq_downloader.plugin import QgisPluginGeoParquet
from gpq_downloader.dialog import DataSourceDialog

def test_plugin_run_with_active_download(qgs_app, mock_iface):
    """Test run method when a download is already in progress"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.worker = MagicMock()
    plugin.worker_thread = MagicMock()
    plugin.worker_thread.isRunning.return_value = True
    
    with patch('qgis.PyQt.QtWidgets.QMessageBox') as mock_message:
        plugin.run()
        mock_message.warning.assert_called_once()
        assert "Download in Progress" in mock_message.warning.call_args[0][1]

@patch('gpq_downloader.plugin.DataSourceDialog')
def test_plugin_run_dialog_rejected(mock_dialog, qgs_app, mock_iface):
    """Test run method when dialog is rejected"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    dialog_instance = MagicMock()
    dialog_instance.exec_.return_value = 0  # Rejected
    mock_dialog.return_value = dialog_instance
    
    plugin.run()
    
    dialog_instance.exec_.assert_called_once()
    assert not hasattr(plugin, 'worker') or plugin.worker is None
    assert not hasattr(plugin, 'worker_thread') or plugin.worker_thread is None

@patch('qgis.PyQt.QtWidgets.QProgressDialog')
@patch('qgis.PyQt.QtWidgets.QFileDialog')
@patch('gpq_downloader.plugin.DataSourceDialog')
def test_plugin_run_with_download(mock_dialog, mock_file_dialog, mock_progress, qgs_app, mock_iface, tmp_path):
    """Test run method with successful download setup"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Setup mock dialog
    dialog_instance = MagicMock()
    dialog_instance.exec_.return_value = 1  # Accepted
    dialog_instance.get_urls.return_value = ["https://example.com/test.parquet"]
    dialog_instance.overture_radio.isChecked.return_value = True
    mock_dialog.return_value = dialog_instance
    
    # Setup mock file dialog
    output_file = str(tmp_path / "test.parquet")
    mock_file_dialog.getSaveFileName.return_value = (output_file, "GeoParquet (*.parquet)")
    
    plugin.run()
    
    mock_file_dialog.getSaveFileName.assert_called_once()
    assert plugin.output_file == output_file

def test_plugin_handle_error(qgs_app, mock_iface):
    """Test error handling"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.progress_dialog = MagicMock()
    error_msg = "Test error message"
    
    with patch('qgis.PyQt.QtWidgets.QMessageBox') as mock_message:
        plugin.handle_error(error_msg)
        mock_message.critical.assert_called_once()
        assert error_msg == mock_message.critical.call_args[0][1]
        plugin.progress_dialog.close.assert_called_once()

def test_plugin_cancel_download(qgs_app, mock_iface):
    """Test download cancellation"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.worker = MagicMock()
    plugin.worker_thread = MagicMock()
    
    plugin.cancel_download()
    plugin.worker.kill.assert_called_once()
    plugin.cleanup_thread()
    assert not hasattr(plugin, 'worker') or plugin.worker is None
    assert not hasattr(plugin, 'worker_thread') or plugin.worker_thread is None

@patch('qgis.core.QgsVectorLayer')
def test_plugin_load_layer_success(mock_vector_layer, qgs_app, mock_iface):
    """Test successful layer loading"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    mock_layer = MagicMock()
    mock_layer.isValid.return_value = True
    mock_vector_layer.return_value = mock_layer
    
    mock_project = MagicMock()
    mock_project.addMapLayer = MagicMock()
    
    with patch('qgis.core.QgsProject.instance', return_value=mock_project):
        plugin.load_layer("test.gpkg")
        mock_project.addMapLayer.assert_called_once_with(mock_layer)

@patch('qgis.core.QgsVectorLayer')
def test_plugin_load_layer_invalid(mock_vector_layer, qgs_app, mock_iface):
    """Test loading invalid layer"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    mock_layer = MagicMock()
    mock_layer.isValid.return_value = False
    mock_vector_layer.return_value = mock_layer
    
    with patch('qgis.PyQt.QtWidgets.QMessageBox') as mock_message:
        plugin.load_layer("test.gpkg")
        mock_message.critical.assert_called_once()
        expected_msg = f"Failed to load the layer from test.gpkg"
        assert expected_msg == mock_message.critical.call_args[0][1]

def test_plugin_show_info(qgs_app, mock_iface):
    """Test info message display"""
    plugin = QgisPluginGeoParquet(mock_iface)
    test_message = "Test info message"
    
    with patch('qgis.PyQt.QtWidgets.QMessageBox') as mock_message:
        plugin.show_info(test_message)
        mock_message.information.assert_called_once()
        assert test_message == mock_message.information.call_args[0][1]

@patch('qgis.PyQt.QtWidgets.QProgressDialog')
@patch('qgis.PyQt.QtWidgets.QDialog')
def test_plugin_handle_large_file_warning(mock_dialog, mock_progress, qgs_app, mock_iface):
    """Test handling of large file warnings"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Setup worker mock
    plugin.worker = MagicMock()
    plugin.worker.dataset_url = "https://example.com/test.parquet"
    plugin.worker.extent = MagicMock()
    plugin.worker.validation_results = {"has_bbox": True}
    plugin.worker.remaining_queue = [("https://example.com/test.parquet", "output.parquet")]
    
    # Setup progress dialog
    plugin.progress_dialog = mock_progress.return_value
    
    # Setup warning dialog
    dialog_instance = mock_dialog.return_value
    dialog_instance.exec_.return_value = 2  # Proceed with GeoJSON
    
    plugin.handle_large_file_warning(5000)  # 5GB file
    
    dialog_instance.exec_.assert_called_once() 
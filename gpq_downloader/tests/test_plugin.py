import pytest
import datetime
from unittest.mock import MagicMock, patch, call
from qgis.PyQt.QtWidgets import QAction, QProgressDialog, QMessageBox, QFileDialog, QDialog, QVBoxLayout, QLabel
from qgis.core import QgsProject, QgsVectorLayer, QgsSettings, QgsCoordinateReferenceSystem, QgsRectangle
from pathlib import Path
from pytestqt import qtbot

from gpq_downloader.plugin import QgisPluginGeoParquet
from gpq_downloader.dialog import DataSourceDialog

def test_plugin_run_with_active_download(qgs_app, mock_iface):
    """Test run method when a download is already in progress"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.worker = MagicMock()
    plugin.worker_thread = MagicMock()
    plugin.worker_thread.isRunning.return_value = True
    
    with patch('gpq_downloader.plugin.QMessageBox.warning') as mock_warning:
        plugin.run()
        mock_warning.assert_called_once()
        assert "Download in Progress" in mock_warning.call_args[0][1]

@patch('gpq_downloader.plugin.DataSourceDialog')
def test_plugin_run_dialog_rejected(mock_dialog, qgs_app, mock_iface):
    """Test run method when dialog is rejected"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Setup mock dialog
    dialog_instance = MagicMock()
    dialog_instance.exec.return_value = QDialog.Rejected
    mock_dialog.return_value = dialog_instance
    
    plugin.run()
    
    dialog_instance.exec.assert_called_once()
    assert plugin.worker is None
    assert plugin.worker_thread is None

@patch('gpq_downloader.plugin.QgsSettings')
@patch('gpq_downloader.plugin.QFileDialog.getSaveFileName')
@patch('gpq_downloader.plugin.DataSourceDialog')
def test_plugin_run_with_download(mock_dialog, mock_save_dialog, mock_settings, qgs_app, mock_iface, tmp_path):
    """Test run method with successful download setup"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Setup mock dialog
    dialog_instance = MagicMock()
    dialog_instance.exec.return_value = QDialog.Accepted
    dialog_instance.get_urls.return_value = ["https://example.com/test.parquet?theme=buildings"]
    dialog_instance.overture_radio.isChecked.return_value = True
    mock_dialog.return_value = dialog_instance
    
    # Setup mock save dialog
    output_file = str(tmp_path / "test.parquet")
    mock_save_dialog.return_value = (output_file, "GeoParquet (*.parquet)")
    
    # Setup mock settings
    mock_settings_instance = MagicMock()
    mock_settings.return_value = mock_settings_instance
    
    # Mock datetime to avoid timestamp issues
    with patch('gpq_downloader.plugin.datetime') as mock_datetime:
        mock_datetime.datetime.now.return_value.strftime.return_value = "20230101_120000"
        
        # Mock the process_download_queue method to avoid actual processing
        with patch.object(plugin, 'process_download_queue'):
            plugin.run()
    
    mock_save_dialog.assert_called_once()

def test_plugin_handle_error(qgs_app, mock_iface):
    """Test error handling"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.progress_dialog = MagicMock()
    error_msg = "Test error"
    
    with patch('gpq_downloader.plugin.QMessageBox.critical') as mock_critical:
        plugin.handle_error(error_msg)
        mock_critical.assert_called_once()
        assert mock_critical.call_args[0][1] == "Error" or error_msg in mock_critical.call_args[0][1]
        plugin.progress_dialog.close.assert_called_once()

def test_plugin_update_progress(qgs_app, mock_iface):
    """Test progress updates"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.progress_dialog = MagicMock()
    
    plugin.update_progress("Test progress")
    plugin.progress_dialog.setLabelText.assert_called_once_with("Test progress")

def test_plugin_cancel_download(qgs_app, mock_iface):
    """Test download cancellation"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.worker = MagicMock()
    plugin.worker_thread = MagicMock()
    
    # Patch the cleanup_thread method to verify it's called
    with patch.object(plugin, 'cleanup_thread') as mock_cleanup:
        plugin.cancel_download()
        plugin.worker.kill.assert_called_once()
        mock_cleanup.assert_called_once()

@patch('gpq_downloader.plugin.QgsVectorLayer')
def test_plugin_load_layer_success(mock_vector_layer, qgs_app, mock_iface):
    """Test successful layer loading"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Setup mock layer
    mock_layer = MagicMock()
    mock_layer.isValid.return_value = True
    mock_vector_layer.return_value = mock_layer
    
    # Setup mock project
    mock_project = MagicMock()
    
    with patch('gpq_downloader.plugin.QgsProject.instance', return_value=mock_project):
        plugin.load_layer("test.gpkg")
        mock_project.addMapLayer.assert_called_once_with(mock_layer)

@patch('gpq_downloader.plugin.QgsVectorLayer')
def test_plugin_load_layer_invalid(mock_vector_layer, qgs_app, mock_iface):
    """Test loading invalid layer"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Setup mock layer
    mock_layer = MagicMock()
    mock_layer.isValid.return_value = False
    mock_vector_layer.return_value = mock_layer
    
    with patch('gpq_downloader.plugin.QMessageBox.critical') as mock_critical:
        plugin.load_layer("test.gpkg")
        mock_critical.assert_called_once()
        assert mock_critical.call_args[0][0] == mock_iface.mainWindow()
        assert mock_critical.call_args[0][1] == "Error" or "test.gpkg" in mock_critical.call_args[0][1]

def test_plugin_show_info(qgs_app, mock_iface):
    """Test info message display"""
    plugin = QgisPluginGeoParquet(mock_iface)
    test_message = "Test info"
    
    with patch('gpq_downloader.plugin.QMessageBox.information') as mock_info:
        plugin.show_info(test_message)
        mock_info.assert_called_once()
        assert mock_info.call_args[0][0] == mock_iface.mainWindow()
        assert mock_info.call_args[0][1] == "Success" or test_message in mock_info.call_args[0][1]

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
    
    # Mock worker thread to not be running
    plugin.worker_thread = MagicMock()
    plugin.worker_thread.isRunning.return_value = False
    
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

def test_handle_validation_complete_success(qgs_app, mock_iface, qtbot):
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Create a fake dialog and attach the expected attributes.
    fake_dialog = QDialog()
    qtbot.addWidget(fake_dialog)
    # Fake overture radio button; isChecked() returns True.
    fake_radio = MagicMock()
    fake_radio.isChecked.return_value = True
    fake_dialog.overture_radio = fake_radio
    
    # Fake overture combo box for theme selection.
    fake_combo = MagicMock()
    fake_combo.currentText.return_value = "castle"  # Any theme other than "base"
    fake_dialog.overture_combo = fake_combo

    # Use a valid dummy extent (avoid using a MagicMock)
    dummy_extent = QgsRectangle(0, 0, 10, 10)
    
    # Patch the file dialog: simulate user clicking "Save" by returning a valid filename.
    with patch('qgis.PyQt.QtWidgets.QFileDialog.getSaveFileName',
               return_value=("test_output.parquet", "GeoParquet (*.parquet)")) as mock_save_dialog:
        plugin.download_and_save = MagicMock()
        
        plugin.handle_validation_complete(
            success=True,
            message="",
            validation_results={},
            url="https://example.com/test.parquet",
            extent=dummy_extent,
            dialog=fake_dialog
        )
        
        mock_save_dialog.assert_called_once()
        plugin.download_and_save.assert_called_once()

def test_handle_validation_complete_cancel(qgs_app, mock_iface, qtbot):
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Create a fake dialog with the same expected attributes.
    fake_dialog = QDialog()
    qtbot.addWidget(fake_dialog)
    fake_radio = MagicMock()
    fake_radio.isChecked.return_value = True
    fake_dialog.overture_radio = fake_radio

    fake_combo = MagicMock()
    fake_combo.currentText.return_value = "castle"
    fake_dialog.overture_combo = fake_combo

    # Use a valid dummy extent instead of a MagicMock.
    dummy_extent = QgsRectangle(0, 0, 10, 10)

    # Simulate the file dialog being cancelled by returning empty strings.
    with patch('qgis.PyQt.QtWidgets.QFileDialog.getSaveFileName',
               return_value=("", "")) as mock_save_dialog:
        plugin.download_and_save = MagicMock()
        # Optionally, also patch the warning to confirm no warning is shown.
        with patch('gpq_downloader.plugin.QMessageBox.warning') as mock_warning:
            plugin.handle_validation_complete(
                success=True,
                message="",
                validation_results={},
                url="https://example.com/test.parquet",
                extent=dummy_extent,
                dialog=fake_dialog
            )
            mock_save_dialog.assert_called_once()
            plugin.download_and_save.assert_not_called()
            # In the cancel case, no warning message is expected.
            mock_warning.assert_not_called()

def test_handle_validation_complete_failure(qgs_app, mock_iface):
    plugin = QgisPluginGeoParquet(mock_iface)
    
    with patch('gpq_downloader.plugin.QMessageBox.warning') as mock_warning:
        plugin.handle_validation_complete(
            success=False,
            message="Validation failed",
            validation_results={},
            url="https://example.com/test.parquet",
            extent=MagicMock(),
            dialog=MagicMock()
        )
        mock_warning.assert_called_once_with(mock_iface.mainWindow(), "Validation Error", "Validation failed")

def test_create_progress_dialog(qgs_app, mock_iface):
    plugin = QgisPluginGeoParquet(mock_iface)
    progress_dialog = plugin.create_progress_dialog("Test Title", "Test Message")
    
    assert progress_dialog.windowTitle() == "Test Title"
    assert progress_dialog.labelText() == "Test Message" 

def test_setup_worker(qgs_app, mock_iface):
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.progress_dialog = MagicMock()  # Ensure progress_dialog is initialized
    dataset_url = "https://example.com/test.parquet"
    extent = MagicMock()
    output_file = "output.parquet"
    validation_results = {"has_bbox": True}
    
    worker, worker_thread = plugin.setup_worker(dataset_url, extent, output_file, validation_results)
    
    assert worker is not None
    assert worker_thread is not None
    assert worker.dataset_url == dataset_url
    assert worker.extent == extent
    assert worker.output_file == output_file
    assert worker.validation_results == validation_results 
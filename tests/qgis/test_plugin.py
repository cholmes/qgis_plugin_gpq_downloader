from unittest.mock import MagicMock, patch
from qgis.PyQt.QtWidgets import QAction
from pathlib import Path

from gpq_downloader.core.plan import DownloadJob
from gpq_downloader.plugin import QgisPluginGeoParquet


def hold_a_job(plugin, job=None):
    """Make the plugin's download manager busy without running anything."""
    job = job or DownloadJob("https://example.com/test.parquet", "out.parquet")
    plugin.downloads.queue.enqueue(job)
    plugin.downloads.queue.next_job()
    return job


@patch('gpq_downloader.plugin.DataSourceDialog')
def test_plugin_run_with_active_download(mock_dialog, qgs_app, mock_iface):
    """#47: a download in progress no longer refuses the dialog; what it asks
    for is queued behind the running one."""
    plugin = QgisPluginGeoParquet(mock_iface)
    hold_a_job(plugin)

    with patch('gpq_downloader.plugin.QMessageBox.warning') as mock_warning:
        plugin.run()
        mock_warning.assert_not_called()
    mock_dialog.return_value.show.assert_called_once()

@patch('gpq_downloader.plugin.DataSourceDialog')
def test_plugin_run_dialog_shown(mock_dialog, qgs_app, mock_iface):
    """Test run method shows dialog non-modally"""
    plugin = QgisPluginGeoParquet(mock_iface)

    # Setup mock dialog
    dialog_instance = MagicMock()
    mock_dialog.return_value = dialog_instance

    plugin.run()

    # Dialog should be shown non-modally (not exec)
    dialog_instance.show.assert_called_once()
    assert plugin.worker is None
    assert not plugin.downloads.is_busy

@patch('gpq_downloader.gui.settings_store.QgsSettings')
@patch('gpq_downloader.plugin.QFileDialog.getSaveFileName')
@patch('gpq_downloader.plugin.DataSourceDialog')
def test_plugin_handle_dialog_accepted(mock_dialog, mock_save_dialog, mock_settings, qgs_app, mock_iface, tmp_path):
    """Test handle_dialog_accepted with successful download setup"""
    plugin = QgisPluginGeoParquet(mock_iface)

    # Setup mock dialog
    dialog_instance = MagicMock()
    dialog_instance.get_urls.return_value = ["https://example.com/test.parquet?theme=buildings"]
    dialog_instance.overture_radio.isChecked.return_value = True
    dialog_instance.get_current_extent.return_value = None
    dialog_instance.extent_group.isChecked.return_value = False

    # Add explicit mocks for aoi properties to avoid the coordinate transformation issue
    dialog_instance.aoi_geometry = None
    dialog_instance.aoi_geometry_crs = None

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
            # Call handle_dialog_accepted directly instead of through run()
            plugin.handle_dialog_accepted(dialog_instance)

    mock_save_dialog.assert_called_once()

@patch('gpq_downloader.gui.settings_store.QgsSettings')
@patch('gpq_downloader.plugin.QFileDialog.getSaveFileName')
@patch('gpq_downloader.plugin.DataSourceDialog')
def test_plugin_queues_buildings_and_building_parts_separately(
    mock_dialog, mock_save_dialog, mock_settings, qgs_app, mock_iface, tmp_path
):
    """#80: two checked presets are two queue entries with distinct file names."""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.download_dir = tmp_path

    base = "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings"
    dialog_instance = MagicMock()
    dialog_instance.get_urls.return_value = [f"{base}/type=building/*", f"{base}/type=building_part/*"]
    dialog_instance.overture_radio.isChecked.return_value = True
    dialog_instance.get_current_extent.return_value = None
    dialog_instance.aoi_geometry = None
    dialog_instance.aoi_geometry_crs = None
    mock_dialog.return_value = dialog_instance

    # Accept the default path each time
    mock_save_dialog.side_effect = lambda parent, title, path, filters: (path, "GeoParquet (*.parquet)")

    with patch('gpq_downloader.plugin.datetime') as mock_datetime:
        mock_datetime.datetime.now.return_value.strftime.return_value = "20230101_120000"
        with patch.object(plugin, 'process_download_queue') as process:
            plugin.handle_dialog_accepted(dialog_instance)

    queue = process.call_args[0][0]
    assert [url for url, _ in queue] == dialog_instance.get_urls.return_value
    assert [Path(output).name for _, output in queue] == [
        "overture_buildings_20230101_120000.parquet",
        "overture_building_parts_20230101_120000.parquet",
    ]


def test_plugin_names_the_building_parts_layer_apart_from_buildings(qgs_app, mock_iface):
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.where_fragment = None
    plugin.clip_to_area = False
    base = "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings"
    names = []
    with patch.object(plugin.downloads, "enqueue", return_value=0) as enqueue:
        for type_str in ("building", "building_part"):
            plugin.process_download_queue([(f"{base}/type={type_str}/*", "out.parquet")], None)
            names.append(enqueue.call_args[0][0][0].layer_name)
    assert names == ["Overture Buildings", "Overture Building Parts"]


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
    plugin.progress_dialog = MagicMock()

    # The manager stops the running worker and drops the queue
    # (test_download_manager.py runs that on real tasks)
    with patch.object(plugin.downloads, 'cancel_all') as cancel_all:
        plugin.cancel_download()
        cancel_all.assert_called_once()
    plugin.progress_dialog.close.assert_called_once()

@patch('gpq_downloader.qgis_io.layers.QgsVectorLayer')
def test_plugin_load_layer_success(mock_vector_layer, qgs_app, mock_iface):
    """Test successful layer loading"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Setup mock layer
    mock_layer = MagicMock()
    mock_layer.isValid.return_value = True
    mock_vector_layer.return_value = mock_layer
    
    # Setup mock project
    mock_project = MagicMock()
    
    with patch('gpq_downloader.qgis_io.layers.QgsProject.instance', return_value=mock_project):
        plugin.load_layer("test.gpkg")
        mock_project.addMapLayer.assert_called_once_with(mock_layer)

@patch('gpq_downloader.qgis_io.layers.QgsVectorLayer')
def test_plugin_load_layer_invalid(mock_vector_layer, qgs_app, mock_iface):
    """Test loading invalid layer"""
    plugin = QgisPluginGeoParquet(mock_iface)
    
    # Setup mock layer
    mock_layer = MagicMock()
    mock_layer.isValid.return_value = False
    mock_vector_layer.return_value = mock_layer
    
    with patch('gpq_downloader.qgis_io.layers.QMessageBox.critical') as mock_critical:
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
    assert not plugin.downloads.is_busy
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
    
    # No download is running (the manager starts idle)
    assert not plugin.downloads.is_busy

    # Unload plugin
    plugin.unload()
    
    # Check that icon was removed
    assert len(mock_iface.toolbar_icons) == 0

def test_plugin_cleanup_thread(qgs_app, mock_iface):
    """Test thread cleanup"""
    plugin = QgisPluginGeoParquet(mock_iface)
    hold_a_job(plugin)

    plugin.cleanup_thread()
    assert plugin.worker is None
    assert not plugin.downloads.is_busy


from gpq_downloader.plugin import QgisPluginGeoParquet


def test_plugin_load(qgs_app, mock_iface):
    """Test that plugin loads properly"""
    plugin = QgisPluginGeoParquet(mock_iface)
    assert plugin is not None

    # Initialize plugin
    plugin.initGui()

    # Check that actions were created
    assert plugin.action is not None


def test_plugin_unload(qgs_app, mock_iface):
    """Test that plugin unloads properly"""
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.initGui()

    # Unload the plugin
    plugin.unload()

    # Check that cleanup was successful
    assert plugin.worker is None
    assert not plugin.downloads.is_busy


def test_plugin_download_dir(qgs_app, mock_iface):
    """Test that plugin creates download directory"""
    plugin = QgisPluginGeoParquet(mock_iface)

    # Check that download directory exists
    assert plugin.download_dir.exists()
    assert plugin.download_dir.is_dir()

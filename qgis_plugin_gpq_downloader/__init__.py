import os
import sys
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.PyQt.QtCore import QTimer
from qgis.core import QgsSettings, QgsApplication
from qgis.utils import iface, unloadPlugin, plugins

def classFactory(iface):
    return MigrationPlugin(iface)

class MigrationPlugin:
    def __init__(self, iface):
        self.iface = iface
        
    def initGui(self):
        # Wait a short time to ensure the UI is ready
        QTimer.singleShot(100, self.show_migration_dialog)
    
    def show_migration_dialog(self):
        """Show dialog when both plugins are active"""
        QMessageBox.information(
            self.iface.mainWindow(),
            "GeoParquet Downloader Plugin Update",
            "The GeoParquet Downloader plugin has been updated with a new directory structure.\n\n"
            "If you see duplicate buttons in your toolbar, please restart QGIS.\n\n"
            "To avoid seeing both plugins listed in your Plugin Manager, you can safely uninstall "
            "this version (0.6.0 or earlier) of the plugin."
        )
        
        self.deactivate_old_plugin()
    
    def deactivate_old_plugin(self):
        """Deactivate the old plugin"""
        # Mark the plugin as disabled in QGIS settings.
        settings = QgsSettings()
        settings.setValue("PythonPlugins/qgis_plugin_gpq_downloader", False)
        settings.sync()  # Force settings to be written
        
        # Attempt to unload the plugin if it's still in memory.
        if "qgis_plugin_gpq_downloader" in plugins:
            try:
                unloadPlugin("qgis_plugin_gpq_downloader")
            except Exception as e:
                print("Error unloading old plugin:", e)
        
        # Now, try renaming the plugin folder so that QGIS won't load it.
        try:
            plugins_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins")
            old_plugin_dir = os.path.join(plugins_dir, "qgis_plugin_gpq_downloader")
            disabled_plugin_dir = os.path.join(plugins_dir, "qgis_plugin_gpq_downloader_disabled")
            if os.path.exists(old_plugin_dir) and not os.path.exists(disabled_plugin_dir):
                os.rename(old_plugin_dir, disabled_plugin_dir)
        except Exception as e:
            print("Error renaming plugin directory:", e)
        
        # Optionally, force QGIS to update its list of available plugins.
        try:
            from qgis.utils import updateAvailablePlugins
            updateAvailablePlugins()
        except Exception as e:
            print("Error updating available plugins:", e)
        
        # Inform the user that a restart is needed for the changes to take effect fully.
        QMessageBox.information(
            self.iface.mainWindow(),
            "Plugin Deactivated",
            "The old GeoParquet Downloader plugin has been replaced by a new version.\n\n"
            "Please manually uninstall version 0.6.0 (or earlier) of the plugin.\n\n"
        )
    
    def unload(self):
        pass 
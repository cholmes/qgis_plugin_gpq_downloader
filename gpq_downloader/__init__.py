import os
import platform
import subprocess
import sys
import shutil
from qgis.PyQt.QtWidgets import QProgressBar, QMessageBox
from qgis.PyQt.QtCore import QCoreApplication, QTimer
from qgis.core import QgsTask, QgsApplication, QgsSettings
from qgis.utils import iface, loadPlugin, startPlugin, unloadPlugin, plugins

from gpq_downloader import logger

# Global flag to track installation status
_duckdb_ready = False


def check_for_old_plugin():
    """Check if the old plugin is installed and handle migration"""
    plugins_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), 'python', 'plugins')
    old_plugin_dir = os.path.join(plugins_dir, 'qgis_plugin_gpq_downloader')
    
    # Check if old plugin directory exists
    if os.path.exists(old_plugin_dir):
        logger.log("Found old plugin directory, handling migration")
        
        # Check if old plugin is active
        if 'qgis_plugin_gpq_downloader' in plugins:
            logger.log("Old plugin is active, showing migration dialog")
            # Show migration message
            QMessageBox.information(
                iface.mainWindow(),
                "GeoParquet Downloader Plugin Update",
                "The GeoParquet Downloader plugin has been updated with a new directory structure.\n\n"
                "The old version has been automatically deactivated.\n\n"
                "If you see duplicate buttons in your toolbar, please restart QGIS.\n\n"
                "To avoid seeing both plugins listed in your Plugin Manager, you can safely uninstall "
                "version 0.6.0 or earlier of the GeoParquet Downloader plugin."
            )
            
            # Deactivate old plugin
            deactivate_old_plugin()
        else:
            logger.log("Old plugin exists but is not active")
            # Just deactivate it in settings to prevent future loading
            settings = QgsSettings()
            settings.setValue("PythonPlugins/qgis_plugin_gpq_downloader", False)


def deactivate_old_plugin():
    """Attempt to disable the old qgis_plugin_gpq_downloader plugin."""
    logger.log("Deactivating old plugin")
    
    # Mark the old plugin as disabled in QGIS settings:
    settings = QgsSettings()
    settings.setValue("PythonPlugins/qgis_plugin_gpq_downloader", False)
    settings.sync()  # write immediately
    
    # Unload the plugin from memory if it's still loaded:
    if "qgis_plugin_gpq_downloader" in plugins:
        try:
            unloadPlugin("qgis_plugin_gpq_downloader")
            logger.log("Unloaded old plugin")
        except Exception as e:
            logger.log(f"Failed to unload old plugin: {str(e)}", 1)
    
    # As a fallback, rename the plugin folder so QGIS won't load it next time:
    try:
        plugins_dir = os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins")
        old_plugin_dir = os.path.join(plugins_dir, "qgis_plugin_gpq_downloader")
        disabled_plugin_dir = os.path.join(plugins_dir, "qgis_plugin_gpq_downloader_disabled")
        if os.path.exists(old_plugin_dir) and not os.path.exists(disabled_plugin_dir):
            os.rename(old_plugin_dir, disabled_plugin_dir)
            logger.log("Renamed old plugin directory")
    except Exception as e:
        logger.log(f"Error renaming plugin folder: {str(e)}", 1)


class DuckDBInstallerTask(QgsTask):
    def __init__(self, callback):
        # Simple initialization with just CanCancel flag
        super().__init__("Installing DuckDB", QgsTask.CanCancel)
        self.success = False
        self.message = ""
        self.exception = None
        self.callback = callback
        # logger.log("Task initialized")

    def run(self):
        # logger.log("Task run method started")
        try:
            logger.log("Starting DuckDB installation...")
            if platform.system() == "Windows":
                py_path = os.path.join(os.path.dirname(sys.executable), "python.exe")
            elif platform.system() == "Darwin":
                qgis_bin = os.path.dirname(sys.executable)
                possible_paths = [
                    os.path.join(qgis_bin, "python3"),
                    os.path.join(qgis_bin, "bin", "python3"),
                    os.path.join(qgis_bin, "Resources", "python", "bin", "python3"),
                ]
                py_path = next(
                    (path for path in possible_paths if os.path.exists(path)),
                    sys.executable,
                )
            else:
                py_path = sys.executable

            # logger.log(f"Using Python path: {py_path}")
            # logger.log(f"Running pip install command...")

            subprocess.check_call([py_path, "-m", "pip", "install", "--user", "duckdb"])

            # logger.log("Pip install completed, reloading modules...")
            import importlib

            importlib.invalidate_caches()

            self.success = True
            self.message = "DuckDB installed successfully"
            return True

        except subprocess.CalledProcessError as e:
            self.exception = e
            self.message = f"Pip install failed: {str(e)}"
            logger.log(f"Installation failed with error: {str(e)}")
            return False
        except Exception as e:
            self.exception = e
            self.message = f"Failed to install/upgrade DuckDB: {str(e)}"
            logger.log(f"Installation failed with error: {str(e)}", 2)
            return False

    def finished(self, result):
        global _duckdb_ready
        msg_bar = iface.messageBar()
        msg_bar.clearWidgets()

        if result and self.success:
            try:
                import duckdb

                self.message = f"DuckDB {duckdb.__version__} installed successfully"
            except ImportError:
                pass
            msg_bar.pushSuccess("Success", self.message)
            logger.log(self.message)
            _duckdb_ready = True
            if self.callback:
                self.callback()
        else:
            msg_bar.pushCritical("Error", self.message)
            logger.log(self.message)
            _duckdb_ready = False


def ensure_duckdb(callback=None):
    try:
        import duckdb

        version = duckdb.__version__
        from packaging import version as version_parser

        if version_parser.parse(version) >= version_parser.parse("1.1.0"):
            logger.log(f"DuckDB {version} already installed")
            global _duckdb_ready
            _duckdb_ready = True
            if callback:
                callback()
            return True
        else:
            logger.log(f"DuckDB {version} found but needs upgrade to 1.1.0+", 2)
            raise ImportError("Version too old")

    except ImportError:
        logger.log("DuckDB not found or needs upgrade, attempting to install/upgrade...", 2)
        try:
            msg_bar = iface.messageBar()
            progress = QProgressBar()
            progress.setMinimum(0)
            progress.setMaximum(0)
            progress.setValue(0)

            msg = msg_bar.createMessage("Installing DuckDB...")
            msg.layout().addWidget(progress)
            msg_bar.pushWidget(msg)
            QCoreApplication.processEvents()

            # Create and start the task
            task = DuckDBInstallerTask(callback)
            # logger.log("Created installer task")

            # Get the task manager and add the task
            task_manager = QgsApplication.taskManager()
            # logger.log(f"Task manager has {task_manager.count()} tasks")

            # Add task and check if it was added successfully
            task_manager.addTask(task)
            # logger.log(f"Task added successfully: {success}")

            # Check task status
            # logger.log(f"Task manager now has {task_manager.count()} tasks")
            # logger.log(f"Task description: {task.description()}")
            # logger.log(f"Task status: {task.status()}")

            # Schedule periodic status checks with guarded access
            def check_status():
                try:
                    status = task.status()
                except RuntimeError:
                    # logger.log("Task has been deleted, stopping status checks")
                    return

                # logger.log(f"Current task status: {status}")
                if status == QgsTask.Queued:
                    # logger.log("Task still queued, retriggering...")
                    try:
                        QgsApplication.taskManager().triggerTask(task)
                    except RuntimeError:
                        logger.log("Failed to trigger task, object likely deleted")
                        return
                    QTimer.singleShot(1000, check_status)
                elif status == QgsTask.Running:
                    # logger.log("Task is running")
                    QTimer.singleShot(1000, check_status)
                elif status == QgsTask.Complete:
                    logger.log("Task completed")

            # Start checking status after a short delay
            QTimer.singleShot(100, check_status)

            return True

        except Exception as e:
            msg_bar.clearWidgets()
            msg_bar.pushCritical("Error", f"Failed to install/upgrade DuckDB: {str(e)}", 2)
            logger.log(f"Failed to setup task with error: {str(e)}", 2)
            logger.log(f"Error type: {type(e)}", 2)
            import traceback

            logger.log(f"Traceback: {traceback.format_exc()}", 2)
            return False


# Instead of a standalone delayed_plugin_load, we now embed the real plugin loading logic
# into our dummy plugin.
class DummyPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.real_plugin = None

    def initGui(self):
        # Optionally show a temporary message or a "loading" placeholder
        self.iface.messageBar().pushInfo(
            "Info", "Plugin is loading… Please wait while dependencies install."
        )
        
        # Check for old plugin immediately
        QTimer.singleShot(100, check_for_old_plugin)

    def unload(self):
        # Unload the real plugin if it has been loaded.
        if self.real_plugin:
            self.real_plugin.unload()

    def loadRealPlugin(self):
        from gpq_downloader.plugin import QgisPluginGeoParquet

        self.real_plugin = QgisPluginGeoParquet(self.iface)
        # The real plugin adds the buttons and other UI elements
        self.real_plugin.initGui()
        self.iface.messageBar().pushSuccess(
            "Success", "Plugin fully loaded with all functionalities"
        )
        # logger.log("Real plugin loaded and UI initialized.")


def classFactory(iface):
    """Plugin entry point"""
    # Setup the path for duckdb
    plugin_dir = os.path.dirname(__file__)
    ext_libs_path = os.path.join(plugin_dir, "ext-libs")
    duckdb_path = os.path.join(ext_libs_path, "duckdb")

    # Add paths to sys.path if they're not already there
    for path in [ext_libs_path, duckdb_path]:
        if path not in sys.path:
            sys.path.insert(0, path)

    # Create the dummy plugin instance
    dummy_plugin = DummyPlugin(iface)

    # Schedule DuckDB installation and, once complete, load the real plugin UI.
    QTimer.singleShot(0, lambda: ensure_duckdb(dummy_plugin.loadRealPlugin))

    # Return the dummy plugin so QGIS has a valid plugin instance immediately
    return dummy_plugin

"""The GeoParquet Downloader options page: QGIS's Settings > Options tab.

A thin shell over settings_store.py's keys and loaders, plus the
core.output_formats and core.copy_options tables those keys choose from.
SettingsPage builds and loads the widgets; SettingsPageFactory is what
plugin.py registers with iface.registerOptionsWidgetFactory() so this page
appears in the Options dialog alongside every other plugin's.
"""

import os

from qgis.gui import QgsCollapsibleGroupBox, QgsFileWidget, QgsOptionsPageWidget, QgsOptionsWidgetFactory
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from gpq_downloader import logger
from gpq_downloader.core import copy_options, output_formats
from gpq_downloader.core.presets import load_presets

from . import settings_store, user_sources_store
from .sources_page import SourcesManagerWidget

# The page widget's objectName, and what iface.showOptionsDialog() is given
# to open directly on this page.
PAGE_OBJECT_NAME = "gpqDownloaderOptions"


class SettingsPage(QgsOptionsPageWidget):
    """The options page: every settings-panel key in one form."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName(PAGE_OBJECT_NAME)

        self.format_combo = QComboBox(self)
        self.format_combo.addItems(output_formats.format_names())

        self.folder_widget = QgsFileWidget(self)
        self.folder_widget.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)

        self.remember_checkbox = QCheckBox(self)

        self.log_combo = QComboBox(self)
        self.log_combo.addItems(settings_store.LOG_LEVELS)

        top_layout = QFormLayout()
        top_layout.addRow("Output format", self.format_combo)
        top_layout.addRow("Output folder", self.folder_widget)
        top_layout.addRow("Remember last folder used", self.remember_checkbox)
        top_layout.addRow("Logging level", self.log_combo)

        self.codec_combo = QComboBox(self)
        self.codec_combo.addItems(copy_options.CODECS)

        self.level_spin = QSpinBox(self)
        self.level_spin.setMinimum(copy_options.MIN_COMPRESSION_LEVEL)
        self.level_spin.setMaximum(copy_options.MAX_COMPRESSION_LEVEL)

        self.row_group_spin = QSpinBox(self)
        self.row_group_spin.setMinimum(0)
        self.row_group_spin.setMaximum(10_000_000)
        self.row_group_spin.setSpecialValueText("DuckDB default")

        self.sort_checkbox = QCheckBox(self)

        advanced_box = QgsCollapsibleGroupBox("Advanced", self)
        advanced_layout = QFormLayout()
        advanced_layout.addRow("Compression codec", self.codec_combo)
        advanced_layout.addRow("Compression level", self.level_spin)
        advanced_layout.addRow("Row group size", self.row_group_spin)
        advanced_layout.addRow("Spatial sort", self.sort_checkbox)
        advanced_box.setLayout(advanced_layout)

        general_layout = QVBoxLayout()
        general_layout.addLayout(top_layout)
        general_layout.addWidget(advanced_box)
        general_layout.addStretch()
        general_page = QWidget(self)
        general_page.setLayout(general_layout)

        self.sources_widget = SourcesManagerWidget(
            user_sources_store.load_user_config(), load_presets(), self
        )

        self.tabs = QTabWidget(self)
        self.tabs.addTab(general_page, "General")
        self.tabs.addTab(self.sources_widget, "Data Sources")

        page_layout = QVBoxLayout()
        page_layout.addWidget(self.tabs)
        self.setLayout(page_layout)

        self.codec_combo.currentTextChanged.connect(self._update_level_enabled)

        self._load()

    def _update_level_enabled(self, codec):
        """Grey the level out for a codec DuckDB takes no level with.

        The same constant the COPY builder gates on, so the control cannot
        offer a level the writer would then drop.
        """
        self.level_spin.setEnabled(codec == copy_options.LEVELLED_CODEC)

    def _load(self):
        """Fill every widget from settings_store, defaulted the same way."""
        format_name = settings_store.load_choice(
            settings_store.OUTPUT_FORMAT, output_formats.format_names(),
            output_formats.DEFAULT_FORMAT_NAME,
        )
        self.format_combo.setCurrentText(format_name)

        self.folder_widget.setFilePath(settings_store.load_text(settings_store.OUTPUT_FOLDER))
        self.remember_checkbox.setChecked(
            settings_store.load_flag(settings_store.REMEMBER_LAST_FOLDER, True)
        )
        self.log_combo.setCurrentText(
            settings_store.load_choice(
                settings_store.LOG_LEVEL, settings_store.LOG_LEVELS, settings_store.DEFAULT_LOG_LEVEL
            )
        )

        download_settings = settings_store.load_download_settings()
        self.codec_combo.setCurrentText(download_settings["compression_codec"])
        self.level_spin.setValue(download_settings["compression_level"])
        self.row_group_spin.setValue(download_settings["row_group_size"] or 0)
        self.sort_checkbox.setChecked(download_settings["spatial_sort"])
        self._update_level_enabled(self.codec_combo.currentText())

    def apply(self):
        """Write every widget's value back through settings_store."""
        settings_store.save_value(settings_store.OUTPUT_FORMAT, self.format_combo.currentText())
        settings_store.save_value(settings_store.OUTPUT_FOLDER, self.folder_widget.filePath())
        settings_store.save_value(
            settings_store.REMEMBER_LAST_FOLDER, self.remember_checkbox.isChecked()
        )
        settings_store.save_value(settings_store.LOG_LEVEL, self.log_combo.currentText())
        settings_store.save_value(settings_store.COMPRESSION_CODEC, self.codec_combo.currentText())
        settings_store.save_value(settings_store.COMPRESSION_LEVEL, self.level_spin.value())
        settings_store.save_value(settings_store.ROW_GROUP_SIZE, self.row_group_spin.value())
        settings_store.save_value(settings_store.SPATIAL_SORT, self.sort_checkbox.isChecked())
        # Takes effect on the next download without reopening anything (#93).
        logger.set_level(self.log_combo.currentText())
        try:
            user_sources_store.save_user_config(self.sources_widget.config())
        except OSError as exc:
            path = user_sources_store.user_config_path()
            QMessageBox.warning(
                self, "Could not save data sources",
                f"Could not write {path}:\n{exc}",
            )


class SettingsPageFactory(QgsOptionsWidgetFactory):
    """Registered with iface.registerOptionsWidgetFactory() in plugin.py."""

    def __init__(self):
        super().__init__()
        self.setTitle("GeoParquet Downloader")
        self.setKey(PAGE_OBJECT_NAME)
        # Without this the row in the Options tree shows a blank space where
        # every other page has an icon; the plugin's own toolbar icon is the
        # one users already associate with it.
        icon = QIcon(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "icons", "parquet-download.svg",
        ))
        if not icon.isNull():
            self.setIcon(icon)

    def createWidget(self, parent):
        return SettingsPage(parent)

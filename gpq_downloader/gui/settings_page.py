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
from qgis.PyQt.QtWidgets import QCheckBox, QComboBox, QFormLayout, QSpinBox, QVBoxLayout

from .. import logger
from ..core import copy_options, output_formats

from . import settings_store

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

        self.remember_format_checkbox = QCheckBox(self)

        self.folder_widget = QgsFileWidget(self)
        self.folder_widget.setStorageMode(QgsFileWidget.StorageMode.GetDirectory)

        self.remember_checkbox = QCheckBox(self)

        self.log_combo = QComboBox(self)
        self.log_combo.addItems(settings_store.LOG_LEVELS)

        # Each default sits above the tick that can override it, so the pair
        # reads as one decision. "Default" rather than plain "Output format"
        # because a tick below it makes the row a starting point rather than
        # what every download uses.
        top_layout = QFormLayout()
        top_layout.addRow("Default output format", self.format_combo)
        top_layout.addRow("Remember last format used", self.remember_format_checkbox)
        top_layout.addRow("Default output folder", self.folder_widget)
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

        page_layout = QVBoxLayout()
        page_layout.addLayout(top_layout)
        page_layout.addWidget(advanced_box)
        page_layout.addStretch()
        self.setLayout(page_layout)

        self.codec_combo.currentTextChanged.connect(self._update_level_enabled)
        self.remember_format_checkbox.toggled.connect(self._update_format_row)
        self.remember_checkbox.toggled.connect(self._update_folder_row)

        # The configured defaults, held apart from the widgets because a
        # greyed-out widget shows the last value used instead. _load() fills
        # them before anything can read them.
        self._configured_format = output_formats.DEFAULT_FORMAT_NAME
        self._configured_folder = ""
        # Whether each row is showing its configured default (live) or the
        # last value used (remembered). Tracked rather than read back off the
        # widget: isEnabled() is the *effective* state, so a disabled ancestor
        # would make a live row look remembered and apply() would then store a
        # stale default. Nothing disables an ancestor of this page in QGIS
        # today, which is exactly the kind of thing that changes quietly.
        self._format_row_live = True
        self._folder_row_live = True

        self._load()

    def _update_level_enabled(self, codec):
        """Grey the level out for a codec DuckDB takes no level with.

        The same constant the COPY builder gates on, so the control cannot
        offer a level the writer would then drop.
        """
        self.level_spin.setEnabled(codec == copy_options.LEVELLED_CODEC)

    def _configured_format_now(self):
        """The configured default format, wherever it currently lives.

        While the row is live it is the combo, so an edit the user has just
        made counts. Once "remember last format used" greys it out the combo
        shows something else, and the configured default is the value saved
        off at the moment it was greyed.
        """
        if self._format_row_live:
            self._configured_format = self.format_combo.currentText()
        return self._configured_format

    def _configured_folder_now(self):
        """The configured default folder. See _configured_format_now()."""
        if self._folder_row_live:
            self._configured_folder = self.folder_widget.filePath()
        return self._configured_folder

    def _update_format_row(self, remembering):
        """Grey the format out when the last format used overrides it.

        The greyed combo shows what the next download will really use - the
        format last written, or the configured default until something has
        been - so the page never displays a default that is not in effect.
        That displayed value is not written back on OK: the configured
        default survives in _configured_format, which is what apply() stores
        and what unticking brings back.
        """
        shown = self._configured_format_now()
        self._format_row_live = not remembering
        self.format_combo.setEnabled(not remembering)
        if remembering:
            last = settings_store.load_text(settings_store.LAST_OUTPUT_FORMAT)
            if last in output_formats.format_names():
                shown = last
        self.format_combo.setCurrentText(shown)

    def _update_folder_row(self, remembering):
        """Grey the folder out when the last folder used overrides it.

        The folder's counterpart to _update_format_row(), and the reason the
        page can say "Default output folder" honestly: ticking the box makes
        the row report ~/dude once that is where a download went, rather
        than going quietly inert while still showing the configured path.
        """
        shown = self._configured_folder_now()
        self._folder_row_live = not remembering
        self.folder_widget.setEnabled(not remembering)
        if remembering:
            shown = settings_store.load_text(settings_store.LAST_OUTPUT_FOLDER) or shown
        self.folder_widget.setFilePath(shown)

    def _load(self):
        """Fill every widget from settings_store, defaulted the same way."""
        self._configured_format = settings_store.load_choice(
            settings_store.OUTPUT_FORMAT, output_formats.format_names(),
            output_formats.DEFAULT_FORMAT_NAME,
        )
        self.format_combo.setCurrentText(self._configured_format)
        self.remember_format_checkbox.setChecked(
            settings_store.load_flag(settings_store.REMEMBER_LAST_FORMAT, False)
        )
        # setChecked only emits toggled() when it changes something, so the
        # row is settled explicitly. Both handlers are idempotent.
        self._update_format_row(self.remember_format_checkbox.isChecked())

        self._configured_folder = settings_store.load_text(settings_store.OUTPUT_FOLDER)
        self.folder_widget.setFilePath(self._configured_folder)
        self.remember_checkbox.setChecked(
            settings_store.load_flag(settings_store.REMEMBER_LAST_FOLDER, True)
        )
        self._update_folder_row(self.remember_checkbox.isChecked())
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
        # The configured defaults, not the widgets: a greyed-out row shows
        # the last value used, and writing that back would erase the default
        # it stands in for the first time anyone ticked the box.
        settings_store.save_value(settings_store.OUTPUT_FORMAT, self._configured_format_now())
        settings_store.save_value(settings_store.OUTPUT_FOLDER, self._configured_folder_now())
        settings_store.save_value(
            settings_store.REMEMBER_LAST_FORMAT, self.remember_format_checkbox.isChecked()
        )
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

"""The settings-panel keys and their loaders (settings_store.py).

The page that writes these keys arrives in Task 4; this pins the keys and the
load_* helpers that will back it, independent of any widget.

Each test starts with no gpq_downloader settings and puts the user's own back
afterwards, via the clean_settings fixture shared with test_dialog_settings.py
(tests/qgis/conftest.py).
"""

import pytest
from qgis.core import QgsSettings

from gpq_downloader import logger
from gpq_downloader.core import copy_options, output_formats
from gpq_downloader.gui import settings_store
from gpq_downloader.gui.dialog import DataSourceDialog
from gpq_downloader.gui.settings_page import PAGE_OBJECT_NAME, SettingsPage, SettingsPageFactory
from gpq_downloader.core.plan import DownloadJob
from gpq_downloader.plugin import QgisPluginGeoParquet

# The two prefixes QSettings can render our keys with; see conftest.py's
# clean_settings fixture and settings_store.py's module docstring.
PREFIXES = ("plugins/gpq_downloader/", "gpq_downloader/")


def test_nothing_stored_gives_the_documented_defaults(clean_settings):
    assert settings_store.load_download_settings() == {
        "compression_codec": "ZSTD",
        "compression_level": 15,
        "row_group_size": None,
        "spatial_sort": True,
    }


def test_stored_values_round_trip(clean_settings):
    settings_store.save_value(settings_store.COMPRESSION_CODEC, "SNAPPY")
    settings_store.save_value(settings_store.COMPRESSION_LEVEL, 9)
    settings_store.save_value(settings_store.ROW_GROUP_SIZE, 15000)
    settings_store.save_value(settings_store.SPATIAL_SORT, False)
    assert settings_store.load_download_settings() == {
        "compression_codec": "SNAPPY",
        "compression_level": 9,
        "row_group_size": 15000,
        "spatial_sort": False,
    }


@pytest.mark.parametrize("stored, expected", [(0, 1), (99, 22), (-5, 1), ("fifteen", 15)])
def test_a_level_outside_the_range_is_clamped(clean_settings, stored, expected):
    settings_store.save_value(settings_store.COMPRESSION_LEVEL, stored)
    assert settings_store.load_download_settings()["compression_level"] == expected


def test_an_unknown_stored_codec_reads_as_zstd(clean_settings):
    settings_store.save_value(settings_store.COMPRESSION_CODEC, "SQUEEZE")
    assert settings_store.load_download_settings()["compression_codec"] == "ZSTD"


@pytest.mark.parametrize("stored", [0, -1, "none"])
def test_a_non_positive_row_group_size_reads_as_unset(clean_settings, stored):
    settings_store.save_value(settings_store.ROW_GROUP_SIZE, stored)
    assert settings_store.load_download_settings()["row_group_size"] is None


def test_an_unknown_stored_output_format_reads_as_the_default(clean_settings):
    settings_store.save_value(settings_store.OUTPUT_FORMAT, "Shapefile")
    name = settings_store.load_choice(
        settings_store.OUTPUT_FORMAT, output_formats.format_names(),
        output_formats.DEFAULT_FORMAT_NAME,
    )
    assert name == "GeoParquet"


def test_remember_last_folder_is_on_when_nothing_is_stored(clean_settings):
    assert settings_store.load_flag(settings_store.REMEMBER_LAST_FOLDER, True) is True



def test_the_page_offers_every_format_and_codec(clean_settings, qgs_app):
    page = SettingsPage()
    assert [page.format_combo.itemText(i) for i in range(page.format_combo.count())] == \
        list(output_formats.format_names())
    assert [page.codec_combo.itemText(i) for i in range(page.codec_combo.count())] == \
        list(copy_options.CODECS)
    assert page.level_spin.minimum() == 1 and page.level_spin.maximum() == 22


def test_the_page_opens_on_the_stored_values(clean_settings, qgs_app):
    settings_store.save_value(settings_store.COMPRESSION_CODEC, "GZIP")
    settings_store.save_value(settings_store.SPATIAL_SORT, False)
    page = SettingsPage()
    assert page.codec_combo.currentText() == "GZIP"
    assert page.sort_checkbox.isChecked() is False


def test_the_page_opens_on_the_defaults_when_nothing_is_stored(clean_settings, qgs_app):
    page = SettingsPage()
    assert page.codec_combo.currentText() == "ZSTD"
    assert page.level_spin.value() == 15
    assert page.sort_checkbox.isChecked() is True
    assert page.remember_checkbox.isChecked() is True
    assert page.log_combo.currentText() == "Info"


def test_the_level_control_is_only_enabled_for_zstd(clean_settings, qgs_app):
    page = SettingsPage()
    assert page.level_spin.isEnabled() is True
    page.codec_combo.setCurrentText("SNAPPY")
    assert page.level_spin.isEnabled() is False
    page.codec_combo.setCurrentText("ZSTD")
    assert page.level_spin.isEnabled() is True


def test_apply_writes_what_the_download_settings_read_back(clean_settings, qgs_app):
    page = SettingsPage()
    page.codec_combo.setCurrentText("SNAPPY")
    page.level_spin.setValue(9)
    page.row_group_spin.setValue(15000)
    page.sort_checkbox.setChecked(False)
    page.apply()
    assert settings_store.load_download_settings() == {
        "compression_codec": "SNAPPY", "compression_level": 9,
        "row_group_size": 15000, "spatial_sort": False,
    }


def test_apply_pushes_the_combo_text_into_the_logger(clean_settings, qgs_app, restore_level):
    page = SettingsPage()
    page.log_combo.setCurrentText("Debug")
    page.apply()
    assert logger.current_level() == "Debug"


def test_apply_stores_nothing_under_a_key_the_dialog_also_writes(clean_settings, qgs_app):
    SettingsPage().apply()
    written = sorted(k for k in QgsSettings().allKeys() if k.startswith(PREFIXES))
    assert not any(k.endswith(("clip_to_area", "aoi_enabled", "attribute_filter")) for k in written)


def test_the_page_carries_the_name_the_gear_button_opens(clean_settings, qgs_app):
    assert SettingsPage().objectName() == PAGE_OBJECT_NAME


def test_init_gui_applies_the_stored_level(clean_settings, qgs_app, mock_iface, restore_level):
    settings_store.save_value(settings_store.LOG_LEVEL, "Warning")
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.initGui()
    assert logger.current_level() == "Warning"


def test_the_plugin_registers_the_factory_and_takes_it_away_again(clean_settings, restore_level, qgs_app, mock_iface):
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.initGui()
    assert len(mock_iface.options_factories) == 1
    assert isinstance(mock_iface.options_factories[0], SettingsPageFactory)
    plugin.unload()
    assert mock_iface.options_factories == []


def test_the_factory_makes_the_page_it_registers(clean_settings, qgs_app):
    """createWidget() is what QGIS calls to put the page in the dialog."""
    factory = SettingsPageFactory()
    page = factory.createWidget(None)
    assert isinstance(page, SettingsPage)
    assert page.objectName() == PAGE_OBJECT_NAME
    # The Options tree row carries the plugin's icon, not a blank space
    assert not factory.icon().isNull()


def test_the_gear_opens_the_plugins_own_options_page(clean_settings, qgs_app, mock_iface):
    dialog = DataSourceDialog(mock_iface.mainWindow(), mock_iface)
    dialog.settings_button.click()
    assert mock_iface.options_dialog_calls == [PAGE_OBJECT_NAME]
    # Parented on the dialog, so the Options window it opens is modal to it
    assert mock_iface.options_dialog_parents == [dialog]


def test_the_gear_does_nothing_without_an_iface(clean_settings, qgs_app):
    DataSourceDialog(None, None).settings_button.click()  # must not raise


def test_apply_persists_every_setting_it_shows(clean_settings, qgs_app):
    """All eight, read back through settings_store after apply().

    Deleting four of apply()'s eight writes broke nothing in the suite: the
    page's own state was asserted, but not that any of it survived the trip
    to QgsSettings. A dropped line or a typo'd key constant means the user
    picks GeoPackage, clicks OK, and still gets .parquet - this session and
    every later one.
    """
    page = SettingsPage()
    page.format_combo.setCurrentText("GeoPackage")
    page.folder_widget.setFilePath("/tmp/gpq-test-folder")
    page.remember_checkbox.setChecked(False)
    page.log_combo.setCurrentText("Debug")
    page.codec_combo.setCurrentText("GZIP")
    page.level_spin.setValue(7)
    page.row_group_spin.setValue(15000)
    page.sort_checkbox.setChecked(False)

    page.apply()

    assert settings_store.load_choice(
        settings_store.OUTPUT_FORMAT, output_formats.format_names(),
        output_formats.DEFAULT_FORMAT_NAME,
    ) == "GeoPackage"
    assert settings_store.load_text(settings_store.OUTPUT_FOLDER) == "/tmp/gpq-test-folder"
    assert settings_store.load_flag(settings_store.REMEMBER_LAST_FOLDER, True) is False
    assert settings_store.load_choice(
        settings_store.LOG_LEVEL, settings_store.LOG_LEVELS, settings_store.DEFAULT_LOG_LEVEL
    ) == "Debug"
    assert settings_store.load_download_settings() == {
        "compression_codec": "GZIP",
        "compression_level": 7,
        "row_group_size": 15000,
        "spatial_sort": False,
    }


def test_the_jobs_write_settings_reach_the_worker(clean_settings, qgs_app, mock_iface, tmp_path):
    """_make_worker hands the job's four write settings to the Worker.

    This is the seam between "the plugin read the settings" and "the COPY
    statement used them", and nothing asserted it: deleting all four keyword
    arguments from _make_worker broke no test in the suite, which would have
    left the panel showing SNAPPY while every file came out ZSTD 15.
    """
    plugin = QgisPluginGeoParquet(mock_iface)
    job = DownloadJob(
        url="https://example.com/a.parquet",
        output_file=str(tmp_path / "a.parquet"),
        layer_name=None,
        extent=None,
        aoi_geometry=None,
        clip_to_area=False,
        where_fragment=None,
        validation_results={},
        compression_codec="GZIP",
        compression_level=7,
        row_group_size=15000,
        spatial_sort=False,
    )
    worker = plugin._make_worker(job)
    assert (
        worker.compression_codec,
        worker.compression_level,
        worker.row_group_size,
        worker.spatial_sort,
    ) == ("GZIP", 7, 15000, False)

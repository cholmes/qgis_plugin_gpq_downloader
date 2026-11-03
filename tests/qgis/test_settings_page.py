"""The settings-panel keys and their loaders (settings_store.py).

The page that writes these keys arrives in Task 4; this pins the keys and the
load_* helpers that will back it, independent of any widget.

Each test starts with no gpq_downloader settings and puts the user's own back
afterwards, via the clean_settings fixture shared with test_dialog_settings.py
(tests/qgis/conftest.py).
"""

import pytest

from gpq_downloader.core import output_formats
from gpq_downloader.gui import settings_store


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

"""The plugin honours the settings-panel keys: starting folder, format,
remembered folder, and the per-batch write-settings snapshot (#1, Task 6).
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gpq_downloader.core import output_formats
from gpq_downloader.gui import settings_store
from gpq_downloader.logger import message_level
from gpq_downloader.plugin import QgisPluginGeoParquet


@pytest.fixture
def accepted(qgs_app):
    """Drive one handle_dialog_accepted() with a mocked save dialog.

    Patches QFileDialog.getSaveFileName to record (directory, initialFilter,
    filters) and return `chosen` (or the offered default path, unchanged).
    Returns (output_path, initial_filter_passed); the filters string passed
    is also available afterwards as `accepted.last_filters` (the return
    tuple stays 2-long so existing callers don't all need a third `_`).
    process_download_queue is patched away unless the test asks otherwise
    by passing patch_queue=False.
    """
    def run(plugin, chosen=None, patch_queue=True):
        dialog_instance = MagicMock()
        dialog_instance.get_urls.return_value = ["https://example.com/test.parquet"]
        dialog_instance.overture_radio.isChecked.return_value = False
        dialog_instance.sourcecoop_radio.isChecked.return_value = False
        dialog_instance.osm_radio.isChecked.return_value = False
        dialog_instance.get_current_extent.return_value = None
        dialog_instance.aoi_geometry = None
        dialog_instance.aoi_geometry_crs = None

        recorded = {}

        def fake_save(parent, title, directory, filters, initialFilter=""):
            recorded["directory"] = directory
            recorded["initialFilter"] = initialFilter
            recorded["filters"] = filters
            return (chosen or directory, initialFilter)

        with patch("gpq_downloader.plugin.QFileDialog.getSaveFileName", side_effect=fake_save):
            with patch("gpq_downloader.plugin.datetime") as mock_datetime:
                mock_datetime.datetime.now.return_value.strftime.return_value = "20230101_120000"
                if patch_queue:
                    with patch.object(plugin, "process_download_queue"):
                        plugin.handle_dialog_accepted(dialog_instance)
                else:
                    plugin.handle_dialog_accepted(dialog_instance)

        run.last_filters = recorded["filters"]
        return recorded["directory"], recorded["initialFilter"]

    return run


def test_the_configured_folder_and_format_set_the_default_save_path(clean_settings, mock_iface, tmp_path, accepted):
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(tmp_path))
    settings_store.save_value(settings_store.OUTPUT_FORMAT, "GeoPackage")
    path, initial_filter = accepted(QgisPluginGeoParquet(mock_iface))
    assert Path(path).parent == tmp_path
    assert Path(path).suffix == ".gpkg"
    assert initial_filter == "GeoPackage (*.gpkg)"


def test_a_configured_folder_that_does_not_exist_yet_is_created(clean_settings, mock_iface, tmp_path, accepted):
    wanted = tmp_path / "new" / "place"
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(wanted))
    path, _ = accepted(QgisPluginGeoParquet(mock_iface))
    assert wanted.is_dir() and Path(path).parent == wanted


def test_a_folder_that_cannot_be_created_falls_back_and_warns(clean_settings, mock_iface, tmp_path, accepted):
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    bad_folder = blocker / "under"
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(bad_folder))
    plugin = QgisPluginGeoParquet(mock_iface)
    path, _ = accepted(plugin)
    assert Path(path).parent == plugin.download_dir
    assert len(mock_iface.messages) == 1
    _title, text, level, _duration = mock_iface.messages[0]
    assert level == message_level("Warning")
    assert str(bad_folder) in text


def test_a_bad_remembered_folder_falls_through_to_the_configured_one(clean_settings, mock_iface, tmp_path, accepted):
    """The chain's middle rung: skip the remembered folder, use the configured one."""
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    bad_remembered = blocker / "under"
    configured = tmp_path / "configured"
    settings_store.save_value(settings_store.LAST_OUTPUT_FOLDER, str(bad_remembered))
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(configured))
    plugin = QgisPluginGeoParquet(mock_iface)
    path, _ = accepted(plugin)
    assert Path(path).parent == configured
    assert len(mock_iface.messages) == 1
    _title, text, level, _duration = mock_iface.messages[0]
    assert level == message_level("Warning")
    assert str(bad_remembered) in text
    # It says where it is going next, and that is where it went
    assert "the configured download folder" in text


def test_one_folder_under_both_keys_warns_once(clean_settings, mock_iface, tmp_path, accepted):
    """The steady state: a user who saved into their configured folder."""
    blocker = tmp_path / "a-file"
    blocker.write_text("")
    bad_folder = blocker / "under"
    settings_store.save_value(settings_store.LAST_OUTPUT_FOLDER, str(bad_folder))
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(bad_folder))
    plugin = QgisPluginGeoParquet(mock_iface)
    path, _ = accepted(plugin)
    assert Path(path).parent == plugin.download_dir
    assert len(mock_iface.messages) == 1
    assert str(plugin.download_dir) in mock_iface.messages[0][1]


def test_the_folder_last_saved_to_wins_when_remembering_is_on(clean_settings, mock_iface, tmp_path, accepted):
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(tmp_path / "configured"))
    settings_store.save_value(settings_store.LAST_OUTPUT_FOLDER, str(tmp_path))
    path, _ = accepted(QgisPluginGeoParquet(mock_iface))
    assert Path(path).parent == tmp_path


def test_the_configured_folder_wins_when_remembering_is_off(clean_settings, mock_iface, tmp_path, accepted):
    configured = tmp_path / "configured"
    settings_store.save_value(settings_store.OUTPUT_FOLDER, str(configured))
    settings_store.save_value(settings_store.LAST_OUTPUT_FOLDER, str(tmp_path))
    settings_store.save_value(settings_store.REMEMBER_LAST_FOLDER, False)
    path, _ = accepted(QgisPluginGeoParquet(mock_iface))
    assert Path(path).parent == configured


def test_saving_remembers_the_folder_that_was_used(clean_settings, mock_iface, tmp_path, accepted):
    picked_dir = tmp_path / "picked"
    picked_dir.mkdir()
    accepted(QgisPluginGeoParquet(mock_iface), chosen=str(picked_dir / "x.parquet"))
    assert settings_store.load_text(settings_store.LAST_OUTPUT_FOLDER) == str(picked_dir)


def test_with_nothing_stored_the_dialog_offers_todays_defaults(clean_settings, mock_iface, accepted):
    """Anti-goal #1: someone who never opens the panel must see exactly what
    the save dialog offered before it existed - every format in the filter,
    GeoParquet preselected, and the legacy default folder."""
    plugin = QgisPluginGeoParquet(mock_iface)
    path, initial_filter = accepted(plugin)
    assert Path(path).parent == plugin.download_dir
    assert Path(path).suffix == ".parquet"
    assert initial_filter == "GeoParquet (*.parquet)"
    assert accepted.last_filters == output_formats.dialog_filters()


def test_every_job_in_one_batch_carries_the_settings_as_they_were_at_enqueue(clean_settings, mock_iface, tmp_path):
    """The snapshot is taken once for the whole batch (plugin.py's
    process_download_queue), not re-read per job. Pinned two ways: every
    field - including row_group_size, which nothing else asserts - is
    checked on every job of a multi-URL batch, and load_download_settings
    is stubbed to return the *changed* settings on any call after the
    first, so an implementation that reads inside the job comprehension
    (one read per URL) would hand job 2 the drifted values and fail here,
    where an implementation that reads once outside the comprehension
    cannot.
    """
    settings_store.save_value(settings_store.COMPRESSION_CODEC, "SNAPPY")
    settings_store.save_value(settings_store.COMPRESSION_LEVEL, 9)
    settings_store.save_value(settings_store.ROW_GROUP_SIZE, 50000)
    settings_store.save_value(settings_store.SPATIAL_SORT, False)
    plugin = QgisPluginGeoParquet(mock_iface)

    real_read = settings_store.load_download_settings
    drifted = {
        "compression_codec": "GZIP",
        "compression_level": 1,
        "row_group_size": 1,
        "spatial_sort": True,
    }
    calls = []

    def read_once_then_drift():
        calls.append(True)
        return real_read() if len(calls) == 1 else dict(drifted)

    download_queue = [
        ("https://e.com/a.parquet", str(tmp_path / "a.parquet")),
        ("https://e.com/b.parquet", str(tmp_path / "b.parquet")),
    ]
    with patch.object(settings_store, "load_download_settings", side_effect=read_once_then_drift) as read:
        with patch.object(plugin.downloads, "enqueue", return_value=0) as enqueue:
            plugin.process_download_queue(download_queue, None)

    read.assert_called_once()
    jobs = enqueue.call_args[0][0]
    assert len(jobs) == 2
    for job in jobs:
        assert (
            job.compression_codec,
            job.compression_level,
            job.row_group_size,
            job.spatial_sort,
        ) == ("SNAPPY", 9, 50000, False)


def test_the_configured_format_wins_when_remembering_is_off(clean_settings, mock_iface, accepted):
    """Off by default, so a GeoPackage written once changes nothing."""
    settings_store.save_value(settings_store.OUTPUT_FORMAT, "FlatGeobuf")
    settings_store.save_value(settings_store.LAST_OUTPUT_FORMAT, "GeoPackage")
    plugin = QgisPluginGeoParquet(mock_iface)

    path, initial_filter = accepted(plugin)

    assert path.endswith(".fgb")
    assert initial_filter == "FlatGeobuf (*.fgb)"


def test_the_format_last_used_wins_when_remembering_is_on(clean_settings, mock_iface, accepted):
    settings_store.save_value(settings_store.OUTPUT_FORMAT, "FlatGeobuf")
    settings_store.save_value(settings_store.LAST_OUTPUT_FORMAT, "GeoPackage")
    settings_store.save_value(settings_store.REMEMBER_LAST_FORMAT, True)
    plugin = QgisPluginGeoParquet(mock_iface)

    path, initial_filter = accepted(plugin)

    assert path.endswith(".gpkg")
    assert initial_filter == "GeoPackage (*.gpkg)"


def test_a_stale_remembered_format_falls_through_to_the_configured_one(
    clean_settings, mock_iface, accepted
):
    """A format name this version no longer offers is not a crash.

    A settings file written by a later version, or one that dropped a
    format, leaves a name that matches nothing in the table.
    """
    settings_store.save_value(settings_store.OUTPUT_FORMAT, "FlatGeobuf")
    settings_store.save_value(settings_store.LAST_OUTPUT_FORMAT, "Shapefile")
    settings_store.save_value(settings_store.REMEMBER_LAST_FORMAT, True)
    plugin = QgisPluginGeoParquet(mock_iface)

    path, initial_filter = accepted(plugin)

    assert path.endswith(".fgb")
    assert initial_filter == "FlatGeobuf (*.fgb)"


def test_saving_remembers_the_format_that_was_written(clean_settings, mock_iface, tmp_path, accepted):
    """By the extension on disk, not the filter that was selected.

    A name typed as "x.gpkg" while the GeoParquet filter is showing is a
    GeoPackage - that is what copy_options dispatches on - so that is what
    gets remembered.
    """
    plugin = QgisPluginGeoParquet(mock_iface)

    accepted(plugin, chosen=str(tmp_path / "typed_by_hand.gpkg"))

    assert settings_store.load_text(settings_store.LAST_OUTPUT_FORMAT) == "GeoPackage"


def test_saving_under_an_unknown_extension_remembers_no_format(
    clean_settings, mock_iface, tmp_path, accepted
):
    """Nothing to preselect next time, so nothing is stored."""
    plugin = QgisPluginGeoParquet(mock_iface)

    accepted(plugin, chosen=str(tmp_path / "mystery.shp"))

    assert settings_store.load_text(settings_store.LAST_OUTPUT_FORMAT) == ""

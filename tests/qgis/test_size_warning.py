"""The large-GeoJSON warning's re-save offer.

worker.py stops before writing a .geojson it estimates at over 4GB and emits
file_size_warning; plugin.handle_large_file_warning() offers to write one of
the binary formats instead. That dialog used to carry its own hardcoded copy
of the format list, its extensions and its filter strings, and recorded
nothing about what the user chose - the one save path the settings panel
could not see.
"""

from unittest.mock import patch

import pytest
from qgis.PyQt.QtWidgets import QComboBox

from gpq_downloader.core import output_formats
from gpq_downloader.core.plan import DownloadJob
from gpq_downloader.gui import settings_store
from gpq_downloader.plugin import QgisPluginGeoParquet

URL = "https://example.com/big.parquet"


@pytest.fixture
def warned(qgs_app):
    """Drive one handle_large_file_warning() through the "Save As..." branch.

    Builds a plugin holding a job stopped at the warning, lets the real
    QComboBox be created so the dialog populates it for real, picks `index`
    in it the moment the dialog is shown, and answers the save dialog with
    `chosen`. Returns (plugin, combo, retry_mock).
    """
    def run(mock_iface, chosen, index=0, output_file="/tmp/gpq/big.geojson"):
        plugin = QgisPluginGeoParquet(mock_iface)
        plugin.downloads.queue.enqueue(DownloadJob(URL, output_file))
        plugin.downloads.queue.next_job()

        made = []

        def spy_combo(*args, **kwargs):
            combo = QComboBox(*args, **kwargs)
            made.append(combo)
            return combo

        def exec_and_pick(*args, **kwargs):
            # After the dialog is built and populated, as a user would.
            made[0].setCurrentIndex(index)
            return 1  # "Save As..."

        with patch("gpq_downloader.plugin.QDialog") as box, \
                patch("gpq_downloader.plugin.QComboBox", side_effect=spy_combo), \
                patch("gpq_downloader.plugin.QFileDialog.getSaveFileName") as save, \
                patch.object(plugin.downloads, "retry") as retry:
            box.return_value.exec.side_effect = exec_and_pick
            save.return_value = (chosen, "")
            plugin.handle_large_file_warning(5000.0)
            save_args = save.call_args
        return plugin, made[0], retry, save_args

    return run


def test_the_offered_formats_come_from_the_format_table(clean_settings, mock_iface, warned):
    """Not three filter strings written out in the dialog.

    A format added to the table used to reach the main save dialog and the
    settings panel but not this one - the dialog people meet precisely when
    the format is the thing that matters.
    """
    _, combo, _, _ = warned(mock_iface, chosen="/tmp/gpq/big.fgb")
    offered = [combo.itemText(i) for i in range(combo.count())]
    assert offered == [f.dialog_filter for f in output_formats.large_dataset_alternatives()]


@pytest.mark.parametrize("index, extension, name", [
    (0, ".fgb", "FlatGeobuf"),
    (1, ".gpkg", "GeoPackage"),
    (2, ".parquet", "GeoParquet"),
])
def test_the_chosen_format_sets_the_extension_and_the_filter(
    clean_settings, mock_iface, warned, index, extension, name
):
    """Both taken from the table by index, not parsed back out of the label.

    The old code recovered the extension with
    `selected_format.split("*")[1].rstrip(")")`, which only works for as long
    as every filter in the list is spelled "Name (*.ext)".
    """
    _, _, retry, save_args = warned(
        mock_iface, chosen=f"/tmp/gpq/big{extension}", index=index
    )
    offered_path, offered_filter = save_args.args[2], save_args.args[3]
    assert offered_path == f"/tmp/gpq/big{extension}"
    assert offered_filter == output_formats.format_for(name).dialog_filter
    assert retry.call_args.args[0].output_file == f"/tmp/gpq/big{extension}"


def test_re_saving_here_is_remembered_like_any_other_save(clean_settings, mock_iface, warned):
    """The settings panel's "remember last used" rows see this save too.

    This path wrote neither key, so a user who always ends up here through
    the size warning saw those rows keep reporting a folder and format from
    whichever earlier download had gone through the normal dialog.
    """
    warned(mock_iface, chosen="/tmp/gpq-elsewhere/big.gpkg", index=1)

    assert settings_store.load_text(settings_store.LAST_OUTPUT_FOLDER) == "/tmp/gpq-elsewhere"
    assert settings_store.load_text(settings_store.LAST_OUTPUT_FORMAT) == "GeoPackage"


def test_cancelling_the_re_save_remembers_nothing(clean_settings, mock_iface, qgs_app):
    """Backing out of the save dialog leaves both keys alone.

    Two things hold this up, and this test pins the outcome rather than
    either one: the call sits inside `if output_file:`, and
    _remember_destination("") would record nothing anyway, since Path("")
    has no absolute parent and no known extension. Moving the call outside
    the guard therefore still passes - deliberately, so that a later caller
    that forgets the guard is not a data bug.
    """
    plugin = QgisPluginGeoParquet(mock_iface)
    plugin.downloads.queue.enqueue(DownloadJob(URL, "/tmp/gpq/big.geojson"))
    plugin.downloads.queue.next_job()

    answers = [1, 0]  # "Save As...", then close the warning

    with patch("gpq_downloader.plugin.QDialog") as box, \
            patch("gpq_downloader.plugin.QFileDialog.getSaveFileName") as save, \
            patch.object(plugin.downloads, "skip"):
        box.return_value.exec.side_effect = lambda *a, **k: answers.pop(0)
        save.return_value = ("", "")  # cancelled
        plugin.handle_large_file_warning(5000.0)

    assert settings_store.load_text(settings_store.LAST_OUTPUT_FOLDER) == ""
    assert settings_store.load_text(settings_store.LAST_OUTPUT_FORMAT) == ""

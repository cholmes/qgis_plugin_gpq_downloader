"""The dialog's attribute filter row (#16), and the path its text takes: the
lexical check on OK, the ValidationWorker's DuckDB probe for a custom URL, and
the plugin handing it to the Worker.

DuckDB is a mock here; what real DuckDB makes of a filter is in
tests/core/test_attribute_filter.py.
"""

import gc
import time
from unittest.mock import MagicMock, patch

import duckdb
import pytest
from qgis.core import QgsApplication, QgsSettings
from qgis.PyQt.QtCore import QCoreApplication, QEvent
from qgis.PyQt.QtWidgets import QLineEdit

from gpq_downloader.dialog import DataSourceDialog
from gpq_downloader.plugin import QgisPluginGeoParquet

URL = "https://example.com/data/places.parquet"
SCHEMA = [
    ("id", "BIGINT", "YES", None, None, None),
    ("confidence", "DOUBLE", "YES", None, None, None),
    ("bbox", "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)", "YES", None, None, None),
    ("geometry", "GEOMETRY", "YES", None, None, None),
]
BINDER_ERROR = 'Binder Error: Referenced column "clas" not found in FROM clause!\nCandidate bindings: "class"'


@pytest.fixture
def mock_iface():
    """The MagicMock iface test_dialog.py uses: the dialog asks it for a canvas and layer tree."""
    iface = MagicMock()
    iface.mainWindow.return_value = None
    return iface


@pytest.fixture
def dialog(qgs_app, mock_iface):
    dialog = DataSourceDialog(None, mock_iface)
    dialog.extent_group.setChecked(False)  # a remembered AOI box would stop for a prompt
    dialog.accepted_count = 0

    def count_accept():
        dialog.accepted_count += 1

    dialog.accepted.connect(count_accept)
    yield dialog
    dialog.cleanup_validation()
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def wait_for(condition, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QgsApplication.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def test_the_filter_row_exists_and_starts_empty(dialog):
    assert isinstance(dialog.filter_input, QLineEdit)
    assert dialog.filter_input.text() == ""
    assert dialog.filter_input.placeholderText() == "confidence > 0.9 AND class = 'school'"
    assert "ANDed with the spatial filter" in dialog.filter_input.toolTip()
    assert dialog.get_where_fragment() is None


@pytest.mark.parametrize("text, expected", [
    ("", None),
    ("   ", None),
    ("confidence > 0.9", "confidence > 0.9"),
    ("  confidence > 0.9 \t", "confidence > 0.9"),
])
def test_get_where_fragment(dialog, text, expected):
    dialog.filter_input.setText(text)
    assert dialog.get_where_fragment() == expected


def test_the_filter_is_not_remembered_between_sessions(qgs_app, mock_iface, dialog):
    """A remembered filter would silently shrink every later download."""
    dialog.filter_input.setText("confidence > 0.9")
    with patch("gpq_downloader.gui.settings_store.QgsSettings") as settings:
        dialog.save_checkbox_states()
    stored = [c.args[1] for c in settings.return_value.setValue.call_args_list]
    assert "confidence > 0.9" not in stored
    assert not any("filter" in k.lower() for k in QgsSettings().allKeys() if "gpq_downloader" in k)
    second = DataSourceDialog(None, mock_iface)
    try:
        assert second.filter_input.text() == ""
    finally:
        second.close()
        second.deleteLater()


@pytest.mark.parametrize("text, message", [
    ("confidence > 0.9; DROP TABLE x", "cannot contain ';'"),
    ("WHERE confidence > 0.9", "Leave out the WHERE keyword"),
    ("class = 'school", "unclosed string"),
])
def test_a_malformed_filter_is_refused_before_any_validation(dialog, text, message):
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText(URL)
    dialog.filter_input.setText(text)
    with patch("gpq_downloader.gui.dialog.QMessageBox.warning") as warning, \
            patch("gpq_downloader.gui.dialog.ValidationWorker") as worker:
        dialog.validate_and_accept()
    warning.assert_called_once()
    assert warning.call_args[0][1] == "Validation Error"
    assert message in warning.call_args[0][2]
    worker.assert_not_called()
    assert dialog.accepted_count == 0


def test_a_preset_source_with_a_filter_is_accepted_without_a_probe(dialog):
    dialog.osm_radio.setChecked(True)
    for checkbox in dialog.osm_checkboxes.values():
        checkbox.setChecked(False)
    dialog.osm_checkboxes["pois"].setChecked(True)
    dialog.filter_input.setText("confidence > 0.9")
    with patch("gpq_downloader.gui.dialog.ValidationWorker") as worker:
        dialog.validate_and_accept()
    worker.assert_not_called()
    assert dialog.accepted_count == 1


def mock_connection(probe_error=None):
    """A DuckDB connection that knows the schema and fails the filter probe."""
    probes = []

    def execute(sql, *args):
        result = MagicMock()
        result.fetchall.return_value = SCHEMA if sql.startswith("DESCRIBE") else []
        result.fetchone.return_value = None
        if sql.startswith("SELECT 1 FROM read_parquet("):
            probes.append(sql)
            if probe_error is not None:
                raise probe_error
        return result

    conn = MagicMock()
    conn.execute.side_effect = execute
    return conn, probes


def test_a_filter_duckdb_rejects_is_shown_and_the_dialog_stays_open(dialog):
    conn, probes = mock_connection(duckdb.BinderException(BINDER_ERROR))
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText(URL)
    dialog.filter_input.setText("clas = 'school'")
    outcomes = []
    dialog.validation_complete.connect(lambda ok, msg, results: outcomes.append((ok, msg)))
    with patch("duckdb.connect", return_value=conn), \
            patch("gpq_downloader.gui.dialog.QMessageBox") as boxes:
        dialog.validate_and_accept()
        assert wait_for(lambda: boxes.warning.called or boxes.method_calls)
    message = "The attribute filter does not work on this data:\n\n" + BINDER_ERROR
    boxes.warning.assert_called_once_with(dialog, "Validation Error", message)
    assert outcomes == [(False, message)]
    assert probes == [f"SELECT 1 FROM read_parquet('{URL}') WHERE (clas = 'school') LIMIT 0"]
    assert dialog.accepted_count == 0
    assert dialog.validation_worker is None and dialog.validation_thread is None


def test_a_filter_duckdb_accepts_closes_the_dialog(dialog):
    conn, probes = mock_connection()
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText(URL)
    dialog.filter_input.setText("confidence > 0.9")
    with patch("duckdb.connect", return_value=conn), \
            patch("gpq_downloader.gui.dialog.QMessageBox") as boxes:
        dialog.validate_and_accept()
        assert wait_for(lambda: dialog.accepted_count or boxes.method_calls)
    assert not boxes.method_calls, boxes.method_calls
    assert dialog.accepted_count == 1
    assert probes == [f"SELECT 1 FROM read_parquet('{URL}') WHERE (confidence > 0.9) LIMIT 0"]


def accept_into_plugin(mock_iface, dialog, tmp_path):
    """Run the plugin's accept handler on `dialog`; returns the Worker class mock."""
    plugin = QgisPluginGeoParquet(mock_iface)
    output = str(tmp_path / "out.parquet")
    with patch("gpq_downloader.plugin.QFileDialog.getSaveFileName", return_value=(output, "GeoParquet (*.parquet)")), \
            patch("gpq_downloader.plugin.QProgressDialog"), \
            patch("gpq_downloader.plugin.QThread"), \
            patch("gpq_downloader.plugin.Worker") as worker:
        plugin.handle_dialog_accepted(dialog)
    worker.assert_called_once()
    return plugin, worker


@pytest.mark.parametrize("text, expected", [
    ("", None),
    ("   ", None),
    ("  confidence > 0.9 AND class = 'school' ", "confidence > 0.9 AND class = 'school'"),
])
def test_the_filter_reaches_the_worker(mock_iface, dialog, tmp_path, text, expected):
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText(URL)
    dialog.filter_input.setText(text)
    plugin, worker = accept_into_plugin(mock_iface, dialog, tmp_path)
    assert worker.call_args.args[0] == URL
    assert worker.call_args.kwargs["where_fragment"] == expected
    assert plugin.where_fragment == expected


def test_a_mocked_dialog_gives_the_worker_no_filter(mock_iface, tmp_path):
    """A MagicMock dialog answers get_where_fragment() with a MagicMock, which
    must not reach the SQL."""
    dialog = MagicMock()
    dialog.get_urls.return_value = [URL]
    dialog.get_current_extent.return_value = None
    dialog.aoi_geometry = None
    for radio in ("overture_radio", "sourcecoop_radio", "osm_radio"):
        getattr(dialog, radio).isChecked.return_value = False
    _, worker = accept_into_plugin(mock_iface, dialog, tmp_path)
    assert worker.call_args.kwargs["where_fragment"] is None


def test_the_filter_survives_the_large_geojson_restart(mock_iface):
    """Proceeding past the GeoJSON size warning starts a new Worker; the
    filter goes with it."""
    plugin = QgisPluginGeoParquet(mock_iface)
    old = MagicMock(where_fragment="confidence > 0.9", remaining_queue=[], layer_name=None,
                    aoi_geometry=None, clip_to_area=False)
    plugin.worker = old
    with patch("gpq_downloader.plugin.QDialog") as box, \
            patch.object(plugin, "cleanup_thread"), \
            patch.object(plugin, "_start_worker") as start:
        box.return_value.exec.return_value = 2  # "Proceed with GeoJSON anyway"
        plugin.handle_large_file_warning(5000.0)
    start.assert_called_once()
    assert start.call_args.kwargs["where_fragment"] == "confidence > 0.9"
    assert start.call_args.kwargs["size_warning_accepted"] is True

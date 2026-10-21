"""The dialog's background work (#197): the Overture release lookup goes
through QGIS's network stack, and a custom URL's validation runs on a QgsTask
that Cancel stops.
"""

import gc
import socket
import subprocess
import sys
import threading
import time
import weakref
from unittest.mock import MagicMock, patch

import duckdb
import pytest
from qgis.core import QgsApplication, QgsBlockingNetworkRequest
from qgis.PyQt.QtCore import QByteArray, QCoreApplication, QEvent, QObject, pyqtSignal

import gpq_downloader.gui.dialog as dialog_module
from gpq_downloader.dialog import DataSourceDialog
from gpq_downloader.gui.dialog import OVERTURE_CATALOG_URL, ReleaseLookupError, latest_overture_release
from gpq_downloader.qgis_io import manager as manager_module

URL = "https://example.com/data/places.parquet"


@pytest.fixture
def mock_iface():
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
    if not getattr(dialog, "closed_by_test", False):
        dialog.close()  # a second closeEvent would disconnect its slots twice
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


# The release lookup


def test_the_release_lookup_does_not_use_requests():
    """requests ignores QGIS's proxy and authentication settings."""
    assert "requests" not in vars(dialog_module)


def fake_fetch(code, content=b"", message=""):
    fetch = MagicMock()
    fetch.get.return_value = code
    fetch.errorMessage.return_value = message
    fetch.reply.return_value.content.return_value = QByteArray(content)
    cls = MagicMock(return_value=fetch)
    cls.ErrorCode = QgsBlockingNetworkRequest.ErrorCode
    return cls, fetch


def test_the_latest_release_comes_from_the_stac_catalog():
    cls, fetch = fake_fetch(QgsBlockingNetworkRequest.ErrorCode.NoError, b'{"latest": "2026-09-23.1"}')
    with patch("gpq_downloader.gui.dialog.QgsBlockingNetworkRequest", cls):
        assert latest_overture_release() == "2026-09-23.1"
    request = fetch.get.call_args[0][0]
    assert request.url().toString() == OVERTURE_CATALOG_URL == "https://stac.overturemaps.org/catalog.json"
    assert request.transferTimeout() == 30000  # requests.get's timeout=30


@pytest.mark.parametrize("code, content, message, expected", [
    (QgsBlockingNetworkRequest.ErrorCode.NetworkError, b"", "Host stac.overturemaps.org not found",
     "Could not look up the latest Overture release: Host stac.overturemaps.org not found"),
    (QgsBlockingNetworkRequest.ErrorCode.TimeoutError, b"", "Operation canceled",
     "Could not look up the latest Overture release: Operation canceled"),
    (QgsBlockingNetworkRequest.ErrorCode.NoError, b"<html>", "", "Could not read the latest Overture release"),
    (QgsBlockingNetworkRequest.ErrorCode.NoError, b"{}", "", "Could not read the latest Overture release"),
])
def test_a_failed_lookup_raises(code, content, message, expected):
    cls, _ = fake_fetch(code, content, message)
    with patch("gpq_downloader.gui.dialog.QgsBlockingNetworkRequest", cls):
        with pytest.raises(ReleaseLookupError, match=expected):
            latest_overture_release()


def test_a_failed_lookup_keeps_the_dialog_open(dialog):
    dialog.overture_radio.setChecked(True)
    dialog.overture_checkboxes["buildings"].setChecked(True)
    with patch("gpq_downloader.gui.dialog.latest_overture_release",
               side_effect=ReleaseLookupError("Could not look up the latest Overture release: offline")), \
            patch("gpq_downloader.gui.dialog.QMessageBox.warning") as warning:
        dialog.validate_and_accept()
    warning.assert_called_once_with(
        dialog, "Validation Error", "Could not look up the latest Overture release: offline"
    )
    assert dialog.accepted_count == 0


@pytest.fixture
def catalog_server(tmp_path):
    """A local HTTP server, in its own process, serving a STAC catalog."""
    (tmp_path / "catalog.json").write_text('{"type": "Catalog", "latest": "2026-10-15.0"}')
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        yield f"http://127.0.0.1:{port}/catalog.json"
    finally:
        server.terminate()
        server.wait(10)


def test_the_lookup_through_the_real_network_stack(qgs_app, catalog_server):
    with patch("gpq_downloader.gui.dialog.OVERTURE_CATALOG_URL", catalog_server):
        assert latest_overture_release() == "2026-10-15.0"


# Validation on a QgsTask


class BlocksUntilInterrupted:
    """A DuckDB connection whose DESCRIBE runs until interrupt()."""

    def __init__(self):
        self.describing = threading.Event()
        self.interrupted = threading.Event()

    def execute(self, sql, *args):
        if sql.startswith("DESCRIBE"):
            self.describing.set()
            self.interrupted.wait(30)
            raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")
        return MagicMock()

    def interrupt(self):
        self.interrupted.set()

    def close(self):
        pass


def test_cancel_stops_a_validation_running_on_a_task(dialog):
    conn = BlocksUntilInterrupted()
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText(URL)
    outcomes = []
    dialog.validation_complete.connect(lambda *a: outcomes.append(a))
    with patch("duckdb.connect", return_value=conn), \
            patch("gpq_downloader.gui.dialog.QMessageBox") as boxes:
        dialog.validate_and_accept()
        worker = dialog.validation_worker
        assert dialog.validation_task is not None
        assert conn.describing.wait(30)  # running, on the task's thread

        dialog.progress_dialog.canceled.emit()  # the Cancel button
        assert worker.killed
        assert conn.interrupted.is_set()  # kill() interrupted the query
        assert dialog.validation_worker is None and dialog.validation_task is None
        assert dialog.progress_dialog is None
        # The task ends once DuckDB returns, and lets go of the worker
        assert wait_for(lambda: not any(t.worker is worker for t in manager_module._running_tasks))
        for _ in range(20):
            QgsApplication.processEvents()
    assert not boxes.method_calls, boxes.method_calls
    assert outcomes == []
    assert dialog.accepted_count == 0


def test_closing_the_dialog_cancels_its_validation(dialog):
    conn = BlocksUntilInterrupted()
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText(URL)
    with patch("duckdb.connect", return_value=conn), \
            patch("gpq_downloader.gui.dialog.QMessageBox") as boxes:
        dialog.validate_and_accept()
        worker = dialog.validation_worker
        assert conn.describing.wait(30)
        dialog.close()
        dialog.closed_by_test = True
        assert worker.killed
        assert dialog.validation_task is None
        assert wait_for(lambda: not any(t.worker is worker for t in manager_module._running_tasks))
    assert not boxes.method_calls, boxes.method_calls
    assert dialog.accepted_count == 0


def test_a_validation_cancelled_from_the_task_manager_closes_its_progress_dialog(dialog):
    conn = BlocksUntilInterrupted()
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText(URL)
    with patch("duckdb.connect", return_value=conn), \
            patch("gpq_downloader.gui.dialog.QMessageBox") as boxes:
        dialog.validate_and_accept()
        assert conn.describing.wait(30)
        dialog.validation_task.cancel()  # what the task manager's cancel calls
        assert wait_for(lambda: dialog.validation_task is None)
        assert dialog.progress_dialog is None
    assert not boxes.method_calls, boxes.method_calls
    assert dialog.accepted_count == 0



class LateValidation(QObject):
    """A ValidationWorker that reports success once released, even if killed:
    a kill() that lands after its last check of `killed`."""

    finished = pyqtSignal(bool, str, dict)
    progress = pyqtSignal(str)
    needs_bbox_warning = pyqtSignal()
    made = []

    def __init__(self, url, iface, extent, *, where_fragment=None):
        super().__init__()
        self.url = url
        self.killed = False
        self.started = threading.Event()
        self.released = threading.Event()
        LateValidation.made.append(self)

    def run(self):
        self.started.set()
        self.released.wait(30)
        self.finished.emit(True, f"validated {self.url}", {"has_bbox": True})

    def kill(self):
        self.killed = True  # too late to stop the emit


def test_a_cancelled_validation_reporting_late_does_not_answer_for_the_next(dialog):
    """Cleanup no longer waits for the worker, so OK can start a second
    validation while the cancelled one is still returning."""
    LateValidation.made = []
    outcomes = []
    dialog.validation_complete.connect(lambda ok, msg, results: outcomes.append(msg))
    dialog.custom_radio.setChecked(True)
    with patch("gpq_downloader.gui.dialog.ValidationWorker", LateValidation), \
            patch("gpq_downloader.gui.dialog.QMessageBox") as boxes:
        dialog.url_input.setText("https://example.com/first.parquet")
        dialog.validate_and_accept()
        first = LateValidation.made[0]
        assert first.started.wait(30)
        dialog.cancel_validation()

        dialog.url_input.setText("https://example.com/second.parquet")
        dialog.validate_and_accept()
        second = LateValidation.made[1]
        assert second.started.wait(30)

        first.released.set()
        assert wait_for(lambda: not any(t.worker is first for t in manager_module._running_tasks))
        for _ in range(20):
            QgsApplication.processEvents()
        assert outcomes == []  # the first one's success is not taken for the second
        assert dialog.accepted_count == 0
        assert dialog.validation_worker is second

        second.released.set()
        assert wait_for(lambda: dialog.accepted_count == 1)
    assert outcomes == ["validated https://example.com/second.parquet"]
    assert not boxes.method_calls, boxes.method_calls


def test_a_cancelled_validation_lets_go_of_its_worker(dialog):
    """Once its task has finished, nothing holds the worker: not the task, the
    dialog, or the module that keeps running tasks alive."""
    conn = BlocksUntilInterrupted()
    dialog.custom_radio.setChecked(True)
    dialog.url_input.setText(URL)
    with patch("duckdb.connect", return_value=conn), \
            patch("gpq_downloader.gui.dialog.QMessageBox"):
        dialog.validate_and_accept()
        assert conn.describing.wait(30)
        worker = weakref.ref(dialog.validation_worker)
        dialog.cancel_validation()
        assert wait_for(lambda: not any(t.worker is worker() for t in manager_module._running_tasks))
    for _ in range(20):
        QgsApplication.processEvents()
    gc.collect()
    assert worker() is None

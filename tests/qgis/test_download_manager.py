"""The download manager (#197) on real QgsTasks, with a stub in place of the Worker.

A second download asked for while one runs is queued behind it (#47), Cancel
stops the running one and drops the queue, and the stub's signals, emitted on
the task's thread, reach the plugin on the main thread.
"""

import gc
import threading
import time
import weakref
from unittest.mock import MagicMock, patch

import pytest
from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QObject, pyqtSignal

from gpq_downloader.core.plan import DownloadJob
from gpq_downloader.plugin import QgisPluginGeoParquet
from gpq_downloader.qgis_io import manager as manager_module
from gpq_downloader.qgis_io.manager import DownloadManager, WorkerTask, submit

MAIN_THREAD = threading.get_ident()
TIMEOUT = 15


def wait_for(condition, timeout=TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QgsApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class StubWorker(QObject):
    """The Worker's signals, kill() and killed; run() waits for release().

    script is what run() does once released: "finish" (load_layer, then
    finished), "error", or "size_warning".
    """

    finished = pyqtSignal()
    error = pyqtSignal(str)
    load_layer = pyqtSignal(str)
    info = pyqtSignal(str)
    progress = pyqtSignal(str)
    percent = pyqtSignal(int)
    file_size_warning = pyqtSignal(float)

    def __init__(self, url, extent, output_file, iface, validation_results, layer_name=None,
                 aoi_geometry=None, clip_to_area=False, *, where_fragment=None, script="finish",
                 hold=True):
        super().__init__()
        self.url = url
        self.output_file = output_file
        self.killed = False
        self.size_warning_accepted = False
        self.script = script
        self.started = threading.Event()
        self._release = threading.Event()
        if not hold:
            self._release.set()
        self.run_thread = None

    def release(self):
        self._release.set()

    def run(self):
        self.run_thread = threading.get_ident()
        self.started.set()
        self.progress.emit(f"Downloading {self.url}")
        self._release.wait(TIMEOUT)
        if self.killed:
            return
        if self.script == "error":
            self.error.emit(f"{self.url} failed")
        elif self.script == "size_warning" and not self.size_warning_accepted:
            self.file_size_warning.emit(5000.0)
        else:
            self.load_layer.emit(self.output_file)
            self.finished.emit()

    def kill(self):
        self.killed = True
        self._release.set()


class Workers:
    """Patches gpq_downloader.plugin.Worker; keeps every stub made, in order."""

    def __init__(self, scripts=None, hold=True):
        self.made = []
        self.scripts = scripts or {}
        self.hold = hold

    def __call__(self, url, *args, **kwargs):
        worker = StubWorker(url, *args, script=self.scripts.get(url, "finish"), hold=self.hold, **kwargs)
        self.made.append(worker)
        return worker

    def urls(self):
        return [w.url for w in self.made]


@pytest.fixture
def iface():
    iface = MagicMock()
    iface.mainWindow.return_value = None
    return iface


@pytest.fixture
def plugin(qgs_app, iface):
    plugin = QgisPluginGeoParquet(iface)
    yield plugin
    plugin.cleanup_thread()
    assert wait_for(lambda: not plugin.downloads.is_busy)
    if hasattr(plugin, "progress_dialog"):
        plugin.progress_dialog.deleteLater()


def dialog_for(*urls):
    """A mocked data source dialog asking for custom URLs."""
    dialog = MagicMock()
    dialog.get_urls.return_value = list(urls)
    dialog.get_current_extent.return_value = None
    dialog.aoi_geometry = None
    for radio in ("overture_radio", "sourcecoop_radio", "osm_radio"):
        getattr(dialog, radio).isChecked.return_value = False
    return dialog


def accept(plugin, *urls):
    """OK in the dialog, saving each URL to a file named after it."""
    with patch("gpq_downloader.plugin.QFileDialog.getSaveFileName",
               side_effect=lambda parent, title, path, filters: (path, "GeoParquet (*.parquet)")):
        plugin.handle_dialog_accepted(dialog_for(*urls))


@pytest.fixture
def loaded():
    """The layers the plugin was told to load, with the thread it was told on."""
    calls = []
    with patch("gpq_downloader.plugin.layers.load_layer",
               side_effect=lambda iface, path: calls.append((path, threading.get_ident()))):
        yield calls


def test_second_download_while_busy_is_queued_then_runs(plugin, iface, loaded):
    workers = Workers()
    with patch("gpq_downloader.plugin.Worker", workers):
        accept(plugin, "https://example.com/a.parquet")
        assert workers.urls() == ["https://example.com/a.parquet"]
        first = workers.made[0]
        assert first.started.wait(TIMEOUT)
        assert first.run_thread != MAIN_THREAD

        # The progress text the worker emits on the task's thread reaches the dialog
        assert wait_for(lambda: plugin.progress_dialog.labelText() == "Downloading https://example.com/a.parquet")
        assert plugin.progress_dialog.isVisible()
        dialog = plugin.progress_dialog

        accept(plugin, "https://example.com/b.parquet")
        # One running, one waiting: the second Worker is not even made yet
        assert workers.urls() == ["https://example.com/a.parquet"]
        assert plugin.downloads.active_job.url == "https://example.com/a.parquet"
        assert [j.url for j in plugin.downloads.queue.pending] == ["https://example.com/b.parquet"]
        iface.messageBar().pushMessage.assert_called_once()
        assert "Added to download queue (1 waiting)" in iface.messageBar().pushMessage.call_args[0][1]
        assert plugin.progress_dialog.windowTitle() == "Downloading Data (1 waiting)"

        first.release()
        assert wait_for(lambda: len(workers.made) == 2)
        second = workers.made[1]
        assert second.url == "https://example.com/b.parquet"
        assert second.started.wait(TIMEOUT)
        # The same progress dialog shows the next job
        assert plugin.progress_dialog is dialog
        assert wait_for(lambda: dialog.labelText() == "Downloading https://example.com/b.parquet")
        assert dialog.windowTitle() == "Downloading Data"

        second.release()
        assert wait_for(lambda: not plugin.downloads.is_busy)

    assert [path.rsplit("/", 1)[-1][:2] for path, _ in loaded] == ["a_", "b_"]
    assert all(thread == MAIN_THREAD for _, thread in loaded)
    assert plugin.worker is None
    assert not dialog.isVisible()


def test_cancel_stops_the_running_download_and_clears_the_queue(plugin, loaded):
    workers = Workers()
    with patch("gpq_downloader.plugin.Worker", workers):
        accept(plugin, "https://example.com/a.parquet", "https://example.com/b.parquet")
        accept(plugin, "https://example.com/c.parquet")
        first = workers.made[0]
        assert first.started.wait(TIMEOUT)
        assert plugin.downloads.waiting == 2

        plugin.cancel_download()  # the progress dialog's Cancel
        assert first.killed  # QgsTask.cancel() reached Worker.kill()
        assert plugin.downloads.waiting == 0
        assert not plugin.progress_dialog.isVisible()
        assert wait_for(lambda: not plugin.downloads.is_busy)

    assert workers.urls() == ["https://example.com/a.parquet"]
    assert loaded == []


def test_closing_the_progress_dialog_cancels(plugin, loaded):
    workers = Workers()
    with patch("gpq_downloader.plugin.Worker", workers):
        accept(plugin, "https://example.com/a.parquet", "https://example.com/b.parquet")
        assert workers.made[0].started.wait(TIMEOUT)
        plugin.progress_dialog.close()  # QProgressDialog emits canceled
        assert workers.made[0].killed
        assert wait_for(lambda: not plugin.downloads.is_busy)
    assert workers.urls() == ["https://example.com/a.parquet"]


def test_an_error_is_shown_and_the_next_download_still_runs(plugin, loaded):
    workers = Workers(scripts={"https://example.com/a.parquet": "error"}, hold=False)
    with patch("gpq_downloader.plugin.Worker", workers), \
            patch("gpq_downloader.plugin.QMessageBox.critical") as critical:
        accept(plugin, "https://example.com/a.parquet", "https://example.com/b.parquet")
        assert wait_for(lambda: not plugin.downloads.is_busy and len(workers.made) == 2)
    critical.assert_called_once()
    assert critical.call_args[0][1:] == ("Error", "https://example.com/a.parquet failed")
    assert [path.rsplit("/", 1)[-1][:2] for path, _ in loaded] == ["b_"]


def test_the_size_warning_holds_the_queue_until_the_user_answers(plugin, loaded):
    workers = Workers(scripts={"https://example.com/a.parquet": "size_warning"}, hold=False)
    answered = []

    def proceed():
        # The job waiting behind it has not started while the user decides
        answered.append(workers.urls())
        return 2  # "Proceed with GeoJSON anyway"

    with patch("gpq_downloader.plugin.Worker", workers), \
            patch("gpq_downloader.plugin.QDialog") as box:
        box.return_value.exec.side_effect = proceed
        accept(plugin, "https://example.com/a.parquet", "https://example.com/b.parquet")
        assert wait_for(lambda: not plugin.downloads.is_busy and len(workers.made) == 3)

    assert answered == [["https://example.com/a.parquet"]]
    assert workers.urls() == ["https://example.com/a.parquet"] * 2 + ["https://example.com/b.parquet"]
    assert workers.made[1].size_warning_accepted is True
    assert [path.rsplit("/", 1)[-1][:2] for path, _ in loaded] == ["a_", "b_"]


def test_cancelling_at_the_size_warning_goes_on_to_the_next(plugin, loaded):
    workers = Workers(scripts={"https://example.com/a.parquet": "size_warning"}, hold=False)
    with patch("gpq_downloader.plugin.Worker", workers), \
            patch("gpq_downloader.plugin.QDialog") as box:
        box.return_value.exec.return_value = 0  # Cancel
        accept(plugin, "https://example.com/a.parquet", "https://example.com/b.parquet")
        assert wait_for(lambda: not plugin.downloads.is_busy and len(workers.made) == 2)
    assert workers.urls() == ["https://example.com/a.parquet", "https://example.com/b.parquet"]
    assert [path.rsplit("/", 1)[-1][:2] for path, _ in loaded] == ["b_"]


# The manager on its own


def job(name):
    return DownloadJob(url=name, output_file=f"/tmp/{name}.parquet")


def test_a_job_queued_after_cancel_waits_for_the_cancelled_worker(qgs_app):
    """#47: no second worker beside one still stopping."""
    workers = Workers()
    downloads = DownloadManager(lambda j: workers(j.url, None, j.output_file, None, {}))
    idle = []
    downloads.idle.connect(lambda: idle.append(True))
    downloads.enqueue([job("a")])
    first = workers.made[0]
    assert first.started.wait(TIMEOUT)

    # kill() is only noted: the worker keeps "running" until its stub returns
    first.kill = lambda: setattr(first, "killed", True)
    downloads.cancel_all()
    assert first.killed
    downloads.enqueue([job("b")])
    for _ in range(20):
        QgsApplication.processEvents()
    assert workers.urls() == ["a"]
    assert downloads.is_busy

    first.release()
    assert wait_for(lambda: len(workers.made) == 2)
    workers.made[1].release()
    assert wait_for(lambda: not downloads.is_busy)
    assert idle == [True]


def test_cancelling_one_task_from_the_task_manager_runs_the_next(qgs_app):
    """The task manager's own cancel ends that download only."""
    workers = Workers()
    downloads = DownloadManager(lambda j: workers(j.url, None, j.output_file, None, {}))
    downloads.enqueue([job("a"), job("b")])
    assert workers.made[0].started.wait(TIMEOUT)
    downloads._task.cancel()  # what the task manager calls
    assert workers.made[0].killed
    assert wait_for(lambda: len(workers.made) == 2)
    workers.made[1].release()
    assert wait_for(lambda: not downloads.is_busy)


def test_a_task_is_held_until_it_finishes(qgs_app):
    """The Python half of a QgsTask must outlive run(), whoever made it."""
    worker = StubWorker("a", None, "/tmp/a", None, {})
    ended = []
    submit(WorkerTask("held", worker, lambda task, result: ended.append(result)))
    assert worker.started.wait(TIMEOUT)
    assert any(t.worker is worker for t in manager_module._running_tasks)
    worker.release()
    assert wait_for(lambda: ended == [True])
    assert not any(t.worker is worker for t in manager_module._running_tasks)


def test_a_worker_that_cannot_be_made_does_not_hold_the_queue(qgs_app):
    workers = Workers(hold=False)

    def make(j):
        if j.url == "a":
            raise ValueError("no worker for a")
        return workers(j.url, None, j.output_file, None, {})

    downloads = DownloadManager(make)
    errors = []
    downloads.error.connect(errors.append)
    downloads.enqueue([job("a"), job("b")])
    assert errors == ["no worker for a"]
    assert wait_for(lambda: not downloads.is_busy)
    assert workers.urls() == ["b"]


def test_a_finished_download_lets_go_of_its_worker(qgs_app):
    made = []

    def make(j):
        worker = StubWorker(j.url, None, j.output_file, None, {}, hold=False)
        made.append(weakref.ref(worker))
        return worker

    downloads = DownloadManager(make)
    downloads.enqueue([job("a")])
    assert wait_for(lambda: not downloads.is_busy)
    for _ in range(20):
        QgsApplication.processEvents()
    gc.collect()
    assert made[0]() is None

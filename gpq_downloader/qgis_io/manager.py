"""The download manager (#197): one download at a time, each on a QgsTask.

DownloadManager runs the jobs of a DownloadQueue (core/plan.py) one after
another. A download asked for while one runs waits its turn (#47), where two
workers used to race on the plugin's shared state.

WorkerTask runs a worker's run() on a QGIS task thread, where it used to run
on a QThread the plugin and the dialog each wired up by hand. The workers
(worker.py) are unchanged: they stay on the main thread, run() is called
from the task's thread the way QThread.started called it, and the task's
cancel() calls their kill().

Signals: a worker emits from the task's thread, so every connection to code
that touches widgets is made queued (QUEUED), and Qt delivers it on the main
thread. The manager passes progress, info and load_layer on as they come.
error and file_size_warning end a run, so they are recorded where they are
emitted (DIRECT) and acted on once the task has finished, on the main thread.
"""

from qgis.core import QgsApplication, QgsTask
from qgis.PyQt.QtCore import QObject, Qt, QTimer, pyqtSignal

from .. import logger
from ..core.plan import DownloadQueue

QUEUED = Qt.ConnectionType.QueuedConnection
DIRECT = Qt.ConnectionType.DirectConnection

# Every task between submit() and finished(). The task manager owns the C++
# object, but the Python half of a QgsTask subclass, where run() lives, is
# only safe while Python holds a reference to it too; losing that mid-run is
# the classic QgsTask crash. This holds one for each task, so whoever made
# it may let go of it.
_running_tasks = set()


class WorkerTask(QgsTask):
    """A QgsTask that calls worker.run(); cancel() calls worker.kill().

    on_finished(task, result) is called on the main thread once run() has
    returned, or once the task was cancelled before it ran. The task is
    deleted by the task manager after that, so keep nothing of it but what
    on_finished copies out.
    """

    def __init__(self, description, worker, on_finished=None):
        super().__init__(description)  # the default flags include CanCancel
        self.worker = worker
        self.exception = None  # an error run() let escape, which the workers do not
        self._on_finished = on_finished

    def run(self):
        try:
            self.worker.run()
        except Exception as e:
            self.exception = e
            logger.log(f"{self.description()} failed: {e}", 2)
            return False
        return True

    def cancel(self):
        # Also reached from the task manager: its cancel button, or QGIS
        # closing - possibly after finished() has already dropped the worker
        worker = self.worker
        if worker is not None:
            worker.kill()
        super().cancel()

    def finished(self, result):
        _running_tasks.discard(self)
        callback, self._on_finished = self._on_finished, None
        try:
            if callback is not None:
                callback(self, result)
        finally:
            self.worker = None


def submit(task):
    """Hand a WorkerTask to the QGIS task manager, holding it until it finishes."""
    _running_tasks.add(task)
    QgsApplication.taskManager().addTask(task)


class DownloadManager(QObject):
    """Runs queued downloads one at a time, each Worker on a WorkerTask.

    make_worker(job) builds the Worker for a DownloadJob. The manager's
    signals are all emitted on the main thread.
    """

    job_started = pyqtSignal(object)  # the DownloadJob that is now running
    progress = pyqtSignal(str)
    info = pyqtSignal(str)
    load_layer = pyqtSignal(str)
    error = pyqtSignal(str)
    # The active job's GeoJSON would be this many MB. The job stays active,
    # holding the queue, until retry() or skip().
    file_size_warning = pyqtSignal(float)
    idle = pyqtSignal()  # the last job ended: nothing runs and nothing waits

    def __init__(self, make_worker, parent=None):
        super().__init__(parent)
        self._make_worker = make_worker
        self.queue = DownloadQueue()
        self.worker = None  # the running job's Worker
        self._task = None  # its WorkerTask, until finished() has run

    @property
    def is_busy(self):
        return self.queue.is_busy

    @property
    def waiting(self):
        return self.queue.waiting

    @property
    def active_job(self):
        return self.queue.active

    def enqueue(self, jobs):
        """Queue jobs behind any already waiting; starts the first if idle.

        Returns how many jobs wait behind the running one.
        """
        for job in jobs:
            self.queue.enqueue(job)
        self._start_next()
        return self.queue.waiting

    def cancel_all(self):
        """Stop the running job and drop the waiting ones; returns those dropped.

        The queue stays busy until the running worker has stopped, so a job
        queued meanwhile starts only after that.
        """
        dropped = self.queue.cancel_all()
        if self._task is not None:
            self._task.cancel()
        elif self.queue.active is not None:
            # Held at the size warning, or its task just finished: nothing runs
            self.queue.mark_done()
            self.idle.emit()
        return dropped

    def retry(self, job):
        """Run the job held at the size warning again, as `job` (a changed copy)."""
        if self._task is not None or self.queue.active is None:
            return False
        self.queue.replace_active(job)
        self._run(job)
        return True

    def skip(self):
        """Drop the job held at the size warning and go on to the next."""
        if self._task is not None or self.queue.active is None:
            return
        self.queue.mark_done()
        self._start_next(ended=True)

    def _start_next(self, ended=False):
        """Start the next waiting job, if none is active; ended: a job just ended."""
        if self._task is not None:
            return
        job = self.queue.next_job()
        if job is not None:
            self._run(job)
        elif ended and not self.queue.is_busy:
            self.idle.emit()

    def _run(self, job):
        try:
            worker = self._make_worker(job)
        except Exception as e:
            # A job that cannot start must not hold the queue for good
            self.queue.mark_done()
            self.error.emit(str(e))
            self._start_next(ended=True)
            return
        outcome = {}
        worker.error.connect(lambda message: outcome.setdefault("error", message), DIRECT)
        worker.file_size_warning.connect(lambda size: outcome.__setitem__("size_warning", size), DIRECT)
        worker.progress.connect(self.progress, QUEUED)
        worker.info.connect(self.info, QUEUED)
        worker.load_layer.connect(self.load_layer, QUEUED)

        name = job.layer_name or job.url
        task = WorkerTask(
            f"Downloading {name}", worker,
            lambda task, result: self._task_finished(job, worker, outcome, task),
        )
        self.worker = worker
        self._task = task
        # Submitted first: the queue must not stay busy without a task even
        # if a slot fails. What the worker emits is queued, so it reaches the
        # plugin after job_started all the same.
        submit(task)
        self.job_started.emit(job)

    def _task_finished(self, job, worker, outcome, task):
        # Inside the task manager's own slot: let go of the task, and leave
        # the message boxes the outcome may open for the next event loop pass
        if task.exception is not None:
            outcome.setdefault("error", str(task.exception))
        self._task = None
        self.worker = None
        killed = worker.killed
        QTimer.singleShot(0, lambda: self._job_ended(job, outcome, killed))

    def _job_ended(self, job, outcome, killed):
        if self.queue.active is not job:
            # cancel_all() ended it while its task was finishing
            self._start_next()
            return
        if not killed and "size_warning" in outcome:
            self.file_size_warning.emit(outcome["size_warning"])
            return
        self.queue.mark_done()
        if not killed and "error" in outcome:
            self.error.emit(outcome["error"])
        self._start_next(ended=True)

import os
import threading
import time
from unittest.mock import MagicMock, patch

import duckdb

from gpq_downloader.utils import ValidationWorker, Worker


def make_worker(mock_iface, sample_bbox, output_file, validation_results):
    return Worker("https://example.com/test.parquet", sample_bbox, output_file, mock_iface, validation_results)


def test_kill_interrupts_running_query(mock_iface, sample_bbox, tmp_path, sample_validation_results):
    worker = make_worker(mock_iface, sample_bbox, str(tmp_path / "out.gpkg"), sample_validation_results)
    worker.conn = MagicMock()
    worker.kill()
    assert worker.killed
    worker.conn.interrupt.assert_called_once()


def test_kill_without_connection_or_failing_interrupt(mock_iface, sample_bbox, tmp_path, sample_validation_results):
    worker = make_worker(mock_iface, sample_bbox, str(tmp_path / "out.gpkg"), sample_validation_results)
    worker.kill()  # not started yet: nothing to interrupt
    assert worker.killed
    worker.conn = MagicMock()
    worker.conn.interrupt.side_effect = RuntimeError("connection already closed")
    worker.kill()  # must not raise into the UI thread


def test_validation_worker_kill_interrupts(mock_iface, sample_bbox):
    worker = ValidationWorker("https://example.com/test.parquet", mock_iface, sample_bbox)
    worker.conn = MagicMock()
    worker.kill()
    assert worker.killed
    worker.conn.interrupt.assert_called_once()


def test_duckdb_interrupt_stops_long_query_quickly():
    """The mechanism kill() relies on: interrupt() from another thread aborts execute()."""
    conn = duckdb.connect()
    outcome = {}

    def long_query():
        try:
            conn.execute("SELECT count(*) FROM range(1000000000000) a").fetchone()
            outcome["result"] = "finished"
        except duckdb.InterruptException:
            outcome["result"] = "interrupted"

    thread = threading.Thread(target=long_query)
    thread.start()
    time.sleep(0.5)
    started = time.time()
    conn.interrupt()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert outcome["result"] == "interrupted"
    assert time.time() - started < 10
    conn.close()


class InterruptedOn:
    """Mock connection that raises the way an interrupted DuckDB query does when `keyword` runs."""

    def __init__(self, worker, keyword, schema, write_file=None):
        self.worker = worker
        self.keyword = keyword
        self.schema = schema
        self.write_file = write_file

    def execute(self, query):
        result = MagicMock()
        result.fetchall.return_value = self.schema if "DESCRIBE" in query else []
        result.fetchone.return_value = (1,)
        if self.keyword in query:
            if self.write_file:
                with open(self.write_file, "w") as f:
                    f.write("partial")
            self.worker.kill()
            raise duckdb.InterruptException("INTERRUPT Error: Interrupted!")
        return result

    def interrupt(self):
        pass

    def commit(self):
        pass

    def close(self):
        pass


SCHEMA = [
    ("id", "INTEGER", "YES", None, None, None),
    ("bbox", "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)", "YES", None, None, None),
    ("geometry", "GEOMETRY", "YES", None, None, None),
]


@patch("duckdb.connect")
def test_cancel_during_export_removes_partial_file_and_stays_quiet(
    mock_connect, mock_iface, sample_bbox, tmp_path, sample_validation_results
):
    output = str(tmp_path / "out.gpkg")
    worker = make_worker(mock_iface, sample_bbox, output, sample_validation_results)
    mock_connect.return_value = InterruptedOn(worker, "COPY", SCHEMA, write_file=output)
    errors = []
    worker.error.connect(errors.append)

    worker.run()

    assert not os.path.exists(output)
    assert errors == []


@patch("duckdb.connect")
def test_cancel_before_export_keeps_existing_file(
    mock_connect, mock_iface, sample_bbox, tmp_path, sample_validation_results
):
    output = tmp_path / "out.gpkg"
    output.write_text("previous export")
    old = time.time() - 3600
    os.utime(output, (old, old))
    worker = make_worker(mock_iface, sample_bbox, str(output), sample_validation_results)
    mock_connect.return_value = InterruptedOn(worker, "CREATE TABLE", SCHEMA)

    worker.run()

    assert output.read_text() == "previous export"

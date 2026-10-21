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

    thread = threading.Thread(target=long_query, daemon=True)
    thread.start()
    started = time.time()
    # An interrupt sent before the query starts is dropped, so keep sending
    while thread.is_alive() and time.time() - started < 10:
        time.sleep(0.2)
        conn.interrupt()
    thread.join(timeout=1)
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


class KilledBetweenQueries(InterruptedOn):
    """Cancel lands while no query is running: kill() is called but nothing raises."""

    def __init__(self, worker, keyword, schema):
        super().__init__(worker, keyword, schema)
        self.queries = []

    def execute(self, query):
        self.queries.append(query)
        result = MagicMock()
        result.fetchall.return_value = self.schema if "DESCRIBE" in query else []
        result.fetchone.return_value = (1,)
        if self.keyword in query:
            self.worker.kill()
        return result


@patch("duckdb.connect")
def test_cancel_between_queries_stops_before_export(
    mock_connect, mock_iface, sample_bbox, tmp_path, sample_validation_results
):
    output = tmp_path / "out.gpkg"
    worker = make_worker(mock_iface, sample_bbox, str(output), sample_validation_results)
    conn = KilledBetweenQueries(worker, "CREATE TABLE", SCHEMA)
    mock_connect.return_value = conn
    events = []
    worker.error.connect(lambda m: events.append("error"))
    worker.load_layer.connect(lambda p: events.append("load"))
    worker.finished.connect(lambda: events.append("finished"))

    worker.run()

    assert not any("COPY" in q for q in conn.queries)
    assert events == []
    assert not output.exists()


@patch("duckdb.connect")
def test_cancelled_parquet_export_removes_duckdb_temp_file(
    mock_connect, mock_iface, sample_bbox, tmp_path, sample_validation_results
):
    output = tmp_path / "out.parquet"
    temp = tmp_path / "tmp_out.parquet"
    worker = make_worker(mock_iface, sample_bbox, str(output), sample_validation_results)
    mock_connect.return_value = InterruptedOn(worker, "COPY", SCHEMA, write_file=str(temp))

    worker.run()

    assert not temp.exists()


@patch("duckdb.connect")
def test_cancelled_validation_emits_nothing(mock_connect, mock_iface, sample_bbox):
    """A cancel during the metadata check must not report success (which would start the download)."""
    no_bbox_schema = [s for s in SCHEMA if s[0] != "bbox"]
    worker = ValidationWorker("https://example.com/test.parquet", mock_iface, sample_bbox)
    mock_connect.return_value = InterruptedOn(worker, "parquet_kv_metadata", no_bbox_schema)
    events = []
    worker.finished.connect(lambda *a: events.append("finished"))
    worker.needs_bbox_warning.connect(lambda: events.append("bbox_warning"))

    worker.run()

    assert events == []

"""The download queue (#197): one job active at a time, the rest waiting in order.

Each case is a list of steps run against a fresh DownloadQueue, each step an
operation and what it should return, followed by the queue's state at the
end. Jobs are named by their URL.
"""

import dataclasses

import pytest

from gpq_downloader.core.plan import DownloadJob, DownloadQueue


def job(name):
    return DownloadJob(url=name, output_file=f"/tmp/{name}.parquet")


def names(jobs):
    return [j.url for j in jobs]


def run(queue, op, arg):
    """Perform one step; returns what the queue returned, with jobs as names."""
    if op == "enqueue":
        return queue.enqueue(job(arg))
    if op == "next":
        started = queue.next_job()
        return started.url if started else None
    if op == "done":
        ended = queue.mark_done()
        return ended.url if ended else None
    if op == "cancel":
        return names(queue.cancel_all())
    raise ValueError(op)


CASES = {
    "a job on an idle queue starts": (
        [("enqueue", "a", 1), ("next", None, "a")],
        "a", [],
    ),
    "a second job while one runs waits": (
        [("enqueue", "a", 1), ("next", None, "a"), ("enqueue", "b", 1), ("next", None, None)],
        "a", ["b"],
    ),
    "the waiting job starts after the active one is done": (
        [("enqueue", "a", 1), ("next", None, "a"), ("enqueue", "b", 1),
         ("done", None, "a"), ("next", None, "b")],
        "b", [],
    ),
    "jobs start in the order they were added": (
        [("enqueue", "a", 1), ("enqueue", "b", 2), ("enqueue", "c", 3),
         ("next", None, "a"), ("done", None, "a"),
         ("next", None, "b"), ("done", None, "b"),
         ("next", None, "c"), ("done", None, "c"),
         ("next", None, None)],
        None, [],
    ),
    "the count returned is how many wait": (
        [("enqueue", "a", 1), ("next", None, "a"), ("enqueue", "b", 1), ("enqueue", "c", 2)],
        "a", ["b", "c"],
    ),
    "next on an empty queue starts nothing": (
        [("next", None, None), ("done", None, None)],
        None, [],
    ),
    "cancel drops the waiting jobs, next first": (
        [("enqueue", "a", 1), ("next", None, "a"), ("enqueue", "b", 1), ("enqueue", "c", 2),
         ("cancel", None, ["b", "c"])],
        "a", [],
    ),
    "a cancelled job stays active until its worker stops": (
        [("enqueue", "a", 1), ("next", None, "a"), ("cancel", None, []),
         ("enqueue", "b", 1), ("next", None, None)],
        "a", ["b"],
    ),
    "a job added after a cancel runs once the cancelled one is done": (
        [("enqueue", "a", 1), ("next", None, "a"), ("enqueue", "b", 1), ("cancel", None, ["b"]),
         ("enqueue", "c", 1), ("done", None, "a"), ("next", None, "c")],
        "c", [],
    ),
    "cancel on an idle queue drops nothing": (
        [("cancel", None, [])],
        None, [],
    ),
    "cancel before anything started drops it all": (
        [("enqueue", "a", 1), ("enqueue", "b", 2), ("cancel", None, ["a", "b"]), ("next", None, None)],
        None, [],
    ),
}


@pytest.mark.parametrize("steps, active, pending", CASES.values(), ids=CASES.keys())
def test_download_queue(steps, active, pending):
    queue = DownloadQueue()
    for i, (op, arg, expected) in enumerate(steps):
        assert run(queue, op, arg) == expected, f"step {i}: {op} {arg}"
    assert (queue.active.url if queue.active else None) == active
    assert names(queue.pending) == pending
    assert queue.waiting == len(pending)
    assert queue.is_busy == (active is not None)


def test_replace_active_keeps_the_job_running_without_a_new_start():
    """The GeoJSON size warning reruns the same job, changed, ahead of the rest."""
    queue = DownloadQueue()
    queue.enqueue(job("a"))
    queue.enqueue(job("b"))
    first = queue.next_job()
    accepted = dataclasses.replace(first, size_warning_accepted=True)
    queue.replace_active(accepted)
    assert queue.active is accepted
    assert queue.next_job() is None
    assert names(queue.pending) == ["b"]
    assert queue.mark_done() is accepted
    assert queue.next_job().url == "b"


def test_replace_active_needs_an_active_job():
    with pytest.raises(RuntimeError):
        DownloadQueue().replace_active(job("a"))


def test_a_job_carries_what_the_worker_needs():
    extent, aoi = object(), object()
    j = DownloadJob(
        url="u", output_file="o.gpkg", layer_name="L", extent=extent, aoi_geometry=aoi,
        clip_to_area=True, where_fragment="x > 1",
    )
    assert (j.url, j.output_file, j.layer_name, j.extent, j.aoi_geometry) == ("u", "o.gpkg", "L", extent, aoi)
    assert j.clip_to_area is True and j.where_fragment == "x > 1"
    assert j.validation_results == {} and j.size_warning_accepted is False
    # Each job gets its own validation results, which the Worker may fill in
    assert DownloadJob("u", "o").validation_results is not DownloadJob("u", "o").validation_results
    with pytest.raises(dataclasses.FrozenInstanceError):
        j.url = "other"

"""Download planning that needs no QGIS: what to call the files a download
writes, and the queue the downloads wait in.

The naming moved here from plugin.py (#195), which still imports
custom_file_stem from here, so gpq_downloader.plugin.custom_file_stem keeps
working. DownloadQueue is the state half of the download manager (#197);
qgis_io/manager.py runs its jobs.
"""

import dataclasses
import re
from collections import deque
from typing import Any, Optional


def custom_file_stem(url):
    """A file name stem for a custom URL: the file's name, or the folder a glob reads.

    https://host/data/rivers.parquet -> rivers
    s3://bucket/cadastre/ple/**/*.parquet -> ple
    s3://bucket/places/country=*/part-0.parquet -> part-0
    """
    path = url.split("?", 1)[0].split("#", 1)[0].split("://", 1)[-1].rstrip("/")
    for part in reversed(path.split("/")[1:]):  # [0] is the host or bucket
        if "*" in part:
            continue  # a glob says nothing about the data
        if "=" in part and not re.search(r"\.(geo)?parquet$|\.pq$", part, flags=re.IGNORECASE):
            continue  # a hive partition key like country=US, unless it is a file name
        stem = re.sub(r"\.(geo)?parquet$|\.pq$", "", part, flags=re.IGNORECASE)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
        if stem:
            return stem
    return "custom_download"


# Presets whose type is told apart from the rest of their theme by name:
# building_part shares theme=buildings with building, so naming by theme alone
# would give both downloads the same layer and file name.
_OVERTURE_TYPE_NAMES = {"building_part": "Building Parts"}


def _overture_theme_and_type(url):
    """(theme, type) from an Overture URL; either is None when the URL lacks it."""
    theme = url.split("theme=")[1].split("/")[0] if "theme=" in url else None
    type_str = url.split("type=")[1].split("/")[0] if theme and "type=" in url else None
    return theme, type_str


def overture_file_stem(url):
    """A file name stem for an Overture preset URL, without the date.

    .../theme=buildings/type=building/* -> overture_buildings
    .../theme=buildings/type=building_part/* -> overture_building_parts
    .../theme=base/type=water/* -> overture_base_water
    """
    theme, type_str = _overture_theme_and_type(url)
    if type_str in _OVERTURE_TYPE_NAMES:
        return "overture_" + _OVERTURE_TYPE_NAMES[type_str].lower().replace(" ", "_")
    if theme == "base" and type_str:
        return f"overture_base_{type_str}"
    return f"overture_{theme}"


def overture_layer_name(url):
    """The QGIS layer name for an Overture preset URL, None if it has no theme.

    .../theme=buildings/type=building/* -> Overture Buildings
    .../theme=buildings/type=building_part/* -> Overture Building Parts
    .../theme=base/type=water/* -> Overture Base - Water
    """
    theme, type_str = _overture_theme_and_type(url)
    if theme is None:
        return None
    if type_str in _OVERTURE_TYPE_NAMES:
        return f"Overture {_OVERTURE_TYPE_NAMES[type_str]}"
    if theme == "base" and type_str:
        # For base layers, include the subtype
        return f"Overture {theme.title()} - {type_str.title()}"
    return f"Overture {theme.title()}"


@dataclasses.dataclass(frozen=True)
class DownloadJob:
    """One file to download: what the plugin hands a Worker.

    extent and aoi_geometry are QGIS objects; nothing here looks inside them.
    A changed job (the GeoJSON size warning's other format, or its "proceed
    anyway") is made with dataclasses.replace().
    """

    url: str
    output_file: str
    layer_name: Optional[str] = None
    extent: Any = None
    aoi_geometry: Any = None
    clip_to_area: bool = False
    where_fragment: Optional[str] = None
    validation_results: dict = dataclasses.field(default_factory=dict)
    size_warning_accepted: bool = False


class DownloadQueue:
    """The downloads waiting, in order, and the one running.

    At most one job is active. It stays active until mark_done(), which the
    manager calls only once that job's worker has stopped: a cancelled job
    is still active while its query is being interrupted, so the next job
    cannot start beside it (#47).
    """

    def __init__(self):
        self._pending = deque()
        self.active = None

    @property
    def is_busy(self):
        """True from next_job() until mark_done(): no other job may start."""
        return self.active is not None

    @property
    def waiting(self):
        """How many jobs are waiting behind the active one."""
        return len(self._pending)

    @property
    def pending(self):
        """The waiting jobs, next first."""
        return tuple(self._pending)

    def enqueue(self, job):
        """Add a job at the back. Returns how many jobs are now waiting."""
        self._pending.append(job)
        return len(self._pending)

    def next_job(self):
        """Make the next waiting job active and return it.

        None while a job is active, or when nothing is waiting.
        """
        if self.active is not None or not self._pending:
            return None
        self.active = self._pending.popleft()
        return self.active

    def replace_active(self, job):
        """Swap the active job for a changed copy of it (to run it again)."""
        if self.active is None:
            raise RuntimeError("no active job to replace")
        self.active = job

    def mark_done(self):
        """End the active job, however it ended. Returns it (None if none was)."""
        job, self.active = self.active, None
        return job

    def cancel_all(self):
        """Drop every waiting job and return them, next first.

        The active job is left for the manager to stop; it stays active,
        and the queue busy, until its worker has stopped and mark_done() is
        called.
        """
        dropped = tuple(self._pending)
        self._pending.clear()
        return dropped

"""Partition pruning: which files of a partitioned dataset a bbox can touch.

Decodes the extents encoded in partition file names (Bing/Web Mercator
quadkeys, lon/lat grid cells, S2 cells), looks up Overture's per-file extents
in its STAC collection, and builds the read_parquet() argument that lists only
the files overlapping a bbox.

Moved here from utils.py (#195), which still re-exports these names. Messages
go to the standard logging module under this module's name; the plugin's
logger.py forwards them to the QGIS message log, where they appeared before.
"""

import json
import logging
import math
import re
import urllib.request

from .sql import sql_str

logger = logging.getLogger(__name__)


# building.002213.parquet (OpenBuildingMap): Bing/Web Mercator quadkey after a prefix.
# A prefix and at least 4 digits are required so names like 0.parquet or part_1.parquet
# are not mistaken for quadkeys.
_QUADKEY_FILE_RE = re.compile(r"^(.*[._])([0-3]{4,})\.parquet$")
# e100_n20_e105_n15.parquet (GlobalBuildingAtlas): lon/lat corners of a grid cell
_GRID_FILE_RE = re.compile(r"^([ew])(\d{3})_([ns])(\d{2})_([ew])(\d{3})_([ns])(\d{2})\.parquet$")


# 3517311308976357376.parquet (VIDA by_country_s2): an S2 cell id, sometimes written
# as a signed 64-bit integer
_S2_FILE_RE = re.compile(r"^(-?\d{4,20})\.parquet$")
# Coarsest-to-finest S2 levels accepted as partition cells. Requiring level <= 20
# keeps small numbered files (0.parquet, 17.parquet) from being read as cells.
_S2_MAX_LEVEL = 20
_S2_POS_TO_IJ = ((0, 1, 3, 2), (0, 2, 3, 1), (3, 2, 0, 1), (3, 1, 0, 2))
_S2_POS_TO_ORIENTATION = (1, 0, 0, 3)


def s2_cell_level(cell_id):
    """Level (0-30) of a valid S2 cell id, or None if it isn't one."""
    if cell_id < 0:
        cell_id += 1 << 64
    if cell_id <= 0 or cell_id >= 1 << 64 or cell_id >> 61 > 5:
        return None
    lowest_bit = (cell_id & -cell_id).bit_length() - 1
    if lowest_bit % 2:
        return None
    return 30 - lowest_bit // 2


def s2_cell_bounds(cell_id):
    """Return (xmin, ymin, xmax, ymax) in WGS84 of an S2 cell.

    Cell edges are geodesics, so points along each edge are sampled, not
    just the corners. Partitions are coarse (level <= 20), so this is cheap.
    """
    if cell_id < 0:
        cell_id += 1 << 64
    level = s2_cell_level(cell_id)
    face = cell_id >> 61
    if level == 0:  # a whole cube face; faces 2 and 5 contain a pole
        return (-180.0, -90.0, 180.0, 90.0)
    # Decode the cell's (i, j) position on its face from the Hilbert curve position
    orientation = face & 1
    i = j = 0
    for k in range(level):
        pos = (cell_id >> (59 - 2 * k)) & 3
        ij = _S2_POS_TO_IJ[orientation][pos]
        i = (i << 1) | (ij >> 1)
        j = (j << 1) | (ij & 1)
        orientation ^= _S2_POS_TO_ORIENTATION[pos]

    def st_to_uv(st):
        return (4 * st * st - 1) / 3 if st >= 0.5 else (1 - 4 * (1 - st) ** 2) / 3

    n = 1 << level
    steps = 8
    lons, lats = [], []
    for a in range(steps + 1):
        for b in range(steps + 1):
            if a not in (0, steps) and b not in (0, steps):
                continue  # only the cell's edges
            u, v = st_to_uv((i + a / steps) / n), st_to_uv((j + b / steps) / n)
            x, y, z = ((1, u, v), (-u, 1, v), (-u, -v, 1),
                       (-1, -v, -u), (v, -1, -u), (v, u, -1))[face]
            lats.append(math.degrees(math.atan2(z, math.hypot(x, y))))
            lons.append(math.degrees(math.atan2(y, x)))
    if max(lats) > 89.9 or min(lats) < -89.9:  # touches a pole: every longitude
        return (-180.0, min(lats), 180.0, max(lats))
    if max(lons) - min(lons) > 180:
        # S2 cell edges lie along the antimeridian rather than crossing it, but
        # points on such an edge can come out as -180 instead of 180 (or the
        # reverse); put them on the same side as the rest of the cell
        east = sum(x > 0 for x in lons) >= sum(x < 0 for x in lons)
        lons = [(180.0 if east else -180.0) if abs(x) > 179.999999 else x for x in lons]
    return (min(lons), min(lats), max(lons), max(lats))


def quadkey_bounds(quadkey):
    """Return (xmin, ymin, xmax, ymax) in WGS84 of a Bing/Web Mercator quadkey tile."""
    zoom = len(quadkey)
    x = y = 0
    for i, digit in enumerate(quadkey):
        mask = 1 << (zoom - 1 - i)
        d = int(digit)
        if d & 1:
            x |= mask
        if d & 2:
            y |= mask
    n = 2 ** zoom

    def lat(tile_y):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * tile_y / n))))

    return (x / n * 360.0 - 180.0, lat(y + 1), (x + 1) / n * 360.0 - 180.0, lat(y))


def extent_from_file_name(name):
    """Extent encoded in a partition file name, or None if the name has no known pattern."""
    m = _GRID_FILE_RE.match(name)
    if m:
        ew1, x1, ns1, y1, ew2, x2, ns2, y2 = m.groups()
        x1 = int(x1) * (-1 if ew1 == "w" else 1)
        x2 = int(x2) * (-1 if ew2 == "w" else 1)
        y1 = int(y1) * (-1 if ns1 == "s" else 1)
        y2 = int(y2) * (-1 if ns2 == "s" else 1)
        return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    m = _QUADKEY_FILE_RE.match(name)
    if m:
        return quadkey_bounds(m.group(2))
    m = _S2_FILE_RE.match(name)
    if m:
        level = s2_cell_level(int(m.group(1)))
        if level is not None and level <= _S2_MAX_LEVEL:
            return s2_cell_bounds(int(m.group(1)))
    return None


_OVERTURE_URL_RE = re.compile(
    r"^s3://overturemaps-us-west-2/release/(?P<release>[^/*]+)/"
    r"theme=(?P<theme>[^/*]+)/type=(?P<type>[^/*]+)/"
)
_OVERTURE_PART_RE = re.compile(r"^part-(\d{5})-")
_OVERTURE_STAC = "https://stac.overturemaps.org"
_overture_extent_cache = {}


def _stac_bbox(box):
    """(xmin, ymin, xmax, ymax) from a STAC bbox, which may carry elevations."""
    if not isinstance(box, (list, tuple)) or not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in box
    ):
        return None
    if len(box) >= 6:
        return (box[0], box[1], box[3], box[4])
    if len(box) >= 4:
        return (box[0], box[1], box[2], box[3])
    return None


def overture_part_extents(url, timeout=30):
    """Per-file bounding boxes for an Overture theme and type, keyed by the part
    number in the file name.

    Overture publishes a STAC collection whose extent.spatial.bbox holds the
    box for the whole collection followed by one box per file, in the same order
    as its item links, and item N is the file named part-N. One request of about
    100KB therefore says which files a window can touch, where a glob makes
    DuckDB read the footer of all 512 of them.

    None when the url is not an Overture release path, or the collection cannot
    be read; the caller then falls back to the plain glob.
    """
    m = _OVERTURE_URL_RE.match(url or "")
    if not m:
        return None
    key = (m.group("release"), m.group("theme"), m.group("type"))
    if key in _overture_extent_cache:
        return _overture_extent_cache[key]
    collection_url = f"{_OVERTURE_STAC}/{key[0]}/{key[1]}/{key[2]}/collection.json"
    # The release, theme and type come out of the user's URL, but they cannot
    # contain a slash, so they only ever land in the path: the scheme and host
    # are the constant above. Checked rather than assumed, because urlopen would
    # just as happily read a file:// path.
    if not collection_url.startswith("https://"):
        return None
    extents = None
    try:
        with urllib.request.urlopen(collection_url, timeout=timeout) as response:  # nosec B310
            # The collection is ~100KB; a response past this cap is not it,
            # and json.loads on the truncated read falls back to the glob
            collection = json.loads(response.read(2_000_000))
        boxes = collection["extent"]["spatial"]["bbox"]
        # boxes[0] covers the collection; the rest are one per file, in item order
        extents = {}
        for index, box in enumerate(boxes[1:]):
            extent = _stac_bbox(box)
            if extent is None:
                extents = None
                break
            extents[index] = extent
        if extents:
            logger.info(f"Overture STAC: {len(extents)} file extents for {'/'.join(key)}")
        else:
            extents = None
    except Exception as e:
        logger.info(f"Could not read the Overture STAC collection: {e}")
    _overture_extent_cache[key] = extents
    return extents


def parquet_source_for_bbox(conn, url, bbox, buffer_deg=0.01):
    """Build the read_parquet() argument for `url`, pruned to the bbox when possible.

    Some datasets are split into files whose names encode their extent: quadkeys in
    OpenBuildingMap (building.002213.parquet), 5-degree cells in GlobalBuildingAtlas
    (e100_n20_e105_n15.parquet) or S2 cells in VIDA (3517311308976357376.parquet). Reading a glob over them forces DuckDB to fetch the
    footer of every file, which takes minutes. When every file matched by the glob has
    such a name, only the files overlapping the bbox are passed in.
    Returns a SQL literal, or None when no file overlaps the bbox.
    """
    default = sql_str(url)
    if bbox is None or "*" not in url:
        return default
    # Overture's file names carry no extent, but its STAC collection publishes
    # one bbox per file, so those are looked up by part number instead.
    overture = overture_part_extents(url)
    if overture is None and not url.endswith(".parquet"):
        return default
    try:
        files = [row[0] for row in conn.execute(f"SELECT file FROM glob({sql_str(url)})").fetchall()]  # nosec B608
    except Exception as e:
        logger.info(f"Could not list files for partition pruning: {e}")
        return default
    if not files:
        return default
    names = [f.rsplit("/", 1)[-1] for f in files]
    quadkeys = [m for m in (_QUADKEY_FILE_RE.match(n) for n in names) if m]
    if quadkeys:
        # Only treat names as quadkeys when they look like one tiling: a single
        # prefix, a single zoom level of at least 5, and no repeated names.
        # Otherwise names like data_2021.parquet or country=*/part_0000.parquet
        # would be mistaken for tiles and their data silently skipped.
        if (len({m.group(1) for m in quadkeys}) > 1
                or len({len(m.group(2)) for m in quadkeys}) > 1
                or len(quadkeys[0].group(2)) < 5
                or len(set(names)) < len(names)):
            return default
    xmin, ymin = bbox.xMinimum() - buffer_deg, bbox.yMinimum() - buffer_deg
    xmax, ymax = bbox.xMaximum() + buffer_deg, bbox.yMaximum() + buffer_deg
    selected = []
    for f, name in zip(files, names):
        if overture is not None:
            part = _OVERTURE_PART_RE.match(name)
            extent = overture.get(int(part.group(1))) if part else None
        else:
            extent = extent_from_file_name(name)
        if extent is None:
            return default
        fxmin, fymin, fxmax, fymax = extent
        if fxmin <= fxmax:
            x_overlap = fxmin <= xmax and fxmax >= xmin
        else:
            # A STAC bbox crossing the antimeridian has xmin > xmax
            # (GeoJSON/STAC convention): it covers [xmin, 180] and
            # [-180, xmax], so the window only misses it between the two
            x_overlap = fxmax >= xmin or fxmin <= xmax
        if x_overlap and fymin <= ymax and fymax >= ymin:
            selected.append(f)
    how = "the Overture STAC" if overture is not None else "file name"
    logger.info(f"Partition pruning by {how}: reading {len(selected)} of {len(files)} files")
    if not selected:
        return None
    if len(selected) == len(files):
        return default  # nothing pruned, so let DuckDB glob rather than list every file
    return "[" + ", ".join(sql_str(f) for f in sorted(selected)) + "]"

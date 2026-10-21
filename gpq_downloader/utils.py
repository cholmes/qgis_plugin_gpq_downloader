from __future__ import annotations

import json
import math
import re
import urllib.request

from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject, QgsGeometry
from qgis.PyQt.QtCore import pyqtSignal, QObject
import os

try:
    import duckdb
except ImportError:
    # The plugin still loads without DuckDB, so it can offer to install it
    # (see duckdb_setup.py); nothing here runs until it is present.
    duckdb = None

from . import logger


class CancellableConnection:
    """DuckDB connection whose execute() raises once the worker is killed.

    conn.interrupt() only stops a query that is running; DuckDB drops an
    interrupt that arrives between two queries. Checking the worker's flag
    before every query makes a cancel in that gap stop the next query too.
    """

    def __init__(self, conn, worker):
        self._conn = conn
        self._worker = worker

    def execute(self, *args, **kwargs):
        if self._worker.killed:
            raise duckdb.InterruptException("Cancelled")
        return self._conn.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


# Region prefix of bucket names like us-west-2.opendata.source.coop
_BUCKET_REGION_RE = re.compile(r"^([a-z]{2}(?:-gov)?-[a-z]+-\d)\.")


def configure_s3_for_url(conn, url):
    """Use path-style S3 URLs for buckets whose names contain dots
    (e.g. us-west-2.opendata.source.coop). Virtual-host style turns them into
    multi-level subdomains that fail AWS's *.s3.amazonaws.com certificate.
    When the bucket name starts with a region, use that region's endpoint."""
    if not url or not url.startswith("s3://"):
        return
    bucket = url[5:].split("/", 1)[0]
    if "." in bucket:
        m = _BUCKET_REGION_RE.match(bucket)
        if m:
            region = m.group(1)
            conn.execute(f"SET s3_region='{region}';")
            conn.execute(f"SET s3_endpoint='s3.{region}.amazonaws.com';")
        conn.execute("SET s3_url_style='path';")


# The Source Cooperative data proxy, which serves the same objects over https
# and over S3. Reading over S3 is only needed to expand a glob.
_SOURCECOOP_PROXY_PREFIX = "https://data.source.coop/"

# host or host:port of an S3-compatible server in a minio:// URL
_MINIO_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d{1,5})?$")
MINIO_URL_FORMAT = "minio://host[:port]/bucket/path (or minio+http:// for a server without TLS)"


def resolve_dataset_url(conn, url):
    """Configure `conn` for the URL's storage and return the URL DuckDB should read.

    minio://host[:port]/bucket/key reads s3://bucket/key from that S3-compatible
    server (MinIO or similar) anonymously, with path-style URLs. It uses https,
    except on port 80 or with minio+http://. A glob on the Source Cooperative
    proxy is read over S3 instead of https. Other s3:// URLs get the
    configure_s3_for_url() settings; everything else is returned unchanged.
    """
    if url and "*" in url and url.startswith(_SOURCECOOP_PROXY_PREFIX):
        # Expanding a glob means listing, which plain https cannot do. The proxy
        # speaks S3, so the same path is read that way instead. It only answers
        # path-style requests: virtual-host style would look for a subdomain
        # named after the account and fail to resolve (#133).
        conn.execute("SET s3_endpoint='data.source.coop';")
        conn.execute("SET s3_use_ssl=true;")
        conn.execute("SET s3_url_style='path';")
        return "s3://" + url[len(_SOURCECOOP_PROXY_PREFIX):]
    for scheme, use_ssl in (("minio://", True), ("minio+http://", False)):
        if url.startswith(scheme):
            host, _, key = url[len(scheme):].partition("/")
            bucket = key.split("/", 1)[0]
            if not bucket or not _MINIO_HOST_RE.match(host):
                raise ValueError(f"Invalid MinIO URL: {url}\nExpected {MINIO_URL_FORMAT}")
            if host.endswith(":80"):
                use_ssl = False
            # A temporary secret scoped to the bucket takes precedence over the
            # user's own S3 secrets and global settings, and empty keys keep their
            # AWS credentials from being sent to a third-party server.
            conn.execute(
                "CREATE OR REPLACE TEMPORARY SECRET gpq_minio ("
                " TYPE s3, PROVIDER config, KEY_ID '', SECRET '', REGION 'us-east-1',"
                " ENDPOINT ?, URL_STYLE 'path', USE_SSL ?, SCOPE ?)",
                [host, use_ssl, f"s3://{bucket}/"],
            )
            return "s3://" + key
    configure_s3_for_url(conn, url)
    return url


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
    extents = None
    try:
        with urllib.request.urlopen(collection_url, timeout=timeout) as response:
            collection = json.load(response)
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
            logger.log(f"Overture STAC: {len(extents)} file extents for {'/'.join(key)}")
        else:
            extents = None
    except Exception as e:
        logger.log(f"Could not read the Overture STAC collection: {e}")
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
        files = [row[0] for row in conn.execute(f"SELECT file FROM glob({sql_str(url)})").fetchall()]
    except Exception as e:
        logger.log(f"Could not list files for partition pruning: {e}")
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
        if fxmin <= xmax and fxmax >= xmin and fymin <= ymax and fymax >= ymin:
            selected.append(f)
    how = "the Overture STAC" if overture is not None else "file name"
    logger.log(f"Partition pruning by {how}: reading {len(selected)} of {len(files)} files")
    if not selected:
        return None
    if len(selected) == len(files):
        return default  # nothing pruned, so let DuckDB glob rather than list every file
    return "[" + ", ".join(sql_str(f) for f in sorted(selected)) + "]"


def use_lon_lat_axis_order(conn):
    """Tell DuckDB the geometries are in [longitude, latitude] order.

    Since DuckDB 2.0 a `COPY ... TO (FORMAT GDAL, SRS ...)` is an error until
    geometry_always_xy says which order the coordinates are in, so without this
    every .gpkg, .fgb and .geojson export fails outright. GDAL would otherwise
    read EPSG:4326 in the authority's [latitude, longitude] order and write
    them transposed, and the data is in lon/lat by the time it is exported.

    DuckDB 2.1 makes this the default and DuckDB 1.x has no such setting, so a
    version that does not recognise it is not a problem.
    """
    try:
        conn.execute("SET geometry_always_xy = true;")
    except Exception as e:
        logger.log(f"This DuckDB does not take geometry_always_xy: {e}")


def sql_str(value) -> str:
    """Quote a value as a SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def sql_ident(name) -> str:
    """Quote a name as a SQL identifier.

    Column names come from the remote file's own schema and GeoParquet
    metadata, so a name containing a double quote must not be able to
    terminate the identifier and smuggle SQL into the query.
    """
    return '"' + str(name).replace('"', '""') + '"'


def parse_geo_metadata(raw) -> tuple[str | None, str | None]:
    """Return (primary_column, bbox_column) from a GeoParquet 'geo' metadata value.

    bbox_column is the struct column named by the primary column's bbox covering
    (GeoParquet 1.1), such as "bbox" or "geom_bbox", or None when there is none.
    """
    try:
        geo = json.loads(raw)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(geo, dict):
        return None, None
    columns = geo.get("columns") if isinstance(geo.get("columns"), dict) else {}
    primary = geo.get("primary_column")
    if primary not in columns:
        primary = next(iter(columns), primary)
    column = columns.get(primary) if isinstance(columns.get(primary), dict) else {}
    covering = column.get("covering") if isinstance(column.get("covering"), dict) else {}
    bbox = covering.get("bbox") if isinstance(covering.get("bbox"), dict) else {}
    xmin = bbox.get("xmin")
    bbox_column = xmin[0] if isinstance(xmin, list) and len(xmin) == 2 else None
    return primary, bbox_column


def parse_geo_crs(raw) -> str | None:
    """The primary column's CRS from 'geo' metadata, as "EPSG:2913" when the
    PROJJSON names an authority, else the PROJJSON itself.

    None means the GeoParquet default, OGC:CRS84, which is EPSG:4326 in
    longitude/latitude order. A null crs also means no reprojection: the spec
    uses it for data that is not in a known CRS.
    """
    try:
        geo = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(geo, dict):
        return None
    columns = geo.get("columns") if isinstance(geo.get("columns"), dict) else {}
    primary = geo.get("primary_column")
    if primary not in columns:
        primary = next(iter(columns), primary)
    column = columns.get(primary) if isinstance(columns.get(primary), dict) else {}
    crs = column.get("crs")
    if crs is None:  # absent (CRS84 by default) or explicitly null
        return None
    if isinstance(crs, str):
        return crs
    if isinstance(crs, dict):
        ident = crs.get("id")
        if isinstance(ident, dict):
            authority, code = ident.get("authority"), ident.get("code")
            if authority and code is not None:
                return f"{authority}:{code}"
        return json.dumps(crs)  # PROJJSON without an id; PROJ can still read it
    return None


def crs_from_geo(crs_text: str | None):
    """A QgsCoordinateReferenceSystem for a parse_geo_crs() value, or None when
    the data is already in longitude/latitude. False when the CRS cannot be read."""
    if not crs_text:
        return None
    crs = QgsCoordinateReferenceSystem(crs_text)
    if not crs.isValid():
        crs = QgsCoordinateReferenceSystem()
        crs.createFromUserInput(crs_text)
    if not crs.isValid():
        return False
    if crs == QgsCoordinateReferenceSystem("EPSG:4326"):
        return None  # already lon/lat; nothing to transform
    return crs


def transform_rect(rect, source_crs, dest_crs):
    """rect from source_crs to dest_crs, or None if the transform fails."""
    try:
        transform = QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance())
        return transform.transformBoundingBox(rect)
    except Exception as e:
        logger.log(f"Could not transform the query window: {e}")
        return None


def first_parquet_file(conn, url: str) -> str:
    """One concrete file at url, so metadata reads don't fetch every footer of a glob."""
    if "*" not in url:
        return url
    try:
        row = conn.execute(f"SELECT file FROM glob({sql_str(url)}) LIMIT 1").fetchone()
    except Exception as e:
        if duckdb is not None and isinstance(e, duckdb.InterruptException):
            raise
        logger.log(f"Could not list files at {url}: {e}")
        return url
    return row[0] if row else url


def read_geo_metadata(conn, url: str) -> tuple[str | None, str | None, str | None]:
    """(primary_column, bbox_column, crs) from the 'geo' metadata of the first file at url."""
    url = first_parquet_file(conn, url)
    try:
        row = conn.execute(
            f"SELECT value FROM parquet_kv_metadata({sql_str(url)}) "
            "WHERE key = 'geo'::BLOB LIMIT 1"
        ).fetchone()
    except Exception as e:
        if duckdb is not None and isinstance(e, duckdb.InterruptException):
            raise
        logger.log(f"Could not read GeoParquet metadata: {e}")
        return None, None, None
    if not row:
        return None, None, None
    value = row[0]
    raw = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
    primary, bbox_column = parse_geo_metadata(raw)
    return primary, bbox_column, parse_geo_crs(raw)


def is_bbox_struct(col_type: str) -> bool:
    """True for a STRUCT with xmin, ymin, xmax and ymax fields."""
    t = col_type.lower()
    return t.startswith("struct") and all(f in t for f in ("xmin", "ymin", "xmax", "ymax"))


def detect_columns(schema, geo_primary=None, geo_bbox=None, hint_geometry=None, hint_bbox=None) -> tuple[str, str | None]:
    """Pick the geometry and bbox columns for a schema from DESCRIBE.

    The GeoParquet metadata wins when it names a column that exists. Then the
    caller's hint, then a GEOMETRY-typed column or a usual geometry name, and a
    struct column called bbox. Returns (geometry_column, bbox_column or None).
    """
    types = {row[0]: row[1] for row in schema}
    geometry = next((c for c in (geo_primary, hint_geometry) if c in types), None)
    if geometry is None:
        geometry = next(
            (name for name, t in types.items() if "GEOMETRY" in t.upper() or "GEOGRAPHY" in t.upper()),
            None,
        )
    if geometry is None:
        geometry = next(
            (name for name in types if name.lower() in ("geometry", "geom", "the_geom", "wkb_geometry")),
            "geometry",
        )
    bbox = next((c for c in (geo_bbox, hint_bbox) if c in types and is_bbox_struct(types[c])), None)
    if bbox is None:
        bbox = next((n for n, t in types.items() if n.lower() == "bbox" and is_bbox_struct(t)), None)
    return geometry, bbox


def bbox_polygon_wkt(bbox) -> str:
    xmin, ymin, xmax, ymax = bbox.xMinimum(), bbox.yMinimum(), bbox.xMaximum(), bbox.yMaximum()
    return f"POLYGON(({xmin} {ymin}, {xmax} {ymin}, {xmax} {ymax}, {xmin} {ymax}, {xmin} {ymin}))"


def bbox_overlap_condition(bbox_column: str, bbox) -> str:
    """Rows whose covering bbox overlaps the window, including features larger than it."""
    col = sql_ident(bbox_column)
    return (
        f"{col}.xmin <= {bbox.xMaximum()} AND {col}.xmax >= {bbox.xMinimum()} "
        f"AND {col}.ymin <= {bbox.yMaximum()} AND {col}.ymax >= {bbox.yMinimum()}"
    )


def transform_bbox_to_4326(extent, source_crs):
    """
    Transform a bounding box to EPSG:4326 (WGS84)

    Args:
        extent (QgsRectangle): The input extent to transform
        source_crs (QgsCoordinateReferenceSystem): The source CRS of the extent

    Returns:
        QgsRectangle: The transformed extent in EPSG:4326, or None if inputs are invalid
    """
    if extent is None or source_crs is None:
        return None
        
    dest_crs = QgsCoordinateReferenceSystem("EPSG:4326")

    if source_crs != dest_crs:
        transform = QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance())
        extent = transform.transformBoundingBox(extent)
    
    return extent


class Worker(QObject):
    finished = pyqtSignal()
    error = pyqtSignal(str)
    load_layer = pyqtSignal(str)
    info = pyqtSignal(str)
    progress = pyqtSignal(str)
    percent = pyqtSignal(int)
    file_size_warning = pyqtSignal(float)  # Signal for file size warnings (in MB)

    def __init__(self, dataset_url, extent, output_file, iface, validation_results, layer_name=None, aoi_geometry=None, clip_to_area=False):
        super().__init__()
        self.dataset_url = dataset_url
        self.read_url = dataset_url  # what DuckDB reads; set by resolve_dataset_url()
        self.extent = extent
        self.output_file = output_file
        self.iface = iface
        self.validation_results = validation_results
        self.killed = False
        self.layer_name = layer_name
        self.size_warning_accepted = False
        self.aoi_geometry = aoi_geometry
        # Cut features that extend past the area of interest (or bbox) at its edge
        self.clip_to_area = clip_to_area
        self.conn = None  # kept so kill() can interrupt a running query

    def run(self):
        try:
            layer_info = f" for {self.layer_name}" if self.layer_name else ""
            self.progress.emit(f"Connecting to database{layer_info}...")
            source_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
            bbox = transform_bbox_to_4326(self.extent, source_crs)

            # Log the dataset URL and aoi_geometry for debugging
            logger.log(f"Processing dataset: {self.dataset_url}")
            if self.aoi_geometry:
                logger.log(f"Using AOI geometry: {self.aoi_geometry.asWkt()}")
            else:
                logger.log("No AOI geometry provided.")

            #logger.log(f"Full validation_results at start of run: {self.validation_results}")

            conn = None
            copy_started = False  # only an export this run started is removed on cancel
            try:
                # Install and load the spatial extension
                self.progress.emit(f"Loading spatial extension{layer_info}...")

                if self.output_file.lower().endswith('.duckdb'):
                    conn = duckdb.connect(self.output_file)  # Connect directly to output file
                else:
                    conn = duckdb.connect()
                conn = CancellableConnection(conn, self)
                self.conn = conn

                conn.execute("INSTALL httpfs;")
                conn.execute("INSTALL spatial;")
                conn.execute("LOAD httpfs;")
                conn.execute("LOAD spatial;")
                use_lon_lat_axis_order(conn)
                self.read_url = resolve_dataset_url(conn, self.dataset_url)

                # Verify spatial extension is loaded by testing a spatial function
                try:
                    conn.execute("SELECT ST_AsText(ST_GeomFromText('POINT(0 0)'))").fetchone()
                except Exception as e:
                    logger.log(f"Failed to verify spatial extension: {e}")
                    # Force reload
                    conn.execute("LOAD spatial;")

                # Get schema early as we need it for both column names and bbox check
                schema_query = f"DESCRIBE SELECT * FROM read_parquet({sql_str(self.read_url)})"
                schema_result = conn.execute(schema_query).fetchall()

                # The caller's validation results are only hints: the download queue
                # passes preset defaults. The file's own GeoParquet metadata and
                # schema decide which columns hold the geometry and the bbox covering.
                geo_primary, geo_bbox, geo_crs = read_geo_metadata(conn, self.read_url)
                geometry_column, bbox_column = detect_columns(
                    schema_result,
                    geo_primary,
                    geo_bbox,
                    hint_geometry=self.validation_results.get('geometry_column'),
                    hint_bbox=self.validation_results.get('bbox_column'),
                )
                self.validation_results['geometry_column'] = geometry_column
                self.validation_results['bbox_column'] = bbox_column
                self.validation_results['has_bbox'] = bbox_column is not None
                logger.log(f"Geometry column: {geometry_column}; bbox column: {bbox_column}")

                # Data that is not in longitude/latitude has to be filtered in its
                # own units: the window and any AOI are in EPSG:4326, the file's
                # bbox covering is not
                data_crs = crs_from_geo(geo_crs)
                if data_crs is False:
                    self.error.emit(
                        f"This file says it is in {geo_crs}, which QGIS does not recognise, "
                        "so the area you asked for cannot be matched against it. "
                        "Reproject the data to EPSG:4326 and try again."
                    )
                    return
                if data_crs is not None:
                    logger.log(f"Data CRS: {data_crs.authid() or geo_crs}; filtering in its units")

                table_name = "download_data"

                self.progress.emit(f"Preparing query{layer_info}...")
                select_query = "SELECT *"
                if not self.output_file.endswith(".parquet"):
                    # Construct the SELECT clause with array conversion to strings
                    columns = []
                    for row in schema_result:
                        col_name = row[0]
                        col_type = row[1]
                        
                        # Quote the column name to handle special characters
                        quoted_col_name = sql_ident(col_name)

                        if 'STRUCT' in col_type.upper() or 'MAP' in col_type.upper():
                            columns.append(f"TO_JSON({quoted_col_name}) AS {quoted_col_name}")
                        elif '[]' in col_type:  # Check for array types like VARCHAR[]
                            columns.append(f"array_to_string({quoted_col_name}, ', ') AS {quoted_col_name}")
                        elif col_type.upper() == 'UTINYINT':
                            columns.append(f"CAST({quoted_col_name} AS INTEGER) AS {quoted_col_name}")
                        elif 'BLOB' in col_type.upper() and col_name == geometry_column:
                            # For BLOB geometry columns, we'll handle conversion differently
                            # to avoid spatial function validation issues
                            columns.append(quoted_col_name)
                        else:
                            columns.append(quoted_col_name)

                    # Check if this is Overture data and has a names column
                    has_names_column = any('names' in row[0] for row in schema_result)
                    if 'overture' in self.dataset_url and has_names_column:
                        select_query = f'SELECT "names"."primary" as name,{", ".join(columns)}'
                    else:
                        select_query = f'SELECT {", ".join(columns)}'

                # Check if geometry column is a BLOB that needs conversion
                geometry_col_type = None
                for row in schema_result:
                    if row[0] == geometry_column:
                        geometry_col_type = row[1].upper()
                        break
                is_wkb_blob = bool(geometry_col_type and 'BLOB' in geometry_col_type)

                # The area to keep, in EPSG:4326: the drawn or selected AOI if
                # there is one, otherwise the bbox
                area_wkt = bbox_polygon_wkt(bbox)
                if self.aoi_geometry is not None:
                    dest_crs = QgsCoordinateReferenceSystem("EPSG:4326")
                    source_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
                    logger.log(f"Source CRS: {source_crs.authid()}, Destination CRS: {dest_crs.authid()}")
                    # Clone and transform only for the SQL query
                    transformed_geom = QgsGeometry(self.aoi_geometry)
                    transform = QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance())
                    transformed_geom.transform(transform)
                    area_wkt = transformed_geom.asWkt()
                    logger.log(f"Applying AOI geometry filter: {area_wkt}")

                # The same area in the file's own CRS, which is what its
                # coordinates and bbox covering are measured in
                filter_bbox, filter_area_wkt = bbox, area_wkt
                if data_crs is not None:
                    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
                    filter_bbox = transform_rect(bbox, wgs84, data_crs)
                    area_geom = QgsGeometry.fromWkt(area_wkt)
                    transform = QgsCoordinateTransform(wgs84, data_crs, QgsProject.instance())
                    if filter_bbox is None or area_geom.transform(transform) != 0:
                        self.error.emit(
                            f"This file is in {data_crs.authid() or geo_crs}, and the area you "
                            "asked for could not be converted into it. Reproject the data to "
                            "EPSG:4326 and try again."
                        )
                        return
                    filter_area_wkt = area_geom.asWkt()
                    logger.log(f"Query window in {data_crs.authid()}: {filter_bbox.toString()}")

                conditions = []
                if bbox_column is not None:
                    # Cheap first pass on the covering column, which can use
                    # row-group statistics to skip data
                    conditions.append(bbox_overlap_condition(bbox_column, filter_bbox))
                if not is_wkb_blob:
                    # A WKB BLOB column is filtered after conversion, below
                    if bbox_column is None or self.aoi_geometry is not None:
                        conditions.append(
                            f'ST_Intersects({sql_ident(geometry_column)}, ST_GeomFromText({sql_str(filter_area_wkt)}))'
                        )
                where_clause = ("WHERE " + "\n AND ".join(conditions)) if conditions else ""

                parquet_source = parquet_source_for_bbox(conn, self.read_url, bbox)
                if parquet_source is None:
                    self.info.emit(f"No data found{layer_info} in the requested area. Check that your map extent overlaps with the data and/or expand your map extent. Skipping to next dataset if available.")
                    self.finished.emit()
                    return

                # Base query
                base_query = f"""
                CREATE TABLE {table_name} AS (
                    {select_query} FROM read_parquet({parquet_source})
                    {where_clause}
                ) 
                """
                self.progress.emit(f"Downloading{layer_info} data...")
                logger.log("Executing SQL query:")
                logger.log(base_query)
                
                conn.execute(base_query)
                
                # If we have a BLOB geometry column, we need to convert it after table creation
                # and apply spatial filter if needed
                if is_wkb_blob:
                    # Create a new table with converted geometry
                    temp_table = f"{table_name}_converted"
                    
                    # Build column list for conversion
                    convert_columns = []
                    for col_name, col_type, _, _, _, _ in schema_result:
                        quoted_col_name = sql_ident(col_name)
                        if col_name == geometry_column:
                            convert_columns.append(f"ST_GeomFromWKB({quoted_col_name}) AS {quoted_col_name}")
                        else:
                            convert_columns.append(quoted_col_name)

                    # Mirror the typed-geometry path: the bbox covering column has
                    # already filtered remotely, so only an AOI needs a real
                    # geometry test here
                    spatial_filter = ""
                    if bbox_column is None or self.aoi_geometry is not None:
                        spatial_filter = (
                            f'WHERE ST_Intersects(ST_GeomFromWKB({sql_ident(geometry_column)}), '
                            f'ST_GeomFromText({sql_str(filter_area_wkt)}))'
                        )

                    convert_query = f"""
                    CREATE TABLE {temp_table} AS
                    SELECT {', '.join(convert_columns)}
                    FROM {table_name}
                    {spatial_filter}
                    """
                    
                    conn.execute(convert_query)
                    
                    # Drop original and rename
                    conn.execute(f"DROP TABLE {table_name}")
                    conn.execute(f"ALTER TABLE {temp_table} RENAME TO {table_name}")
                
                # Everything downstream (clipping, the GDAL exports, QGIS) treats
                # the table as EPSG:4326, so convert it now that it is local
                if data_crs is not None:
                    self.progress.emit(f"Reprojecting{layer_info} to EPSG:4326...")
                    if not self.reproject_table(
                        conn, table_name, geometry_column, bbox_column,
                        data_crs.authid() or geo_crs,
                    ):
                        return

                if self.clip_to_area:
                    self.progress.emit(f"Clipping{layer_info} features to the area...")
                    self.clip_table(conn, table_name, geometry_column, bbox_column, area_wkt)

                # Add check for empty results
                row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                if row_count == 0:
                    self.info.emit(f"No data found{layer_info} in the requested area. Check that your map extent overlaps with the data and/or expand your map extent. Skipping to next dataset if available.")
                    self.finished.emit()  # Ensure finished signal is emitted
                    return

                self.progress.emit(f"Processing{layer_info} data to requested format...")

                file_extension = self.output_file.lower().split('.')[-1]

                if file_extension == 'duckdb':
                    # Commit the transaction to ensure the data is saved
                    conn.commit()
                    if not self.killed:
                        self.info.emit(
                            "Data has been successfully saved to DuckDB database.\n\n"
                            "Note: QGIS does not currently support loading DuckDB files directly."
                        )
                else:
                    # Check size if exporting to GeoJSON
                    if self.output_file.lower().endswith('.geojson'):
                        estimated_size = self.estimate_file_size(conn, table_name, geometry_column)
                        if estimated_size > 4096 and not self.size_warning_accepted:  # 4GB warning threshold
                            self.file_size_warning.emit(estimated_size)
                            return

                    # Use the geometry column from validation results for the Hilbert sorting
                    # At this point, if we converted BLOB to geometry, it's already a GEOMETRY type
                    # So we don't need ST_GeomFromWKB anymore
                    geometry_expr = sql_ident(geometry_column)
                    extent_expr = sql_ident(geometry_column)
                    
                    copy_query = f"""
                    COPY (
                        WITH bbox AS (
                            SELECT ST_Extent(ST_Extent_Agg({extent_expr}))::BOX_2D AS b
                            FROM   {table_name}
                        )
                        SELECT   t.*
                        FROM     {table_name} AS t
                                CROSS JOIN bbox
                        ORDER BY ST_Hilbert(t.{geometry_expr}, bbox.b)
                    ) TO {sql_str(self.output_file)}
                    """

                    if file_extension == "parquet":
                        format_options = "(FORMAT 'parquet', COMPRESSION 'ZSTD', COMPRESSION_LEVEL 22);"
                    elif self.output_file.endswith(".gpkg"):
                        format_options = "(FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326');"
                    elif self.output_file.endswith(".fgb"):
                        format_options = "(FORMAT GDAL, DRIVER 'FlatGeobuf', SRS 'EPSG:4326');"
                    elif self.output_file.endswith(".geojson"):
                        format_options = "(FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326');"
                    else:
                        self.error.emit("Unsupported file format.")
                    
                    logger.log("Executing SQL query:")
                    logger.log(copy_query + format_options)
                    copy_started = True
                    conn.execute(copy_query + format_options)

                
                if self.killed:
                    return

                if not self.killed:
                    if self.output_file.lower().endswith('.duckdb'):
                        self.info.emit(
                            "Data has been successfully saved to DuckDB database.\n\n"
                            "Note: QGIS does not currently support loading DuckDB files directly."
                        )
                    else:
                        self.load_layer.emit(self.output_file)
                    self.finished.emit()

            except Exception as e:
                if not self.killed:
                    # Change error to info if it's a "no data" error
                    error_str = str(e)
                    if "No data found" in error_str:
                        self.info.emit(f"No data found{layer_info} in the requested area for {self.dataset_url}. Skipping to next dataset if available.")
                        self.finished.emit()  # Ensure finished signal is emitted
                    else:
                        self.error.emit(error_str)
            finally:
                if conn:
                    if not self.output_file.lower().endswith('.duckdb'): # Clean up temporary table
                        try:
                            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
                        except:
                            pass
                    self.conn = None
                    conn.close()
                # Don't leave a half-written export behind after a cancel
                # (.duckdb is the user's database file, so it is never removed)
                if self.killed and copy_started and not self.output_file.lower().endswith('.duckdb'):
                    # DuckDB writes parquet to tmp_<name> when overwriting an existing file
                    out_dir, out_name = os.path.split(self.output_file)
                    for path in (self.output_file, os.path.join(out_dir, "tmp_" + out_name)):
                        try:
                            if os.path.exists(path):
                                os.remove(path)
                        except OSError as e:
                            logger.log(f"Could not remove partial output {path}: {e}")

        except Exception as e:
            if not self.killed:
                self.error.emit(str(e))

    def reproject_table(self, conn, table_name, geometry_column, bbox_column, source_crs):
        """Convert the downloaded table to EPSG:4326 in place. False on failure,
        having emitted an error naming the CRS."""
        geom = sql_ident(geometry_column)
        transformed = (
            f"ST_Transform({geom}, {sql_str(source_crs)}, 'EPSG:4326', always_xy := true)"
        )
        columns = []
        for name, *_ in conn.execute(f"DESCRIBE {table_name}").fetchall():
            quoted = sql_ident(name)
            if name == geometry_column:
                columns.append(f"{transformed} AS {quoted}")
            elif name == bbox_column:
                # The covering is in the old units, so rebuild it from the new geometry
                columns.append(
                    f"struct_pack(xmin := ST_XMin({transformed}), ymin := ST_YMin({transformed}), "
                    f"xmax := ST_XMax({transformed}), ymax := ST_YMax({transformed})) AS {quoted}"
                )
            else:
                columns.append(quoted)

        temp_table = f"{table_name}_4326"
        try:
            conn.execute(
                f"CREATE TABLE {temp_table} AS SELECT {', '.join(columns)} FROM {table_name}"
            )
        except Exception as e:
            if duckdb is not None and isinstance(e, duckdb.InterruptException):
                raise
            logger.log(f"ST_Transform from {source_crs} failed: {e}")
            self.error.emit(
                f"This data is in {source_crs} and DuckDB could not convert it to "
                f"EPSG:4326 ({e}). Reproject the data to EPSG:4326 and try again."
            )
            return False
        conn.execute(f"DROP TABLE {table_name}")
        conn.execute(f"ALTER TABLE {temp_table} RENAME TO {table_name}")
        return True

    def clip_table(self, conn, table_name, geometry_column, bbox_column, area_wkt):
        """Cut each feature to the area, dropping those outside it, and refresh the bbox column."""
        geom = sql_ident(geometry_column)
        area = f"ST_GeomFromText({sql_str(area_wkt)})"
        # Drop NULL geometries, features outside the area, and features whose
        # intersection collapses to a lower dimension (a polygon that only
        # touches the area's edge would otherwise become a line or point).
        conn.execute(
            f"DELETE FROM {table_name} WHERE {geom} IS NULL"
            f" OR NOT ST_Intersects({geom}, {area})"
            f" OR (NOT ST_CoveredBy({geom}, {area})"
            f" AND ST_Dimension(ST_Intersection({geom}, {area})) < ST_Dimension({geom}))"
        )
        # Only features crossing the area's edge need cutting
        conn.execute(
            f"UPDATE {table_name} SET {geom} = ST_Intersection({geom}, {area})"
            f" WHERE NOT ST_CoveredBy({geom}, {area})"
        )
        if bbox_column is None:
            return
        columns = {row[0]: row[1].upper() for row in conn.execute(f"DESCRIBE {table_name}").fetchall()}
        col_type = columns.get(bbox_column)
        if col_type is None:
            return
        new_bbox = (
            f"struct_pack(xmin := ST_XMin({geom}), ymin := ST_YMin({geom}), "
            f"xmax := ST_XMax({geom}), ymax := ST_YMax({geom}))"
        )
        if not col_type.startswith("STRUCT"):
            new_bbox = f"to_json({new_bbox})"  # exports other than parquet store it as JSON
        conn.execute(f'UPDATE {table_name} SET {sql_ident(bbox_column)} = {new_bbox}')

    def kill(self):
        self.killed = True
        # Stop the running DuckDB query; otherwise cancel waits for it to finish
        # and the UI thread blocks in QThread.wait().
        conn = self.conn
        if conn is not None:
            try:
                conn.interrupt()
            except Exception as e:
                logger.log(f"Could not interrupt DuckDB query: {e}")

    def estimate_file_size(self, conn, table_name, geometry_column):
        """Estimate the output file size in MB using GeoJSON feature collection structure"""
        try:
            # Get total row count
            row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

            # Use a smaller sample size for large datasets
            sample_size = min(100, row_count)

            if sample_size > 0:
                properties = ", ".join(
                    f"{sql_str(col[0])}, COALESCE(CAST({sql_ident(col[0])} AS VARCHAR), 'null')"
                    for col in conn.execute(f"DESCRIBE {table_name}").fetchall()
                    if col[0] != geometry_column
                )
                # Create a proper GeoJSON FeatureCollection sample with all properties
                sample_query = f"""
                    WITH sample AS (
                        SELECT * FROM {table_name} LIMIT {sample_size}
                    )
                    SELECT AVG(LENGTH(
                        json_object(
                            'type', 'Feature',
                            'geometry', ST_AsGeoJSON({sql_ident(geometry_column)}),
                            'properties', json_object({properties})
                        )::VARCHAR
                    )) as avg_feature_size
                    FROM sample;
                """

                # Get average feature size
                avg_feature_size = conn.execute(sample_query).fetchone()[0]

                if avg_feature_size:
                    # Account for GeoJSON overhead
                    collection_overhead = (
                        50  # {"type":"FeatureCollection","features":[]}
                    )
                    comma_overhead = row_count - 1  # Commas between features

                    total_estimated_bytes = (
                        (row_count * avg_feature_size)
                        + collection_overhead
                        + comma_overhead
                    )
                    return total_estimated_bytes / (1024 * 1024)  # Convert to MB
            return 0

        except Exception as e:
            logger.log(f"Error estimating file size: {str(e)}", 2)
            return 0

    def process_schema_columns(self, schema_result):
        """Process schema columns and return formatted SELECT clause"""
        columns = []
        for row in schema_result:
            col_name = row[0]
            col_type = row[1]
            quoted_col_name = sql_ident(col_name)

            if "STRUCT" in col_type.upper() or "MAP" in col_type.upper():
                columns.append(f"TO_JSON({quoted_col_name}) AS {quoted_col_name}")
            elif "[]" in col_type:
                columns.append(
                    f"array_to_string({quoted_col_name}, ', ') AS {quoted_col_name}"
                )
            elif col_type.upper() == "UTINYINT":
                columns.append(
                    f"CAST({quoted_col_name} AS INTEGER) AS {quoted_col_name}"
                )
            else:
                columns.append(quoted_col_name)
        return columns


class ValidationWorker(QObject):
    finished = pyqtSignal(bool, str, dict)
    progress = pyqtSignal(str)
    needs_bbox_warning = pyqtSignal()

    def __init__(self, dataset_url, iface, extent):
        super().__init__()
        self.dataset_url = dataset_url
        self.read_url = dataset_url  # what DuckDB reads; set by resolve_dataset_url()
        self.iface = iface
        self.extent = extent
        self.killed = False
        self.conn = None  # kept so kill() can interrupt a running query

        base_path = os.path.dirname(os.path.abspath(__file__))
        presets_path = os.path.join(base_path, "data", "presets.json")
        with open(presets_path, "r") as f:
            self.PRESET_DATASETS = json.load(f)

    def run(self):
        # Initialize validation results with default values
        validation_results = {
            "has_bbox": False,
            "bbox_column": None,
            "geometry_column": "geometry"  # Default fallback
        }
        
        conn = None
        try:
            self.progress.emit("Connecting to data source...")
            conn = CancellableConnection(duckdb.connect(), self)
            self.conn = conn
            conn.execute("INSTALL spatial;")
            conn.execute("LOAD spatial;")
            conn.execute("INSTALL httpfs;")
            conn.execute("LOAD httpfs;")
            self.read_url = resolve_dataset_url(conn, self.dataset_url)

            if self.killed:
                return
            if not self.needs_validation():
                validation_results.update({
                    "has_bbox": True,
                    "bbox_column": "bbox",
                })
                self.finished.emit(True, "Validation successful", validation_results)
                return

            self.progress.emit("Checking data format...")
            schema_query = f"DESCRIBE SELECT * FROM read_parquet({sql_str(self.read_url)})"
            schema_result = conn.execute(schema_query).fetchall()
            if self.killed:
                return

            # The GeoParquet metadata names the primary geometry column and its
            # bbox covering, whatever they are called (geom, geom_bbox, ...)
            geo_primary, geo_bbox, geo_crs = read_geo_metadata(conn, self.read_url)
            if self.killed:  # read_geo_metadata may swallow an interrupt
                return
            geometry_column, bbox_column = detect_columns(schema_result, geo_primary, geo_bbox)
            validation_results["geometry_column"] = geometry_column
            validation_results["crs"] = geo_crs
            if bbox_column:
                validation_results["has_bbox"] = True
                validation_results["bbox_column"] = bbox_column
                self.finished.emit(True, "Validation successful", validation_results)
            else:
                # No bbox column found - emit warning signal first
                self.needs_bbox_warning.emit()
                # Then emit finished signal with no bbox results
                self.finished.emit(True, "Validation with no bbox column", validation_results)

        except Exception as e:
            logger.log(f"Error in ValidationWorker: {str(e)}")
            if not self.killed:
                # Emit warning before error if no bbox was found
                if not validation_results.get("has_bbox"):
                    self.needs_bbox_warning.emit()
                # Still emit validation results with default values in case of error
                self.finished.emit(False, f"Error validating source: {str(e)}", validation_results)
        finally:
            self.conn = None
            if conn is not None:
                conn.close()

    def kill(self):
        """Stop validation; interrupts the running DuckDB query so cancel returns at once."""
        self.killed = True
        conn = self.conn
        if conn is not None:
            try:
                conn.interrupt()
            except Exception as e:
                logger.log(f"Could not interrupt DuckDB query: {e}")

    def needs_validation(self):
        """Determine if the dataset needs any validation"""
        # Check if URL matches any preset dataset
        for source in self.PRESET_DATASETS.values():
            for dataset in source.values():
                if (
                    isinstance(dataset.get("url"), str)
                    and dataset["url"] in self.dataset_url
                ):
                    return dataset.get("needs_validation", True)
                elif (
                    isinstance(dataset.get("url_template"), str)
                    and dataset["url_template"].split("{")[0] in self.dataset_url
                ):
                    return dataset.get("needs_validation", True)

        # All other datasets need validation
        return True

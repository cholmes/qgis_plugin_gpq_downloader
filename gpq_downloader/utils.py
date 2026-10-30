from __future__ import annotations

import json
import re

try:
    import duckdb
except ImportError:
    # The plugin still loads without DuckDB, so it can offer to install it
    # (see duckdb_setup.py); nothing here runs until it is present.
    duckdb = None

from . import logger

# Re-exported from gpq_downloader.core, where this code moved (#195), so
# existing `from gpq_downloader.utils import ...` and `utils.<name>` uses keep
# working. The shim is deleted at 1.0 (#198); import from core instead.
from .core.overture import (  # noqa: F401
    _stac_bbox,
    extent_from_file_name,
    overture_part_extents,
    parquet_source_for_bbox,
    quadkey_bounds,
    s2_cell_bounds,
    s2_cell_level,
)
from .core.sql import sql_ident, sql_str

# Moved to gpq_downloader.qgis_io (#195) and re-exported here the same way;
# import from qgis_io instead.
from .qgis_io.crs import crs_from_geo, transform_bbox_to_4326, transform_rect  # noqa: F401

_MOVED_TO_QGIS_IO_WORKER = ("CancellableConnection", "Worker", "ValidationWorker")


def __getattr__(name):
    # qgis_io.worker imports its helpers from this module, so it can only be
    # imported once this module has finished loading: on first use, not above.
    if name in _MOVED_TO_QGIS_IO_WORKER:
        from .qgis_io import worker

        return getattr(worker, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


# DuckDB's Python API cannot parameterise a table name, a column name or the
# path read_parquet() reads, which is most of what varies in the queries in
# this module, so they are built as f-strings with every value that did not
# come from this file passed through sql_str() or sql_ident() first.
#
# Those f-strings are what the B608 suppressions here mark. Bandit sees the
# string formatting but not the quoting, so each suppression asserts that the
# line has been read: add one only after checking that every interpolation on
# it is a literal in this file, a number, or one of these two calls.


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


def first_parquet_file(conn, url: str) -> str:
    """One concrete file at url, so metadata reads don't fetch every footer of a glob."""
    if "*" not in url:
        return url
    try:
        row = conn.execute(f"SELECT file FROM glob({sql_str(url)}) LIMIT 1").fetchone()  # nosec B608
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
            f"SELECT value FROM parquet_kv_metadata({sql_str(url)}) "  # nosec B608
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

"""Canned sources for the Worker golden-SQL tests.

Each source is what the Worker would find at a URL: the DESCRIBE rows, the
'geo' metadata, and the files a glob lists. `respond` answers each statement
the Worker issues the way DuckDB would, and fails on one it does not expect.
"""

import io
import json

from core_support import FakeIface, RecordingConnection, Rect
from gpq_downloader.core import overture
from gpq_downloader.utils import Worker

WINDOW = Rect(10, 10, 11, 11)

BBOX_FLOAT = "STRUCT(xmin FLOAT, xmax FLOAT, ymin FLOAT, ymax FLOAT)"
BBOX_DOUBLE = "STRUCT(xmin DOUBLE, ymin DOUBLE, xmax DOUBLE, ymax DOUBLE)"


def describe(*columns):
    return [(name, col_type, "YES", None, None, None) for name, col_type in columns]


def geo_metadata(primary, bbox_column=None, version="1.1.0"):
    column = {"encoding": "WKB", "geometry_types": []}
    if bbox_column:
        column["covering"] = {"bbox": {k: [bbox_column, k] for k in ("xmin", "ymin", "xmax", "ymax")}}
    return json.dumps({"version": version, "primary_column": primary, "columns": {primary: column}}).encode()


class Source:
    def __init__(self, url, schema, geo=None, files=None, stac=None, row_count=3, table_schema=None,
                 avg_feature_size=None):
        self.url = url
        self.schema = schema
        # DESCRIBE download_data, after the download (clipping and the GeoJSON size estimate)
        self.table_schema = table_schema if table_schema is not None else schema
        self.avg_feature_size = avg_feature_size
        self.geo = geo
        self.files = files
        self.stac = stac  # Overture STAC collection body, or None
        self.row_count = row_count

    def respond(self, sql):
        if isinstance(sql, tuple):
            return []
        if sql == "SELECT ST_AsText(ST_GeomFromText('POINT(0 0)'))":
            return [("POINT (0 0)",)]
        if sql.startswith("DESCRIBE SELECT * FROM read_parquet("):
            return self.schema
        if sql.startswith("SELECT file FROM glob("):
            assert self.files is not None, sql
            return self.files[:1] if sql.endswith(" LIMIT 1") else self.files
        if "FROM parquet_kv_metadata(" in sql:
            return [(self.geo,)] if self.geo is not None else []
        if sql.startswith("SELECT COUNT(*) FROM download_data"):
            return [(self.row_count,)]
        if sql == "DESCRIBE download_data":
            return self.table_schema
        if "avg_feature_size" in sql:
            assert self.avg_feature_size is not None, sql
            return [(self.avg_feature_size,)]
        first = sql.split(None, 1)[0].upper() if sql.strip() else ""
        assert first in {"INSTALL", "LOAD", "SET", "CREATE", "DROP", "ALTER", "COPY", "DELETE", "UPDATE"}, sql
        return []


OVERTURE_URL = "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*"
OVERTURE_PREFIX = OVERTURE_URL[:-1]


def overture_part(n):
    return f"{OVERTURE_PREFIX}part-{n:05d}-23bda295-5d48-57ef-aa31-bce815c998ac-c000.zstd.parquet"


OBM_URL = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/*.parquet"
OBM_PREFIX = OBM_URL[: -len("*.parquet")]
PROXY_URL = "https://data.source.coop/ondata/cadastral-italy-geospatial-data/ple/**/*.parquet"
PROXY_S3 = "s3://ondata/cadastral-italy-geospatial-data/ple/**/*.parquet"
CUSTOM_URL = "https://example.com/data/rivers.parquet"
MINIO_URL = "minio://minio.example.org:9000/lake/roads/*.parquet"

SOURCES = {
    # Overture: a glob pruned by the STAC, with Overture's struct and list columns
    "overture": Source(
        OVERTURE_URL,
        describe(
            ("id", "VARCHAR"),
            ("geometry", "GEOMETRY"),
            ("bbox", BBOX_FLOAT),
            ("version", "INTEGER"),
            ("sources", "STRUCT(property VARCHAR, dataset VARCHAR)[]"),
            ("names", 'STRUCT("primary" VARCHAR, common MAP(VARCHAR, VARCHAR))'),
            ("height", "DOUBLE"),
        ),
        geo=geo_metadata("geometry", "bbox"),
        files=[(overture_part(n),) for n in (0, 1, 2)],
        stac={"extent": {"spatial": {"bbox": [
            [-180, -90, 180, 90],
            [-180.0, -84.29, -85.87, 13.99],
            [5.0, 5.0, 12.0, 12.0],
            [140.01, 39.66, 179.68, 77.11],
        ]}}},
    ),
    # Source Cooperative over S3: a dotted bucket, pruned by quadkey file names
    "sourcecoop": Source(
        OBM_URL,
        describe(("id", "BIGINT"), ("height", "DOUBLE"), ("geometry", "GEOMETRY"), ("bbox", BBOX_DOUBLE)),
        geo=geo_metadata("geometry", "bbox"),
        files=[(OBM_PREFIX + f"building.{k}.parquet",) for k in ("122221", "122230", "021230")],
    ),
    # A glob on the Source Cooperative https proxy, read over S3: WKB in a BLOB
    # with no GeoParquet metadata, in files whose names carry no extent
    "sourcecoop_proxy": Source(
        PROXY_URL,
        describe(("name", "VARCHAR"), ("geometry", "BLOB"), ("tags", "VARCHAR[]")),
        files=[(PROXY_S3.replace("**/*.parquet", f"region=x/part_{n}.parquet"),) for n in (0, 1)],
    ),
    # One https file, with a named covering and every column conversion
    "custom_https": Source(
        CUSTOM_URL,
        describe(
            ("id", "BIGINT"),
            ("name", "VARCHAR"),
            ("geom", "GEOMETRY"),
            ("geom_bbox", BBOX_DOUBLE),
            ("tags", "VARCHAR[]"),
            ("props", "MAP(VARCHAR, VARCHAR)"),
            ("level", "UTINYINT"),
        ),
        geo=geo_metadata("geom", "geom_bbox"),
    ),
    # A glob on a MinIO server: GeoParquet 1.0, so no covering
    "minio": Source(
        MINIO_URL,
        describe(("id", "BIGINT"), ("geometry", "GEOMETRY")),
        geo=geo_metadata("geometry", version="1.0.0"),
        files=[(f"s3://lake/roads/{n}.parquet",) for n in (0, 1)],
    ),
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def serve_stac(monkeypatch, body):
    requests = []

    def urlopen(url, timeout=None):
        requests.append(url)
        return FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr(overture.urllib.request, "urlopen", urlopen)
    return requests


SIGNALS = ("progress", "info", "error", "load_layer", "finished", "file_size_warning", "percent")


def run_worker(source, output_file, monkeypatch, *, responder=None, clip=False, validation_results=None,
               layer_name=None, before_run=None, where_fragment=None):
    """Run a Worker on `source` against a recording connection.

    Returns (worker, connection, signals): signals is every emission in order,
    as (signal name, *args).
    """
    if source.stac is not None:
        serve_stac(monkeypatch, source.stac)
    connections = []

    def connect(*args):
        conn = RecordingConnection(responder or source.respond)
        conn.connect_args = args
        connections.append(conn)
        return conn

    worker = Worker(
        source.url, WINDOW, output_file, FakeIface(),
        dict(validation_results or {}), layer_name, clip_to_area=clip,
        connection_factory=connect, where_fragment=where_fragment,
    )
    signals = []
    for name in SIGNALS:
        getattr(worker, name).connect(lambda *args, _name=name: signals.append((_name, *args)))
    if before_run:
        before_run(worker)
    worker.run()
    assert len(connections) == 1
    return worker, connections[0], signals

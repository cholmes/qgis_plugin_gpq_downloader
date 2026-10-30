"""Characterization of resolve_dataset_url() and configure_s3_for_url().

These pin what the plugin does today with each kind of URL: which URL DuckDB
is told to read and the exact statements issued to configure the connection
for it. The statements are pinned on a recording connection because the s3_*
settings and S3 secrets need the httpfs extension, which a headless run does
not have; gpq_downloader/tests/test_minio.py checks the secret on a real one.
"""

import json
import os

import pytest

from core_support import RecordingConnection
from gpq_downloader.utils import (
    MINIO_URL_FORMAT,
    ValidationWorker,
    configure_s3_for_url,
    resolve_dataset_url,
)

PRESETS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "gpq_downloader", "data", "presets.json"
)
RELEASE = "2026-09-23.1"

DOTTED_SOURCE_COOP = [
    "SET s3_region='us-west-2';",
    "SET s3_endpoint='s3.us-west-2.amazonaws.com';",
    "SET s3_url_style='path';",
]
PROXY_GLOB = [
    "SET s3_endpoint='data.source.coop';",
    "SET s3_use_ssl=true;",
    "SET s3_url_style='path';",
]


def resolve(url):
    conn = RecordingConnection()
    return resolve_dataset_url(conn, url), conn.statements


# Every preset in presets.json, as the dialog builds its URL (Overture with a
# release and each subtype), with the URL DuckDB reads and the statements issued.
# (section, key, subtype): (template or url in presets.json, url passed in, url read, statements)
PRESET_TABLE = {
    ("overture", "buildings", "building"): (
        "s3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*",
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*",
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*",
        [],
    ),
    ("overture", "places", "place"): (
        "s3://overturemaps-us-west-2/release/{release}/theme=places/type=place/*",
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=places/type=place/*",
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=places/type=place/*",
        [],
    ),
    ("overture", "transportation", "segment"): (
        "s3://overturemaps-us-west-2/release/{release}/theme=transportation/type=segment/*",
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=transportation/type=segment/*",
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=transportation/type=segment/*",
        [],
    ),
    ("overture", "addresses", "*"): (
        "s3://overturemaps-us-west-2/release/{release}/theme=addresses/type=*/*",
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=addresses/type=*/*",
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=addresses/type=*/*",
        [],
    ),
    **{
        ("overture", "base", subtype): (
            "s3://overturemaps-us-west-2/release/{release}/theme=base/type={subtype}/*",
            f"s3://overturemaps-us-west-2/release/2026-09-23.1/theme=base/type={subtype}/*",
            f"s3://overturemaps-us-west-2/release/2026-09-23.1/theme=base/type={subtype}/*",
            [],
        )
        for subtype in ("infrastructure", "land", "land_cover", "land_use", "water", "bathymetry")
    },
    **{
        ("overture", "divisions", subtype): (
            "s3://overturemaps-us-west-2/release/{release}/theme=divisions/type={subtype}/*",
            f"s3://overturemaps-us-west-2/release/2026-09-23.1/theme=divisions/type={subtype}/*",
            f"s3://overturemaps-us-west-2/release/2026-09-23.1/theme=divisions/type={subtype}/*",
            [],
        )
        for subtype in ("division", "division_area", "division_boundary")
    },
    ("source_cooperative", "vida_buildings", None): (
        "s3://us-west-2.opendata.source.coop/vida/google-microsoft-osm-open-buildings/geoparquet/by_country_s2/*/*.parquet",
        "s3://us-west-2.opendata.source.coop/vida/google-microsoft-osm-open-buildings/geoparquet/by_country_s2/*/*.parquet",
        "s3://us-west-2.opendata.source.coop/vida/google-microsoft-osm-open-buildings/geoparquet/by_country_s2/*/*.parquet",
        DOTTED_SOURCE_COOP,
    ),
    ("source_cooperative", "microsoft_ml_roads", None): (
        "s3://us-west-2.opendata.source.coop/nlebovits/microsoft-ml-road-detections/road-detections/by_country/country=*/*.parquet",
        "s3://us-west-2.opendata.source.coop/nlebovits/microsoft-ml-road-detections/road-detections/by_country/country=*/*.parquet",
        "s3://us-west-2.opendata.source.coop/nlebovits/microsoft-ml-road-detections/road-detections/by_country/country=*/*.parquet",
        DOTTED_SOURCE_COOP,
    ),
    ("source_cooperative", "globalbuildingatlas", None): (
        "s3://us-west-2.opendata.source.coop/tge-labs/globalbuildingatlas-lod1/*.parquet",
        "s3://us-west-2.opendata.source.coop/tge-labs/globalbuildingatlas-lod1/*.parquet",
        "s3://us-west-2.opendata.source.coop/tge-labs/globalbuildingatlas-lod1/*.parquet",
        DOTTED_SOURCE_COOP,
    ),
    ("source_cooperative", "openbuildingmap", None): (
        "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/*.parquet",
        "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/*.parquet",
        "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/*.parquet",
        DOTTED_SOURCE_COOP,
    ),
    ("source_cooperative", "fsq_places_fused", None): (
        "s3://us-west-2.opendata.source.coop/fused/fsq-os-places/2025-02-06/places/*.parquet",
        "s3://us-west-2.opendata.source.coop/fused/fsq-os-places/2025-02-06/places/*.parquet",
        "s3://us-west-2.opendata.source.coop/fused/fsq-os-places/2025-02-06/places/*.parquet",
        DOTTED_SOURCE_COOP,
    ),
    ("source_cooperative", "us_structures", None): (
        "s3://us-west-2.opendata.source.coop/wherobots/usa-structures/geoparquet/*.parquet",
        "s3://us-west-2.opendata.source.coop/wherobots/usa-structures/geoparquet/*.parquet",
        "s3://us-west-2.opendata.source.coop/wherobots/usa-structures/geoparquet/*.parquet",
        DOTTED_SOURCE_COOP,
    ),
    ("source_cooperative", "planet_eu_boundaries", None): (
        "https://data.source.coop/planet/eu-field-boundaries/field_boundaries.parquet",
        "https://data.source.coop/planet/eu-field-boundaries/field_boundaries.parquet",
        "https://data.source.coop/planet/eu-field-boundaries/field_boundaries.parquet",
        [],
    ),
    ("source_cooperative", "usda_crop", None): (
        "https://data.source.coop/fiboa/us-usda-cropland/us_usda_cropland.parquet",
        "https://data.source.coop/fiboa/us-usda-cropland/us_usda_cropland.parquet",
        "https://data.source.coop/fiboa/us-usda-cropland/us_usda_cropland.parquet",
        [],
    ),
    ("source_cooperative", "nhd_flowlines", None): (
        "https://data.source.coop/cholmes/nhd/NHDFlowline.parquet",
        "https://data.source.coop/cholmes/nhd/NHDFlowline.parquet",
        "https://data.source.coop/cholmes/nhd/NHDFlowline.parquet",
        [],
    ),
    **{
        ("openstreetmap", layer, None): (
            f"https://data.openstreetmap.us/layercake/{layer}.parquet",
            f"https://data.openstreetmap.us/layercake/{layer}.parquet",
            f"https://data.openstreetmap.us/layercake/{layer}.parquet",
            [],
        )
        for layer in (
            "addresses", "buildings", "boundaries", "highways", "parks", "pois",
            "settlements", "waterways", "wetlands",
        )
    },
}


def presets():
    with open(PRESETS_PATH) as f:
        return json.load(f)


def preset_entries():
    """(section, key, subtype) for every URL the dialog can build from presets.json."""
    data = presets()
    entries = set()
    for section, datasets in data.items():
        for key, dataset in datasets.items():
            if "subtypes" in dataset:
                entries.update((section, key, s) for s in dataset["subtypes"])
            elif "url_template" in dataset:
                # dialog.get_urls(): the type is derived from the theme name
                subtype = {"transportation": "segment", "addresses": "*"}.get(key, key.rstrip("s"))
                entries.add((section, key, subtype))
            else:
                entries.add((section, key, None))
    return entries


def test_the_table_covers_every_preset():
    """A preset added to presets.json has to be added here with its resolved URL."""
    assert preset_entries() == set(PRESET_TABLE)


@pytest.mark.parametrize("entry", sorted(PRESET_TABLE, key=str), ids=lambda e: "-".join(filter(None, e)))
def test_preset_url_resolution(entry):
    section, key, subtype = entry
    source, url, expected_url, expected_statements = PRESET_TABLE[entry]
    dataset = presets()[section][key]
    assert dataset.get("url", dataset.get("url_template")) == source
    if "url_template" in dataset:
        assert dataset["url_template"].format(release=RELEASE, subtype=subtype) == url
    assert resolve(url) == (expected_url, expected_statements)


def test_presets_needs_validation_flags():
    """ValidationWorker.needs_validation() matches these by substring of the URL."""
    flags = {
        (section, key): dataset.get("needs_validation", True)
        for section, datasets in presets().items()
        for key, dataset in datasets.items()
    }
    assert [k for k, v in flags.items() if v] == [("source_cooperative", "nhd_flowlines")]


@pytest.mark.parametrize("url, expected", [
    ("https://data.source.coop/cholmes/nhd/NHDFlowline.parquet", True),
    ("https://data.openstreetmap.us/layercake/pois.parquet", False),
    ("s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/*.parquet", False),
    # A substring match: anything that contains a preset URL counts as that preset
    ("https://data.openstreetmap.us/layercake/pois.parquet?v=2", False),
    # Overture templates match on everything before the first "{", so any
    # Overture release path, whatever its theme, skips validation
    ("s3://overturemaps-us-west-2/release/2020-01-01/theme=anything/type=x/*", False),
    ("https://example.com/data.parquet", True),
    ("https://data.source.coop/someone/else.parquet", True),
])
def test_needs_validation(url, expected):
    worker = ValidationWorker(url, None, None)
    assert worker.needs_validation() is expected


# --- The Source Cooperative proxy, dotted buckets, and rewriting twice -------

@pytest.mark.parametrize("url, expected_url, expected_statements", [
    # A glob on the https proxy is read over S3 from the proxy, path-style (#133, #191)
    (
        "https://data.source.coop/ondata/cadastral-italy-geospatial-data/ple/**/*.parquet",
        "s3://ondata/cadastral-italy-geospatial-data/ple/**/*.parquet",
        PROXY_GLOB,
    ),
    # The rewritten URL is not passed on to configure_s3_for_url(): a dotted
    # first segment gets no region endpoint, which would replace the proxy's
    (
        "https://data.source.coop/us-west-2.opendata.source.coop/x/*.parquet",
        "s3://us-west-2.opendata.source.coop/x/*.parquet",
        PROXY_GLOB,
    ),
    ("https://data.source.coop/my.org/data/*.parquet", "s3://my.org/data/*.parquet", PROXY_GLOB),
    # The "*" is looked for anywhere, so one in the query string also rewrites
    ("https://data.source.coop/a/b.parquet?x=*", "s3://a/b.parquet?x=*", PROXY_GLOB),
    # A single file on the proxy stays on https and configures nothing
    ("https://data.source.coop/cholmes/nhd/NHDFlowline.parquet",
     "https://data.source.coop/cholmes/nhd/NHDFlowline.parquet", []),
    # Only https://data.source.coop/ exactly
    ("http://data.source.coop/a/*.parquet", "http://data.source.coop/a/*.parquet", []),
    ("https://DATA.source.coop/a/*.parquet", "https://DATA.source.coop/a/*.parquet", []),
    ("https://data.source.coop.example.com/a/*.parquet", "https://data.source.coop.example.com/a/*.parquet", []),
    # A glob on any other https host is left alone
    ("https://example.com/data/*.parquet", "https://example.com/data/*.parquet", []),
    # The bucket-in-hostname form of Source Cooperative is a dotted S3 bucket
    (
        "s3://us-west-2.opendata.source.coop/ondata/cadastral-italy-geospatial-data/ple/**/*.parquet",
        "s3://us-west-2.opendata.source.coop/ondata/cadastral-italy-geospatial-data/ple/**/*.parquet",
        DOTTED_SOURCE_COOP,
    ),
])
def test_source_coop_resolution(url, expected_url, expected_statements):
    assert resolve(url) == (expected_url, expected_statements)


@pytest.mark.parametrize("url, second_statements", [
    # The rewritten URL is an ordinary s3:// URL. Resolving it a second time on
    # the same connection issues nothing for an undotted bucket...
    ("https://data.source.coop/ondata/ple/**/*.parquet", []),
    # ...but a dotted one gets the configure_s3_for_url() settings, and a region
    # prefix points s3_endpoint at AWS, replacing the proxy endpoint set first
    ("https://data.source.coop/my.org/data/*.parquet", ["SET s3_url_style='path';"]),
    ("https://data.source.coop/us-west-2.opendata.source.coop/x/*.parquet", DOTTED_SOURCE_COOP),
])
def test_resolving_the_rewritten_url_again(url, second_statements):
    """The double-rewriting hazard: the Worker and ValidationWorker each resolve
    the dataset URL they were given, never the URL a resolve returned. If that
    changes, the second pass reconfigures the connection as shown here."""
    conn = RecordingConnection()
    first = resolve_dataset_url(conn, url)
    assert conn.statements == PROXY_GLOB
    second = resolve_dataset_url(conn, first)
    assert second == first
    assert conn.statements == PROXY_GLOB + second_statements


# --- MinIO --------------------------------------------------------------------

MINIO_SECRET = (
    "CREATE OR REPLACE TEMPORARY SECRET gpq_minio ("
    " TYPE s3, PROVIDER config, KEY_ID '', SECRET '', REGION 'us-east-1',"
    " ENDPOINT ?, URL_STYLE 'path', USE_SSL ?, SCOPE ?)"
)


@pytest.mark.parametrize("url, expected_url, params", [
    ("minio://data.example.org/cadencemaps/release/x/*",
     "s3://cadencemaps/release/x/*", ["data.example.org", True, "s3://cadencemaps/"]),
    ("minio://minio.local:9000/bucket/path/*.parquet",
     "s3://bucket/path/*.parquet", ["minio.local:9000", True, "s3://bucket/"]),
    # Port 80 means plain http, whichever scheme
    ("minio://host:80/b/k.parquet", "s3://b/k.parquet", ["host:80", False, "s3://b/"]),
    ("minio+http://host:9000/b/k.parquet", "s3://b/k.parquet", ["host:9000", False, "s3://b/"]),
    # minio+http:// is never upgraded, even on 443
    ("minio+http://host:443/b/k.parquet", "s3://b/k.parquet", ["host:443", False, "s3://b/"]),
    # Only the host is checked; the rest of the path is taken as it is
    ("minio://host/b", "s3://b", ["host", True, "s3://b/"]),
    ("minio://10.0.0.5:9000/b/k.parquet", "s3://b/k.parquet", ["10.0.0.5:9000", True, "s3://b/"]),
    # A dotted bucket on MinIO is scoped by the secret, not by configure_s3_for_url()
    ("minio://host/my.bucket/k.parquet", "s3://my.bucket/k.parquet", ["host", True, "s3://my.bucket/"]),
    ("minio://host/us-west-2.data.bucket/k.parquet", "s3://us-west-2.data.bucket/k.parquet",
     ["host", True, "s3://us-west-2.data.bucket/"]),
])
def test_minio_resolution(url, expected_url, params):
    assert resolve(url) == (expected_url, [(MINIO_SECRET, params)])


@pytest.mark.parametrize("url", [
    "minio://host",
    "minio://host/",
    "minio:///bucket/k.parquet",
    "minio://host:abc/b/k.parquet",
    "minio://host:123456/b/k.parquet",
    "minio://-host/b/k.parquet",
    "minio://user@host/b/k.parquet",
    "minio://h';CREATE TABLE pwned AS SELECT 1;--/b/k.parquet",
    "minio+http://host",
])
def test_invalid_minio_urls(url):
    conn = RecordingConnection()
    with pytest.raises(ValueError) as e:
        resolve_dataset_url(conn, url)
    assert str(e.value) == f"Invalid MinIO URL: {url}\nExpected {MINIO_URL_FORMAT}"
    assert conn.statements == []


def test_minio_url_format():
    assert MINIO_URL_FORMAT == (
        "minio://host[:port]/bucket/path (or minio+http:// for a server without TLS)"
    )


def test_minio_scheme_is_case_sensitive():
    # MINIO:// is not recognised, so it is returned for DuckDB to reject
    assert resolve("MINIO://host/b/k.parquet") == ("MINIO://host/b/k.parquet", [])


# --- Everything else ------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://example.com/a.parquet",
    "http://example.com/a.parquet",
    "file:///tmp/a.parquet",
    "/tmp/a.parquet",
    "hf://datasets/x/y/*.parquet",
    "gs://bucket/a.parquet",
    "s3://overturemaps-us-west-2/release/x/*",
    "s3://bucket/a.parquet",
    "",
])
def test_other_urls_pass_through(url):
    assert resolve(url) == (url, [])


def test_none_url_raises():
    """Oddity: configure_s3_for_url() accepts None, but resolve_dataset_url()
    reaches url.startswith() first and raises."""
    with pytest.raises(AttributeError):
        resolve_dataset_url(RecordingConnection(), None)


@pytest.mark.parametrize("url, expected", [
    (None, []),
    ("", []),
    ("https://us-west-2.opendata.source.coop/x.parquet", []),
    ("s3://bucket/x.parquet", []),
    ("s3://bucket", []),
    ("s3://overturemaps-us-west-2/release/x/*", []),
    ("s3://my.bucket.example/data/*.parquet", ["SET s3_url_style='path';"]),
    ("s3://us-west-2.opendata.source.coop/x/*.parquet", DOTTED_SOURCE_COOP),
    ("s3://eu-central-1.data.example/x.parquet", [
        "SET s3_region='eu-central-1';",
        "SET s3_endpoint='s3.eu-central-1.amazonaws.com';",
        "SET s3_url_style='path';",
    ]),
    ("s3://ap-southeast-2.bucket.example/x.parquet", [
        "SET s3_region='ap-southeast-2';",
        "SET s3_endpoint='s3.ap-southeast-2.amazonaws.com';",
        "SET s3_url_style='path';",
    ]),
    ("s3://us-gov-west-1.bucket.example/x.parquet", [
        "SET s3_region='us-gov-west-1';",
        "SET s3_endpoint='s3.us-gov-west-1.amazonaws.com';",
        "SET s3_url_style='path';",
    ]),
    # The region has to be lower case and followed by a dot
    ("s3://US-WEST-2.bucket.example/x.parquet", ["SET s3_url_style='path';"]),
    ("s3://us-west-2-bucket.example/x.parquet", ["SET s3_url_style='path';"]),
    ("s3://us-west-2/x.parquet", []),
    # A dotted bucket with no path
    ("s3://a.b", ["SET s3_url_style='path';"]),
    # Only the scheme in lower case counts
    ("S3://a.b/x.parquet", []),
])
def test_configure_s3_for_url(url, expected):
    conn = RecordingConnection()
    assert configure_s3_for_url(conn, url) is None
    assert conn.statements == expected

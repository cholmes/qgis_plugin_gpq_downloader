"""Characterization of the partition-extent decoders and the Overture STAC lookup.

The values are what the code computes today (checked against the files where
the existing tests in tests/qgis/test_s3_partitions.py say so); a
move that changes any of them changes which files a download reads.
"""

import io
import json

import pytest

from gpq_downloader.core import overture
from gpq_downloader.utils import (
    extent_from_file_name,
    overture_part_extents,
    quadkey_bounds,
    s2_cell_bounds,
    s2_cell_level,
)

MERC_MAX = 85.0511287798066


@pytest.mark.parametrize("quadkey, expected", [
    ("0", (-180.0, 0.0, 0.0, MERC_MAX)),
    ("1", (0.0, 0.0, 180.0, MERC_MAX)),
    ("2", (-180.0, -MERC_MAX, 0.0, 0.0)),
    ("3", (0.0, -MERC_MAX, 180.0, 0.0)),
    ("00", (-180.0, 66.51326044311186, -90.0, MERC_MAX)),
    ("0123", (-67.5, 66.51326044311186, -45.0, 74.01954331150226)),
    ("021230", (-123.75, 45.089035564831015, -118.125, 48.92249926375824)),  # Seattle
    ("122221", (5.625, 5.615985819155334, 11.25, 11.178401873711781)),
    ("122100", (22.5, 36.59788913307021, 28.125, 40.97989806962013)),
    ("", (-180.0, -MERC_MAX, 180.0, MERC_MAX)),  # zoom 0: the whole world
])
def test_quadkey_bounds(quadkey, expected):
    assert quadkey_bounds(quadkey) == pytest.approx(expected, abs=1e-12)


def test_quadkey_bounds_does_not_validate_digits():
    # Digits above 3 are not rejected; only bits 0 and 1 of each are read
    assert quadkey_bounds("7") == quadkey_bounds("3")
    with pytest.raises(ValueError):
        quadkey_bounds("x")


@pytest.mark.parametrize("cell_id, level", [
    (1 << 60, 0),                       # face 0
    ((5 << 61) | (1 << 60), 0),         # face 5
    (3544332906740580352, 4),           # VIDA THA
    (-6052837899185946624, 1),          # signed int64 form (VIDA NZL)
    (12393906174523604992, 1),          # the same cell, unsigned
    (10466365534009032704, 3),          # VIDA PER
    (6354579074219769856, 4),           # Aleutians
    (1, 30),                            # lowest bit 0: a leaf cell
    (2, None),                          # lowest set bit at an odd position
    (0, None),
    (6 << 61, None),                    # face 6 does not exist
    (1 << 64, None),
    (-(1 << 64), None),
    (5 << 61, None),
])
def test_s2_cell_level(cell_id, level):
    assert s2_cell_level(cell_id) == level


@pytest.mark.parametrize("cell_id, expected", [
    (1 << 60, (-180.0, -90.0, 180.0, 90.0)),  # level 0 is taken as the whole world
    (3544332906740580352, (100.61965527615514, 15.886262361665889, 106.53483785734517, 22.2705754880082)),
    (-6052837899185946624, (-180.0, -90.0, 180.0, -35.264389682754654)),  # touches the south pole
    (10466365534009032704, (-79.38034472384487, -22.2705754880082, -67.38013505195957, -9.8193006387579)),
    (7782220156096217088, (135.0, -45.0, 180.0, 0.0)),    # west of the antimeridian
    (8358680908399640576, (-180.0, -45.0, -135.0, 0.0)),  # east of it
    (6354579074219769856, (172.66139366376376, 49.86230374719826, 180.0, 55.4914770123316)),
])
def test_s2_cell_bounds(cell_id, expected):
    assert s2_cell_bounds(cell_id) == pytest.approx(expected, abs=1e-9)


def test_s2_cell_bounds_signed_and_unsigned_agree():
    assert s2_cell_bounds(-6052837899185946624) == s2_cell_bounds(12393906174523604992)


def test_s2_cell_bounds_of_an_invalid_id_raises():
    """Oddity: there is no None for an invalid id; callers check s2_cell_level() first."""
    with pytest.raises(TypeError):
        s2_cell_bounds(5 << 61)


@pytest.mark.parametrize("name, expected", [
    # GlobalBuildingAtlas 5-degree grid cells, either corner order
    ("e100_n20_e105_n15.parquet", (100, 15, 105, 20)),
    ("w170_n65_w165_n60.parquet", (-170, 60, -165, 65)),
    ("e100_n00_e105_s05.parquet", (100, -5, 105, 0)),
    ("w005_s10_e000_s15.parquet", (-5, -15, 0, -10)),
    # OpenBuildingMap quadkeys after a prefix ending in . or _
    ("building.021230.parquet", quadkey_bounds("021230")),
    ("tile_1222.parquet", quadkey_bounds("1222")),
    ("a.b.0000.parquet", quadkey_bounds("0000")),
    # VIDA S2 cell ids
    ("3544332906740580352.parquet", s2_cell_bounds(3544332906740580352)),
    ("-6052837899185946624.parquet", s2_cell_bounds(-6052837899185946624)),
    # Not partition names
    ("0.parquet", None),
    ("17.parquet", None),
    ("1024.parquet", None),            # not a valid S2 id
    ("20240101.parquet", None),
    ("part_1.parquet", None),
    ("THA.parquet", None),
    ("building.012.parquet", None),    # quadkeys need at least 4 digits
    ("012301.parquet", None),          # and a prefix
    ("1.parquet", None),               # S2 ids need at least 4 digits...
    ("e100_n20_e105_n15.PARQUET", None),
    ("e100_n20_e105_n15.zstd.parquet", None),
    ("part-00000-170892fe-0fe0-43c1-999d-f0911ce43365-c000.zstd.parquet", None),
])
def test_extent_from_file_name(name, expected):
    got = extent_from_file_name(name)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_s2_names_deeper_than_level_20_are_ignored():
    leaf = 1 << 60 | 1  # level 30
    assert s2_cell_level(leaf) == 30
    assert extent_from_file_name(f"{leaf}.parquet") is None


def test_grid_names_are_not_range_checked():
    # e999 is outside the world, but the name decodes anyway
    assert extent_from_file_name("e999_n99_e995_n95.parquet") == (995, 95, 999, 99)


# --- overture_part_extents --------------------------------------------------

BUILDINGS = "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*"
COLLECTION_URL = "https://stac.overturemaps.org/2026-09-23.1/buildings/building/collection.json"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def stac(monkeypatch):
    """Serve a STAC collection (set .body) and record each request as (url, timeout)."""
    server = type("Stac", (), {})()
    server.requests = []
    server.body = None

    def urlopen(url, timeout=None):
        server.requests.append((url, timeout))
        if isinstance(server.body, Exception):
            raise server.body
        return FakeResponse(json.dumps(server.body).encode())

    monkeypatch.setattr(overture.urllib.request, "urlopen", urlopen)
    return server


def collection(*boxes):
    return {"type": "Collection", "extent": {"spatial": {"bbox": list(boxes)}}}


def test_overture_part_extents_reads_one_box_per_file(stac):
    stac.body = collection(
        [-180, -90, 180, 90],               # the whole collection
        [-180.0, -84.29, -85.87, 13.99],    # part 0
        [8.99, 48.69, 0, 11.07, 51.36, 10],  # part 1, with elevations
        [140.01, 39.66, 179.68, 77.11],     # part 2
    )
    assert overture_part_extents(BUILDINGS) == {
        0: (-180.0, -84.29, -85.87, 13.99),
        1: (8.99, 48.69, 11.07, 51.36),
        2: (140.01, 39.66, 179.68, 77.11),
    }
    assert stac.requests == [(COLLECTION_URL, 30)]


def test_overture_part_extents_is_cached_per_release_theme_and_type(stac):
    stac.body = collection([0, 0, 1, 1], [0, 0, 1, 1])
    first = overture_part_extents(BUILDINGS)
    # Any URL under the same release/theme/type uses the cached result
    assert overture_part_extents(BUILDINGS + "part-00000-x.parquet") is first
    assert len(stac.requests) == 1
    assert overture._overture_extent_cache == {("2026-09-23.1", "buildings", "building"): {0: (0, 0, 1, 1)}}
    overture_part_extents(BUILDINGS.replace("2026-09-23.1", "2026-10-22.0"))
    assert [u for u, _ in stac.requests] == [
        COLLECTION_URL,
        "https://stac.overturemaps.org/2026-10-22.0/buildings/building/collection.json",
    ]


def test_overture_part_extents_timeout_is_passed_through(stac):
    stac.body = collection([0, 0, 1, 1], [0, 0, 1, 1])
    overture_part_extents(BUILDINGS, timeout=5)
    assert stac.requests == [(COLLECTION_URL, 5)]


@pytest.mark.parametrize("body", [
    collection([0, 0, 1, 1]),                      # no per-file boxes
    collection([0, 0, 1, 1], [0, 0, 1, 1], [0, 0]),  # one box too short
    {"extent": {}},                                # no spatial extent
    [],                                            # not a collection
    OSError("no network"),
    ValueError("bad json"),
])
def test_overture_part_extents_unusable_collection_is_none(stac, body):
    stac.body = body
    assert overture_part_extents(BUILDINGS) is None


def test_overture_part_extents_failure_is_cached(stac):
    """Oddity: a failed STAC request is cached as None for the rest of the
    session, so a transient error means every later download of that type
    reads the whole glob, without asking again."""
    stac.body = OSError("timed out")
    assert overture_part_extents(BUILDINGS) is None
    stac.body = collection([0, 0, 1, 1], [0, 0, 1, 1])
    assert overture_part_extents(BUILDINGS) is None
    assert len(stac.requests) == 1


@pytest.mark.parametrize("url", [
    None,
    "",
    "https://example.com/a.parquet",
    "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/*.parquet",
    # Addresses are read with type=*, which cannot name one collection
    "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=addresses/type=*/*",
    "s3://overturemaps-us-west-2/release/*/theme=buildings/type=building/*",
    "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/*",
    "s3://overturemaps-us-east-1/release/2026-09-23.1/theme=buildings/type=building/*",
    "https://overturemaps-us-west-2.s3.amazonaws.com/release/2026-09-23.1/theme=buildings/type=building/*",
])
def test_overture_part_extents_other_urls_make_no_request(stac, url):
    assert overture_part_extents(url) is None
    assert stac.requests == []


def test_overture_part_extents_accepts_an_overture_file_url(stac):
    stac.body = collection([0, 0, 1, 1], [0, 0, 1, 1])
    file_url = BUILDINGS[:-1] + "part-00000-abc.zstd.parquet"
    assert overture_part_extents(file_url) == {0: (0, 0, 1, 1)}
    assert stac.requests == [(COLLECTION_URL, 30)]

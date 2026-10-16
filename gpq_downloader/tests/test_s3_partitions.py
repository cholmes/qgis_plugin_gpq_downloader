from unittest.mock import MagicMock

import pytest

from qgis.core import QgsRectangle

from gpq_downloader import utils
from gpq_downloader.utils import (
    configure_s3_for_url,
    extent_from_file_name,
    overture_part_extents,
    parquet_source_for_bbox,
    quadkey_bounds,
    s2_cell_bounds,
    s2_cell_level,
)

SOURCE_COOP_GLOB = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/*.parquet"
PREFIX = "s3://us-west-2.opendata.source.coop/tge-labs/openbuildingmap/"
GBA_GLOB = "s3://us-west-2.opendata.source.coop/tge-labs/globalbuildingatlas-lod1/*.parquet"
GBA_PREFIX = "s3://us-west-2.opendata.source.coop/tge-labs/globalbuildingatlas-lod1/"
NAN_THAILAND = (100.76, 18.77, 100.79, 18.80)


def executed_sql(conn):
    return [c.args[0] for c in conn.execute.call_args_list]


def test_configure_s3_dotted_bucket_uses_path_style():
    conn = MagicMock()
    configure_s3_for_url(conn, SOURCE_COOP_GLOB)
    sql = executed_sql(conn)
    assert "SET s3_url_style='path';" in sql
    assert "SET s3_endpoint='s3.us-west-2.amazonaws.com';" in sql


def test_configure_s3_dotted_bucket_without_region_only_sets_path_style():
    conn = MagicMock()
    configure_s3_for_url(conn, "s3://my.bucket.example/data/*.parquet")
    assert executed_sql(conn) == ["SET s3_url_style='path';"]


def test_configure_s3_uses_region_from_bucket_name():
    conn = MagicMock()
    configure_s3_for_url(conn, "s3://eu-central-1.data.example/x.parquet")
    assert "SET s3_endpoint='s3.eu-central-1.amazonaws.com';" in executed_sql(conn)


def test_configure_s3_leaves_other_urls_alone():
    for url in [
        "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*",
        "https://data.source.coop/cholmes/nhd/NHDFlowline.parquet",
        "",
        None,
    ]:
        conn = MagicMock()
        configure_s3_for_url(conn, url)
        conn.execute.assert_not_called()


def contains(extent, x, y):
    xmin, ymin, xmax, ymax = extent
    return xmin <= x <= xmax and ymin <= y <= ymax


def test_quadkey_bounds():
    # Seattle and Nan, Thailand fall in these zoom-6 tiles
    assert contains(quadkey_bounds("021230"), -122.33, 47.61)
    assert contains(quadkey_bounds("132201"), 100.77, 18.78)
    assert not contains(quadkey_bounds("132201"), -122.33, 47.61)
    # Zoom-1 tile "3" is the south-east quarter of the world
    xmin, ymin, xmax, ymax = quadkey_bounds("3")
    assert (xmin, xmax) == (0.0, 180.0)
    assert ymax == 0.0 and ymin < -85


def test_extent_from_grid_file_name():
    assert extent_from_file_name("e100_n20_e105_n15.parquet") == (100, 15, 105, 20)
    assert extent_from_file_name("w170_n65_w165_n60.parquet") == (-170, 60, -165, 65)
    assert extent_from_file_name("e100_n00_e105_s05.parquet") == (100, -5, 105, 0)


def test_extent_from_file_name_ignores_other_names():
    for name in ["0.parquet", "part_1.parquet", "THA.parquet", "building.012.parquet",
                 "part-00000-170892fe-0fe0-43c1-999d-f0911ce43365-c000.zstd.parquet"]:
        assert extent_from_file_name(name) is None


def mock_glob(files):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(f,) for f in files]
    return conn


def test_parquet_source_prunes_quadkey_files():
    conn = mock_glob([PREFIX + f"building.{k}.parquet" for k in ("132201", "132203", "021230")])
    source = parquet_source_for_bbox(conn, SOURCE_COOP_GLOB, QgsRectangle(100.76, 18.77, 100.79, 18.80))
    assert source == f"['{PREFIX}building.132201.parquet']"


def test_parquet_source_prunes_grid_files():
    names = ["e100_n20_e105_n15", "e095_n20_e100_n15", "e100_n25_e105_n20", "w170_n65_w165_n60"]
    conn = mock_glob([GBA_PREFIX + f"{n}.parquet" for n in names])
    source = parquet_source_for_bbox(conn, GBA_GLOB, QgsRectangle(*NAN_THAILAND))
    assert source == f"['{GBA_PREFIX}e100_n20_e105_n15.parquet']"


def test_parquet_source_includes_neighbour_within_buffer():
    # An extent touching the 100° E cell edge also reads the western neighbour.
    # The third cell is far away, so the two are a real selection rather than
    # everything the glob matched.
    names = ["e100_n20_e105_n15", "e095_n20_e100_n15", "w170_n65_w165_n60"]
    conn = mock_glob([GBA_PREFIX + f"{n}.parquet" for n in names])
    source = parquet_source_for_bbox(conn, GBA_GLOB, QgsRectangle(100.005, 18.0, 100.1, 18.1))
    assert source == (
        f"['{GBA_PREFIX}e095_n20_e100_n15.parquet', '{GBA_PREFIX}e100_n20_e105_n15.parquet']"
    )


def test_parquet_source_falls_back_for_mixed_quadkey_lengths():
    conn = mock_glob([PREFIX + "building.1322.parquet", PREFIX + "building.132201.parquet"])
    assert parquet_source_for_bbox(conn, SOURCE_COOP_GLOB, QgsRectangle(*NAN_THAILAND)) == f"'{SOURCE_COOP_GLOB}'"


def test_parquet_source_no_overlap_returns_none():
    conn = mock_glob([PREFIX + "building.021230.parquet"])
    assert parquet_source_for_bbox(conn, SOURCE_COOP_GLOB, QgsRectangle(100.76, 18.77, 100.79, 18.80)) is None


def test_parquet_source_falls_back_for_other_file_names():
    url = "s3://bucket/by_country/*/*.parquet"
    conn = mock_glob(["s3://bucket/by_country/THA/THA.parquet", "s3://bucket/by_country/LAO/LAO.parquet"])
    assert parquet_source_for_bbox(conn, url, QgsRectangle(100, 18, 101, 19)) == f"'{url}'"


def test_parquet_source_skips_listing_for_single_files():
    conn = MagicMock()
    url = "https://data.source.coop/cholmes/nhd/NHDFlowline.parquet"
    assert parquet_source_for_bbox(conn, url, QgsRectangle(0, 0, 1, 1)) == f"'{url}'"
    conn.execute.assert_not_called()


def test_parquet_source_ignores_year_named_files():
    url = "s3://bucket/data/*.parquet"
    conn = mock_glob([f"s3://bucket/data/data_{y}.parquet" for y in (2020, 2021, 2022, 2023)])
    assert parquet_source_for_bbox(conn, url, QgsRectangle(*NAN_THAILAND)) == f"'{url}'"


def test_parquet_source_ignores_repeated_basenames():
    url = "s3://bucket/data/*/*.parquet"
    conn = mock_glob([f"s3://bucket/data/country={c}/part_00000.parquet" for c in ("TH", "LA", "VN")])
    assert parquet_source_for_bbox(conn, url, QgsRectangle(*NAN_THAILAND)) == f"'{url}'"


VIDA_S2 = "s3://us-west-2.opendata.source.coop/vida/google-microsoft-osm-open-buildings/geoparquet/by_country_s2/"
VIDA_S2_GLOB = VIDA_S2 + "*/*.parquet"


def contains_point(extent, x, y):
    xmin, ymin, xmax, ymax = extent
    return xmin <= x <= xmax and ymin <= y <= ymax


def test_s2_cell_level():
    assert s2_cell_level(1 << 60) == 0                   # face 0
    assert s2_cell_level(3544332906740580352) == 4       # VIDA THA cell
    assert s2_cell_level(-6052837899185946624) == 1      # signed int64 form (NZL)
    assert s2_cell_level(2) is None                      # lowest set bit at an odd position
    assert s2_cell_level(0) is None


def test_s2_cell_bounds_match_vida_cells():
    # Cells and the places VIDA puts in them (checked against the files' bbox statistics)
    assert contains_point(s2_cell_bounds(3544332906740580352), 100.77, 18.78)       # THA: Nan
    assert not contains_point(s2_cell_bounds(3544332906740580352), -0.12, 51.5)
    assert contains_point(s2_cell_bounds(10466365534009032704), -77.04, -12.05)     # PER: Lima
    assert contains_point(s2_cell_bounds(-6052837899185946624), 174.76, -36.85)     # NZL: Auckland


def test_s2_cells_on_the_antimeridian_stay_on_their_side():
    # Pacific cells with an edge on 180 degrees: west of it (Fiji) and east of it
    assert s2_cell_bounds(7782220156096217088) == pytest.approx((135, -45, 180, 0), abs=1e-6)
    assert s2_cell_bounds(8358680908399640576) == pytest.approx((-180, -45, -135, 0), abs=1e-6)
    # A level-4 cell in the Aleutians, touching 180 degrees from the west
    xmin, ymin, xmax, ymax = s2_cell_bounds(6354579074219769856)
    assert xmin > 170 and xmax == 180


def test_numbered_files_are_not_s2_cells():
    for name in ["0.parquet", "17.parquet", "1024.parquet", "20240101.parquet"]:
        assert extent_from_file_name(name) is None


def test_parquet_source_prunes_s2_files():
    files = [
        VIDA_S2 + "country_iso=THA/3544332906740580352.parquet",
        VIDA_S2 + "country_iso=LAO/3746994889972252672.parquet",
        VIDA_S2 + "country_iso=PER/10466365534009032704.parquet",
        VIDA_S2 + "country_iso=NZL/-6052837899185946624.parquet",
        VIDA_S2 + "country_iso=USA/6354579074219769856.parquet",
    ]
    source = parquet_source_for_bbox(mock_glob(files), VIDA_S2_GLOB, QgsRectangle(*NAN_THAILAND))
    assert source == f"['{files[1]}', '{files[0]}']"  # sorted: LAO, THA


OVERTURE_GLOB = (
    "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/*"
)
OVERTURE_PREFIX = (
    "s3://overturemaps-us-west-2/release/2026-09-23.1/theme=buildings/type=building/"
)
OVERTURE_KEY = ("2026-09-23.1", "buildings", "building")


def overture_part(number):
    """A file name shaped like Overture's, which carries no extent of its own."""
    return f"{OVERTURE_PREFIX}part-{number:05d}-23bda295-5d48-57ef-aa31-bce815c998ac-c000.zstd.parquet"


@pytest.fixture
def overture_stac(monkeypatch):
    """Seed the STAC cache so nothing reaches the network.

    Three files: Germany, the Americas, and Japan.
    """
    extents = {
        230: (8.99, 48.69, 11.07, 51.36),
        0: (-180.0, -84.29, -85.87, 13.99),
        511: (140.01, 39.66, 179.68, 77.11),
    }
    monkeypatch.setattr(utils, "_overture_extent_cache", {OVERTURE_KEY: extents})
    return extents


def test_overture_part_extents_ignores_other_urls():
    # No network for a url that is not an Overture release path
    assert overture_part_extents(SOURCE_COOP_GLOB) is None
    assert overture_part_extents("https://example.com/a.parquet") is None
    assert overture_part_extents(None) is None


def test_overture_part_extents_comes_from_the_cache(overture_stac):
    assert overture_part_extents(OVERTURE_GLOB) == overture_stac


def test_parquet_source_prunes_overture_by_stac(overture_stac):
    conn = mock_glob([overture_part(n) for n in (0, 230, 511)])
    # The user's Frankfurt extent, which only the Germany file covers
    source = parquet_source_for_bbox(conn, OVERTURE_GLOB, QgsRectangle(9.1252, 50.2389, 9.1514, 50.2439))
    assert source == f"['{overture_part(230)}']"


def test_parquet_source_falls_back_when_every_overture_file_matches(overture_stac):
    conn = mock_glob([overture_part(n) for n in (0, 230, 511)])
    # The whole world: listing every file gains nothing over the glob
    source = parquet_source_for_bbox(conn, OVERTURE_GLOB, QgsRectangle(-180, -90, 180, 90))
    assert source == f"'{OVERTURE_GLOB}'"


def test_parquet_source_overture_no_overlap_returns_none(overture_stac):
    conn = mock_glob([overture_part(n) for n in (0, 230, 511)])
    # The Indian Ocean, which none of the three cover
    assert parquet_source_for_bbox(conn, OVERTURE_GLOB, QgsRectangle(70.0, -30.0, 71.0, -29.0)) is None


def test_parquet_source_falls_back_for_an_unknown_overture_part(overture_stac):
    # A file the collection does not describe must not be silently skipped
    conn = mock_glob([overture_part(230), overture_part(400)])
    source = parquet_source_for_bbox(conn, OVERTURE_GLOB, QgsRectangle(9.1252, 50.2389, 9.1514, 50.2439))
    assert source == f"'{OVERTURE_GLOB}'"


def test_parquet_source_falls_back_when_the_stac_is_unavailable(monkeypatch):
    monkeypatch.setattr(utils, "_overture_extent_cache", {})

    def no_network(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(utils.urllib.request, "urlopen", no_network)
    conn = mock_glob([overture_part(230)])
    source = parquet_source_for_bbox(conn, OVERTURE_GLOB, QgsRectangle(9.1252, 50.2389, 9.1514, 50.2439))
    assert source == f"'{OVERTURE_GLOB}'"

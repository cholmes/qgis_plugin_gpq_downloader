"""What the plugin calls an Overture download: its file and its layer.

building_part shares theme=buildings with building, so naming by theme alone
would give the two downloads one name (#80).
"""

import pytest

from gpq_downloader.core.plan import overture_file_stem, overture_layer_name

RELEASE_URL = "s3://overturemaps-us-west-2/release/2026-09-23.1/theme={theme}/type={type}/*"


def url(theme, type_str):
    return RELEASE_URL.format(theme=theme, type=type_str)


@pytest.mark.parametrize("theme, type_str, stem, layer", [
    ("buildings", "building", "overture_buildings", "Overture Buildings"),
    ("buildings", "building_part", "overture_building_parts", "Overture Building Parts"),
    ("places", "place", "overture_places", "Overture Places"),
    ("transportation", "segment", "overture_transportation", "Overture Transportation"),
    ("addresses", "*", "overture_addresses", "Overture Addresses"),
    ("divisions", "division", "overture_divisions", "Overture Divisions"),
    ("divisions", "division_area", "overture_division_areas", "Overture Division Areas"),
    ("divisions", "division_boundary", "overture_division_boundaries", "Overture Division Boundaries"),
    ("base", "water", "overture_base_water", "Overture Base - Water"),
    ("base", "land_use", "overture_base_land_use", "Overture Base - Land_Use"),
])
def test_overture_names(theme, type_str, stem, layer):
    assert overture_file_stem(url(theme, type_str)) == stem
    assert overture_layer_name(url(theme, type_str)) == layer


def test_building_parts_do_not_share_a_name_with_buildings():
    buildings, parts = url("buildings", "building"), url("buildings", "building_part")
    assert overture_file_stem(buildings) != overture_file_stem(parts)
    assert overture_layer_name(buildings) != overture_layer_name(parts)


def test_the_divisions_subtypes_do_not_share_a_name():
    urls = [url("divisions", t) for t in ("division", "division_area", "division_boundary")]
    stems = [overture_file_stem(u) for u in urls]
    layers = [overture_layer_name(u) for u in urls]
    assert len(set(stems)) == 3
    assert len(set(layers)) == 3


def test_the_division_points_label_does_not_rename_the_layer():
    """The dialog labels type=division "Division Points" to tell it from the
    Divisions theme, but the file and layer names follow the type key (#215),
    so a download keeps the name it has always had."""
    assert overture_file_stem(url("divisions", "division")) == "overture_divisions"
    assert overture_layer_name(url("divisions", "division")) == "Overture Divisions"


def test_a_url_without_a_theme_still_gets_a_file_stem():
    assert overture_file_stem("s3://overturemaps-us-west-2/release/x/*") == "overture"


def test_a_url_without_a_type_is_named_by_its_theme():
    bare = "https://example.com/test.parquet?theme=buildings"
    assert overture_file_stem(bare) == "overture_buildings"
    assert overture_layer_name(bare) == "Overture Buildings"


def test_a_url_without_a_theme_has_no_layer_name():
    assert overture_layer_name("s3://overturemaps-us-west-2/release/x/*") is None

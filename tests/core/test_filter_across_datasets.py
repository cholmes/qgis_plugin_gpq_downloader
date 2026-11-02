"""A filter over more than one selected dataset (#217).

The owner asked for Buildings and Building Parts to be filterable together,
since they are "the same schema". Measured against Overture 2026-10-21.0 they
are nearly the same and the other groups are not, which is why none of this
is a hardcoded pair:

  buildings / building_part   23 columns shared; class, has_parts and subtype
                              are buildings-only, building_id part-only
  divisions, all three        12 of 28 shared, and `names` is not one of them
  base, all six               7 shared, all of them plumbing

So the rule is the schemas themselves: completion comes from the
intersection, and a column some of them have is a warning naming the one
that has not got it.
"""

import pytest

from gpq_downloader.core.filters import (
    column_feedback,
    dataset_label,
    shared_columns,
    unknown_column_message,
)

# Trimmed from the real schemas, keeping what differs
BUILDINGS = [
    ("id", "VARCHAR"),
    ("geometry", "GEOMETRY"),
    ("bbox", "STRUCT(xmin DOUBLE, xmax DOUBLE)"),
    ("version", "INTEGER"),
    ("names", 'STRUCT("primary" VARCHAR)'),
    ("height", "DOUBLE"),
    ("num_floors", "INTEGER"),
    ("class", "VARCHAR"),
    ("has_parts", "BOOLEAN"),
    ("subtype", "VARCHAR"),
]
BUILDING_PARTS = [
    ("id", "VARCHAR"),
    ("geometry", "GEOMETRY"),
    ("bbox", "STRUCT(xmin DOUBLE, xmax DOUBLE)"),
    ("version", "INTEGER"),
    ("names", 'STRUCT("primary" VARCHAR)'),
    ("height", "DOUBLE"),
    ("num_floors", "INTEGER"),
    ("building_id", "VARCHAR"),
]
DIVISION = [
    ("id", "VARCHAR"),
    ("geometry", "GEOMETRY"),
    ("subtype", "VARCHAR"),
    ("names", 'STRUCT("primary" VARCHAR)'),
    ("country", "VARCHAR"),
]
DIVISION_BOUNDARY = [
    ("id", "VARCHAR"),
    ("geometry", "GEOMETRY"),
    ("subtype", "VARCHAR"),
    ("country", "VARCHAR"),
]

PAIR = [("Building", BUILDINGS), ("Building Part", BUILDING_PARTS)]
DIVISIONS = [("Division", DIVISION), ("Division Boundary", DIVISION_BOUNDARY)]


# --- the intersection -----------------------------------------------------

def test_shared_columns_of_buildings_and_parts():
    """Everything but the four that differ, in the first schema's order."""
    assert [name for name, _type in shared_columns([BUILDINGS, BUILDING_PARTS])] == [
        "id", "geometry", "bbox", "version", "names", "height", "num_floors"
    ]


def test_shared_columns_keeps_the_type_so_struct_fields_still_work():
    shared = dict(shared_columns([BUILDINGS, BUILDING_PARTS]))
    assert shared["names"] == 'STRUCT("primary" VARCHAR)'


def test_one_schema_shares_everything_with_itself():
    assert shared_columns([BUILDINGS]) == BUILDINGS


def test_no_schemas_share_nothing():
    assert shared_columns([]) == []


def test_columns_are_shared_by_name_whatever_their_case():
    assert [name for name, _ in shared_columns([[("Class", "VARCHAR")], [("class", "VARCHAR")]])] == [
        "Class"
    ]


def test_a_column_whose_type_differs_is_not_shared():
    """`height > 10` cannot mean one thing across a DOUBLE and a VARCHAR: the
    VARCHAR side compares as text, where '9' > '10', so that download would
    come back with the wrong rows and nothing to say so. Not sharing the
    column says it instead.
    """
    assert shared_columns([[("height", "DOUBLE")], [("height", "VARCHAR")]]) == []


def test_a_struct_column_that_is_a_scalar_elsewhere_is_not_shared():
    """Offering names.primary because the first schema has the struct would
    complete a field the other dataset cannot extract.
    """
    both = [[("names", 'STRUCT("primary" VARCHAR)')], [("names", "VARCHAR")]]
    assert shared_columns(both) == []


def test_type_text_is_compared_past_spacing_and_case():
    """The same type spelled differently is still the same type."""
    both = [[("bbox", "STRUCT(xmin  DOUBLE)")], [("bbox", "struct(xmin DOUBLE)")]]
    assert [name for name, _ in shared_columns(both)] == ["bbox"]


def test_plain_name_rows_are_matched_whole():
    """A column list of bare names matches by name, not by first letter."""
    assert shared_columns([["height", "hclass"], ["height"]]) == ["height"]


def test_divisions_share_little_and_not_names():
    shared = [name for name, _ in shared_columns([DIVISION, DIVISION_BOUNDARY])]
    assert shared == ["id", "geometry", "subtype", "country"]
    assert "names" not in shared


# --- checking a fragment across them --------------------------------------

@pytest.mark.parametrize("fragment", [
    "height > 10",
    "names.primary = 'Flatiron'",
    "num_floors >= 3 AND height > 10",
    "bbox.xmin > 7",
])
def test_a_filter_on_shared_columns_is_fine_across_both(fragment):
    assert column_feedback(fragment, PAIR) is None


@pytest.mark.parametrize("fragment, expected", [
    ("class = 'school'",
     'Column "class" is not in every selected dataset (missing from "Building Part").'),
    ("has_parts = true",
     'Column "has_parts" is not in every selected dataset (missing from "Building Part").'),
    ("building_id IS NOT NULL",
     'Column "building_id" is not in every selected dataset (missing from "Building").'),
    ("height > 10 AND subtype = 'residential'",
     'Column "subtype" is not in every selected dataset (missing from "Building Part").'),
])
def test_a_column_only_some_of_them_have_names_the_one_without(fragment, expected):
    assert column_feedback(fragment, PAIR) == expected


def test_a_field_only_some_of_them_have_is_reported_as_a_field():
    assert column_feedback("names.primary = 'x'", DIVISIONS) == (
        'Column "names" is not in every selected dataset '
        '(missing from "Division Boundary").'
    )


def test_a_column_none_of_them_have_keeps_the_unknown_message():
    assert column_feedback("zzzzzz > 10", PAIR) == (
        'Unknown column "zzzzzz" - the data has no column by that name.'
    )
    assert column_feedback("hieght > 10", PAIR) == (
        'Unknown column "hieght" - did you mean "height"?'
    )


def test_the_suggestion_can_name_a_column_only_one_of_them_has():
    """The union is what is searched: "class" is worth suggesting even though
    only one of the two has it, because that is what was meant."""
    assert column_feedback("clas = 'x'", PAIR) == (
        'Unknown column "clas" - did you mean "class"?'
    )


def test_the_quote_trap_still_comes_first_across_datasets():
    assert column_feedback('names.primary = "Flatiron"', PAIR) == (
        '"Flatiron" is not a column. For a text value use single quotes: \'Flatiron\''
    )


def test_the_first_thing_wrong_is_what_is_reported():
    assert column_feedback("class = 'a' AND hieght > 1", PAIR) == (
        'Column "class" is not in every selected dataset (missing from "Building Part").'
    )


@pytest.mark.parametrize("schemas", [[], [("Building", [])], None])
def test_with_no_schemas_nothing_is_judged(schemas):
    assert column_feedback("class = 'school'", schemas) is None


def test_a_schema_that_could_not_be_read_is_left_out_rather_than_guessed_at():
    """A dataset whose DESCRIBE failed arrives as an empty column list; it
    must not make every column look missing."""
    assert column_feedback("class = 'school'", [("Building", BUILDINGS), ("Part", [])]) is None


def test_one_schema_behaves_exactly_as_it_did_before():
    """unknown_column_message() is this function with a single schema."""
    for fragment in [
        "class = 'school'",
        "clas = 'school'",
        "hieght > 1",
        'names.primary = "Jack Park"',
        "names.primry = 'x'",
        "height > 10",
    ]:
        assert column_feedback(fragment, [("Building", BUILDINGS)]) == (
            unknown_column_message(fragment, BUILDINGS)
        )


# --- naming a dataset in a message ----------------------------------------

@pytest.mark.parametrize("url, expected", [
    # Overture names the record type in the path
    ("s3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*",
     "Building"),
    ("s3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building_part/*",
     "Building Part"),
    ("s3://overturemaps-us-west-2/release/{release}/theme=divisions/type=division_area/*",
     "Division Area"),
    ("s3://overturemaps-us-west-2/release/{release}/theme=base/type=land_cover/*",
     "Land Cover"),
    # addresses has no single type, so the theme names it
    ("s3://overturemaps-us-west-2/release/{release}/theme=addresses/type=*/*",
     "Addresses"),
    # everything else is named by its file
    ("https://data.openstreetmap.us/layercake/pois.parquet", "pois"),
    ("https://data.openstreetmap.us/layercake/buildings.parquet", "buildings"),
    ("s3://bucket/some/folder/*", "folder"),
    ("https://example.com/data/places.parquet", "places"),
    ("", ""),
    (None, ""),
])
def test_dataset_label(url, expected):
    assert dataset_label(url) == expected

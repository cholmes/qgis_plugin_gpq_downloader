"""Reading DuckDB's printed column types, so the filter box can offer the
fields of a struct (#217).

Every type string here was taken off the real Overture divisions schema
(s3://overturemaps-us-west-2/release/2026-10-21.0/theme=divisions/
type=division/), because the awkward parts are all real: reserved words
quoted inside the type ("primary", "between", "version"), arbitrary nesting,
[] and [][] suffixes, MAP(K, V), and GEOMETRY('OGC:CRS84') carrying a quoted
literal. Splitting on commas is exactly the way to get this wrong.

Which paths DuckDB will actually accept was probed rather than assumed:
    names.primary         OK     a reserved word needs no quotes in a field
    names."primary"       OK
    bbox.xmin             OK
    names.common.en       OK     a MAP is reached by dot, but its keys are
                                 nowhere in the schema
    sources.property      FAILS  Cannot extract field ... it is not a struct
    sources[1].property   OK
    names.rules.variant   FAILS  same: rules is STRUCT(...)[]
"""

import pytest

from gpq_downloader.core.schema import (
    STRUCT,
    field_candidates,
    field_names,
    parse_column_type,
    resolve_path,
    strip_subscripts,
)

NAMES = (
    'STRUCT("primary" VARCHAR, common MAP(VARCHAR, VARCHAR), rules '
    'STRUCT(variant VARCHAR, "language" VARCHAR, perspectives STRUCT("mode" '
    'VARCHAR, countries VARCHAR[]), "value" VARCHAR, "between" DOUBLE[], side '
    "VARCHAR)[])"
)
SOURCES = (
    'STRUCT(property VARCHAR, dataset VARCHAR, license VARCHAR, record_id '
    'VARCHAR, update_time VARCHAR, confidence DOUBLE, "between" DOUBLE[], '
    'provider VARCHAR, resource VARCHAR, "version" VARCHAR)[]'
)
BBOX = "STRUCT(xmin DOUBLE, xmax DOUBLE, ymin DOUBLE, ymax DOUBLE)"
HIERARCHIES = 'STRUCT(division_id VARCHAR, subtype VARCHAR, "name" VARCHAR)[][]'
CARTOGRAPHY = (
    "STRUCT(prominence INTEGER, min_zoom INTEGER, max_zoom INTEGER, "
    "sort_key INTEGER)"
)

SCHEMA = [
    ("id", "VARCHAR"),
    ("geometry", "GEOMETRY('OGC:CRS84')"),
    ("bbox", BBOX),
    ("names", NAMES),
    ("sources", SOURCES),
    ("hierarchies", HIERARCHIES),
    ("cartography", CARTOGRAPHY),
    ("local_type", "MAP(VARCHAR, VARCHAR)"),
    ("subtype", "VARCHAR"),
    ("population", "BIGINT"),
    ("is_land", "BOOLEAN"),
]


# --- the parser ------------------------------------------------------------

@pytest.mark.parametrize("text, base, depth", [
    ("VARCHAR", "VARCHAR", 0),
    ("BIGINT", "BIGINT", 0),
    ("DOUBLE[]", "DOUBLE", 1),
    ("VARCHAR[][]", "VARCHAR", 2),
    # a parenthesised argument list is not a struct and is not a field
    ("DECIMAL(18,3)", "DECIMAL", 0),
    ("GEOMETRY('OGC:CRS84')", "GEOMETRY", 0),
    ("MAP(VARCHAR, VARCHAR)", "MAP", 0),
    ("MAP(VARCHAR, STRUCT(a INTEGER))", "MAP", 0),
    (BBOX, STRUCT, 0),
    (NAMES, STRUCT, 0),
    (SOURCES, STRUCT, 1),
    (HIERARCHIES, STRUCT, 2),
    # a fixed-size array keeps its depth
    ("DOUBLE[4]", "DOUBLE", 1),
])
def test_the_base_type_and_how_many_lists_wrap_it(text, base, depth):
    parsed = parse_column_type(text)
    assert (parsed.base, parsed.list_depth) == (base, depth)


@pytest.mark.parametrize("text, expected", [
    (BBOX, ["xmin", "xmax", "ymin", "ymax"]),
    (CARTOGRAPHY, ["prominence", "min_zoom", "max_zoom", "sort_key"]),
    # the quotes around a reserved word are the type string's, not the name's
    (NAMES, ["primary", "common", "rules"]),
    (
        SOURCES,
        [
            "property", "dataset", "license", "record_id", "update_time",
            "confidence", "between", "provider", "resource", "version",
        ],
    ),
    (HIERARCHIES, ["division_id", "subtype", "name"]),
    # nothing to take fields from
    ("VARCHAR", []),
    ("MAP(VARCHAR, VARCHAR)", []),
    ("GEOMETRY('OGC:CRS84')", []),
    ("", []),
    ("DECIMAL(18,3)", []),
])
def test_field_names_never_split_on_a_nested_comma(text, expected):
    assert field_names(text) == expected


def test_a_nested_struct_keeps_its_own_fields():
    rules = dict(parse_column_type(NAMES).fields)["rules"]
    assert rules.base == STRUCT and rules.list_depth == 1
    assert [name for name, _ in rules.fields] == [
        "variant", "language", "perspectives", "value", "between", "side"
    ]
    perspectives = dict(rules.fields)["perspectives"]
    assert [name for name, _ in perspectives.fields] == ["mode", "countries"]
    assert dict(perspectives.fields)["countries"].list_depth == 1


def test_each_field_keeps_the_type_string_as_duckdb_printed_it():
    fields = dict(parse_column_type(NAMES).fields)
    assert fields["primary"].text == "VARCHAR"
    assert fields["common"].text == "MAP(VARCHAR, VARCHAR)"
    assert fields["rules"].text.startswith("STRUCT(variant VARCHAR,")
    assert fields["rules"].text.endswith(")[]")


@pytest.mark.parametrize("text", [
    "STRUCT(",
    "STRUCT(a",
    "STRUCT(a VARCHAR",
    "STRUCT()",
    "))))",
    "[]",
    None,
    "MAP(",
])
def test_a_type_it_cannot_read_is_not_an_exception(text):
    """These come off a remote file; a DESCRIBE this parser chokes on must
    cost the completer its suggestions, not the dialog its event loop."""
    parsed = parse_column_type(text)
    assert parsed.fields == () or isinstance(parsed.fields, tuple)
    assert field_names(text) == [] or parsed.base == STRUCT


# --- which paths resolve ---------------------------------------------------

@pytest.mark.parametrize("path, expected", [
    (("bbox",), ["xmin", "xmax", "ymin", "ymax"]),
    (("names",), ["primary", "common", "rules"]),
    (("cartography",), ["prominence", "min_zoom", "max_zoom", "sort_key"]),
    # unquoted names fold the way DuckDB's do
    (("BBOX",), ["xmin", "xmax", "ymin", "ymax"]),
    (("Names",), ["primary", "common", "rules"]),
])
def test_a_plain_struct_offers_its_fields(path, expected):
    assert [name for name, _label in field_candidates(SCHEMA, path)] == expected


@pytest.mark.parametrize("path", [
    # a list of structs: dot access is a binder error, so nothing is offered
    ("sources",),
    ("hierarchies",),
    ("names", "rules"),
    # a MAP's keys are not in the schema; there is nothing to suggest
    ("local_type",),
    ("names", "common"),
    # and neither a scalar nor a name the data has not got has fields
    ("subtype",),
    ("bbox", "xmin"),
    ("nosuchcolumn",),
    ("names", "nosuchfield"),
    (),
])
def test_nothing_is_offered_where_a_dot_cannot_be_followed(path):
    assert field_candidates(SCHEMA, path) == []


def test_the_candidate_label_carries_the_field_type():
    assert dict(field_candidates(SCHEMA, ("bbox",)))["xmin"] == "xmin  DOUBLE"
    assert dict(field_candidates(SCHEMA, ("names",)))["common"] == (
        "common  MAP(VARCHAR, VARCHAR)"
    )


def test_a_long_type_is_cut_short_for_the_popup():
    """names' own type runs to 300 characters; a popup row that wide is worse
    than no type at all."""
    label = dict(field_candidates(SCHEMA, ("names",)))["rules"]
    assert label.startswith("rules  STRUCT(")
    assert label.endswith("...")
    assert len(label) < 70


# --- resolve_path, which the unknown-field check is built on ---------------

@pytest.mark.parametrize("path, outcome, base", [
    (("bbox",), "struct", STRUCT),
    (("bbox", "xmin"), "scalar", "DOUBLE"),
    (("names", "primary"), "scalar", "VARCHAR"),
    (("names", "rules"), "list", STRUCT),
    (("sources",), "list", STRUCT),
    (("local_type",), "unknowable", "MAP"),
    (("names", "common"), "unknowable", "MAP"),
    # past a MAP nothing can be judged: the key could be anything
    (("names", "common", "en"), "unknowable", "MAP"),
    (("geometry",), "scalar", "GEOMETRY"),
])
def test_resolve_path_says_what_a_path_lands_on(path, outcome, base):
    resolved = resolve_path(SCHEMA, path)
    assert (resolved.outcome, resolved.type.base) == (outcome, base)


@pytest.mark.parametrize("path, outcome, missing", [
    (("nosuchcolumn",), "unknown_column", "nosuchcolumn"),
    (("names", "primry"), "unknown_field", "primry"),
    (("bbox", "xmn"), "unknown_field", "xmn"),
    # the first thing wrong is what is reported
    (("names", "primry", "deeper"), "unknown_field", "primry"),
    # a list is reported as a list, not as a missing field
    (("sources", "property"), "list", None),
    (("names", "rules", "variant"), "list", None),
])
def test_resolve_path_reports_the_first_thing_wrong(path, outcome, missing):
    resolved = resolve_path(SCHEMA, path)
    assert resolved.outcome == outcome
    assert resolved.missing == missing


def test_resolve_path_with_no_schema_judges_nothing():
    """Until the DESCRIBE has come back there is nothing to check against."""
    assert resolve_path([], ("cg",)).outcome == "unknowable"
    assert resolve_path([], ("names", "primry")).outcome == "unknowable"


def test_the_list_outcome_carries_where_the_list_was():
    """So the message can say sources[1].property rather than just "no"."""
    resolved = resolve_path(SCHEMA, ("sources", "property"))
    assert resolved.outcome == "list"
    assert resolved.list_path == ("sources",)
    resolved = resolve_path(SCHEMA, ("names", "rules", "variant"))
    assert resolved.list_path == ("names", "rules")


# --- the indexed form ------------------------------------------------------

@pytest.mark.parametrize("path, expected", [
    (("sources[1]",), ["property", "dataset", "license", "record_id",
                       "update_time", "confidence", "between", "provider",
                       "resource", "version"]),
    (("names", "rules[1]"), ["variant", "language", "perspectives", "value",
                             "between", "side"]),
    # [][] needs both indexes before the dot will follow
    (("hierarchies[1]",), []),
    (("hierarchies[1][1]",), ["division_id", "subtype", "name"]),
])
def test_an_indexed_list_offers_its_element_fields(path, expected):
    assert [name for name, _label in field_candidates(SCHEMA, path)] == expected


@pytest.mark.parametrize("part, expected", [
    ("sources", ("sources", 0)),
    ("sources[1]", ("sources", 1)),
    ("sources[]", ("sources", 1)),
    ("hierarchies[1][2]", ("hierarchies", 2)),
    ("sources[ 3 ]", ("sources", 1)),
    # not a subscript: a map key, and a name that merely ends in a bracket
    ("common['en']", ("common['en']", 0)),
    ("odd]name", ("odd]name", 0)),
])
def test_strip_subscripts(part, expected):
    assert strip_subscripts(part) == expected

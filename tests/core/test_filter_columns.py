"""The filter box's column knowledge: which names a fragment reads, which of
them the data does not have, and the word a completer should complete.

All of it is headless (core/filters.py), so the dialog's completer and its
inline "did you mean" note are a thin wrapper over cases pinned here. The
schema these run against is Overture's places, trimmed; the "cg" cases are
the report that started this: the user typed `cg = cv` and learnt what the
columns were called only from a DuckDB Binder Error after the download.
"""

import pytest

from gpq_downloader.core.filters import (
    completion_candidates,
    completion_text,
    current_word,
    identifier_paths_in,
    identifiers_in,
    unknown_column_message,
    unknown_identifiers,
)

COLUMNS = [
    "id",
    "geometry",
    "bbox",
    "class",
    "subclass",
    "names",
    "confidence",
    "sources",
    "version",
    "theme",
    "type",
]


# --- which names a fragment reads ------------------------------------------

@pytest.mark.parametrize("fragment, expected", [
    ("", []),
    ("   ", []),
    (None, []),
    ("confidence > 0.9", ["confidence"]),
    ("cg = cv", ["cg", "cv"]),
    ("confidence > 0.9 AND class = 'school'", ["confidence", "class"]),
    ("class IN ('school', 'college') OR height IS NULL", ["class", "height"]),
    ("(a = 1 OR b = 2) AND c <> 3", ["a", "b", "c"]),
    # a quoted name is a column, keywords and types inside it or not
    ('"my column" = 1', ["my column"]),
    ('"odd ""name""" = 1', ['odd "name"']),
    ('"select" = 1', ["select"]),
    # a dotted path names its root column, not its struct fields
    ("names.primary ILIKE '%park%'", ["names"]),
    ("bbox.xmin > 10 AND bbox.ymax < 20", ["bbox"]),
    ("list_contains(categories.alternate, 'cafe')", ["categories"]),
    # a word in front of '(' is a function, not a column
    ("length(class) > 3", ["class"]),
    ("lower(name) = 'x'", ["name"]),
    # literals hold no columns, whatever they spell
    ("note = 'confidence > 0.9'", ["note"]),
    ("src = 'read_text(x)'", ["src"]),
    ("name = 'O''Brien'", ["name"]),
    (r"name = E'O\'Brien' AND class = 'x'", ["name", "class"]),
    # numbers are not names, and a decimal point is not a struct path
    ("confidence > 0.9 AND version >= 1e5", ["confidence", "version"]),
    ("a > -1.5e-3", ["a"]),
    # keywords, type names and interval units are not columns
    ("class IS NOT NULL AND version::VARCHAR LIKE '2%'", ["class", "version"]),
    ("CASE WHEN class = 'a' THEN 1 ELSE 0 END = 1", ["class"]),
    ("height BETWEEN 1 AND 2", ["height"]),
    ("CAST(version AS INTEGER) > 1", ["version"]),
    # each name once, in the order it first appears
    ("class = 'a' OR class = 'b'", ["class"]),
    # unquoted names fold like DuckDB's, so the first spelling is kept
    ("Confidence > 0.9 AND confidence < 1", ["Confidence"]),
    # half-typed text is scanned as far as it goes, not refused
    ("class = 'scho", ["class"]),
    ("confidence > 0.9 AND ", ["confidence"]),
])
def test_identifiers_in(fragment, expected):
    assert identifiers_in(fragment) == expected


# --- which of them the data does not have ----------------------------------

def test_unknown_identifiers_finds_the_typo_and_suggests_the_column():
    """The report that started this: `cg = cv` against Overture's places."""
    # Both halves are unknown and both are nearest "class"; the dialog reports
    # the first, and the second once that one is fixed
    assert unknown_identifiers("cg = cv", COLUMNS) == [("cg", "class"), ("cv", "class")]


@pytest.mark.parametrize("fragment, expected", [
    # nothing close enough, so no guess rather than a bad one
    ("zzzzzz = 1", [("zzzzzz", None)]),
    ("hieght > 1", [("hieght", None)]),
    # an abbreviation scores too low for difflib, but it is the common slip
    ("conf > 0.9", [("conf", "confidence")]),
    ("sub = 'a'", [("sub", "subclass")]),
])
def test_suggestions_are_offered_only_where_one_is_likely(fragment, expected):
    assert unknown_identifiers(fragment, COLUMNS) == expected


def test_every_unknown_name_is_reported_in_order():
    assert unknown_identifiers("clas = 'a' AND confidence > 0.9 AND hieght > 1", COLUMNS) == [
        ("clas", "class"),
        ("hieght", None),
    ]


@pytest.mark.parametrize("fragment", [
    "",
    "confidence > 0.9",
    "confidence > 0.9 AND class = 'school'",
    "bbox.xmin > 10",
    "names.primary ILIKE '%park%'",
    '"class" = 1',
    # DuckDB matches unquoted names without regard to case, so neither does this
    "CONFIDENCE > 0.9",
    "Class = 'school'",
])
def test_a_fragment_on_real_columns_has_nothing_unknown(fragment):
    assert unknown_identifiers(fragment, COLUMNS) == []


def test_with_no_schema_nothing_is_unknown():
    """Until the column list has arrived there is nothing to check against."""
    assert unknown_identifiers("cg = cv", []) == []
    assert unknown_column_message("cg = cv", []) is None


@pytest.mark.parametrize("fragment, expected", [
    ("cg = cv", 'Unknown column "cg" - did you mean "class"?'),
    ("clas = 'school'", 'Unknown column "clas" - did you mean "class"?'),
    ("confidence > 0.9 AND clas = 'x'", 'Unknown column "clas" - did you mean "class"?'),
    ("geometrie IS NULL", 'Unknown column "geometrie" - did you mean "geometry"?'),
    ('"Clas" = 1', 'Unknown column "Clas" - did you mean "class"?'),
    # nothing close enough to name: say what is wrong without guessing
    ("zzzzzz = 1", 'Unknown column "zzzzzz" - the data has no column by that name.'),
])
def test_unknown_column_message(fragment, expected):
    assert unknown_column_message(fragment, COLUMNS) == expected


@pytest.mark.parametrize("fragment", ["", "   ", None, "confidence > 0.9"])
def test_a_fragment_with_nothing_unknown_has_no_message(fragment):
    assert unknown_column_message(fragment, COLUMNS) is None


# --- the word a completer should complete ----------------------------------

@pytest.mark.parametrize("text, cursor, expected", [
    # the whole line, when that is all there is
    ("conf", 4, (0, "conf", ())),
    ("", 0, (0, "", ())),
    # mid-expression: after AND, inside parens, after an operator
    ("confidence > 0.9 AND cl", 23, (21, "cl", ())),
    ("(class = 'a' OR sub", 19, (16, "sub", ())),
    ("height>", 7, (7, "", ())),
    ("confidence > 0.9 AND ", 21, (21, "", ())),
    # the cursor need not be at the end of the line
    ("conf AND class = 'a'", 4, (0, "conf", ())),
    ("xx AND class = 'a'", 2, (0, "xx", ())),
    # a cursor in the middle of a word takes only what is behind it
    ("class", 3, (0, "cla", ())),
    # out-of-range cursors are clamped rather than raising
    ("conf", 99, (0, "conf", ())),
    ("conf", -1, (0, "", ())),
    # after a dot the word belongs to the struct in front of it
    ("names.", 6, (6, "", ("names",))),
    ("names.pri", 9, (6, "pri", ("names",))),
    ("bbox.xm", 7, (5, "xm", ("bbox",))),
    ("confidence > 0.9 AND names.pri", 30, (27, "pri", ("names",))),
    ("names.rules.var", 15, (12, "var", ("names", "rules"))),
    ('"my col".x', 10, (9, "x", ("my col",))),
    ("names . pri", 11, (8, "pri", ("names",))),
    # a decimal point is not a struct path
    ("a > 0.9", 7, None),
])
def test_current_word(text, cursor, expected):
    assert current_word(text, cursor) == expected


@pytest.mark.parametrize("text, cursor", [
    # inside a string literal: a value, not a column name
    ("class = 'scho", 13),
    ("class = 'school' OR class = 'coll", 33),
    ("name = 'O''Br", 13),
    # inside a quoted name
    ('"my col', 7),
])
def test_no_completion_where_a_column_name_cannot_be_typed(text, cursor):
    assert current_word(text, cursor) is None


@pytest.mark.parametrize("text, cursor", [
    # a closed literal leaves the string: the next word is completable again
    ("class = 'school' AND co", 23),
    (r"name = E'O\'Brien' AND co", 25),
])
def test_completion_resumes_after_a_closed_literal(text, cursor):
    spot = current_word(text, cursor)
    assert spot is not None and spot[1] == "co"


# --- the candidate list ----------------------------------------------------

def test_completion_candidates_pair_each_column_with_its_type():
    schema = [("class", "VARCHAR"), ("bbox", "STRUCT(xmin FLOAT)")]
    assert completion_candidates(schema) == [
        ("class", "class  VARCHAR"),
        ("bbox", "bbox  STRUCT(xmin FLOAT)"),
    ]


def test_completion_candidates_cope_with_a_missing_type():
    assert completion_candidates([("class",), ("bbox", None)]) == [
        ("class", "class"),
        ("bbox", "bbox"),
    ]


@pytest.mark.parametrize("name, expected", [
    ("class", "class"),
    ("confidence", "confidence"),
    ("_private", "_private"),
    ("col2", "col2"),
    # quoted only where a bare name would not be read as one
    ("my column", '"my column"'),
    ("2fast", '"2fast"'),
    ("select", '"select"'),
    ("AND", '"AND"'),
    ("odd\"name", '"odd""name"'),
    ("", '""'),
])
def test_completion_text_quotes_only_what_has_to_be(name, expected):
    assert completion_text(name) == expected


# --- the quote trap --------------------------------------------------------
#
# What the owner actually hit, against Overture divisions:
#
#     names.primary = "Jack Park"
#
# answered with `Unknown column "Jack Park" - the data has no column by that
# name`. Accurate and useless: he typed a value, and double quotes in SQL are
# an identifier. Confirmed against DuckDB 1.5.3 - the single-quoted form
# returns the row, the double-quoted one raises exactly that Binder Error.

QUOTE_HINT = '"Jack Park" is not a column. For a text value use single quotes: \'Jack Park\''


@pytest.mark.parametrize("fragment", [
    'names.primary = "Jack Park"',
    'names.primary="Jack Park"',
    'class = \'a\' AND names.primary = "Jack Park"',
    'names.primary <> "Jack Park"',
    'names.primary LIKE "Jack Park"',
    'names.primary ilike "Jack Park"',
])
def test_a_double_quoted_value_after_a_comparison_says_so(fragment):
    assert unknown_column_message(fragment, COLUMNS) == QUOTE_HINT


def test_the_hint_names_the_text_the_user_typed():
    assert unknown_column_message('class = "Jack\'s Park"', COLUMNS) == (
        '"Jack\'s Park" is not a column. '
        "For a text value use single quotes: 'Jack''s Park'"
    )


@pytest.mark.parametrize("fragment, expected", [
    # on the left of the operator it really does read as a column name
    ('"Clas" = 1', 'Unknown column "Clas" - did you mean "class"?'),
    ('"my column" = 1', 'Unknown column "my column" - the data has no column by that name.'),
    # and a quoted name that exists is no trap at all
    ('"class" = \'a\'', None),
    ('class = "class"', None),
])
def test_a_quoted_name_is_only_a_trap_where_a_value_belongs(fragment, expected):
    assert unknown_column_message(fragment, COLUMNS) == expected


# --- struct paths ----------------------------------------------------------

STRUCT_SCHEMA = [
    ("id", "VARCHAR"),
    ("subtype", "VARCHAR"),
    ("bbox", "STRUCT(xmin DOUBLE, xmax DOUBLE, ymin DOUBLE, ymax DOUBLE)"),
    ("names", 'STRUCT("primary" VARCHAR, common MAP(VARCHAR, VARCHAR), '
              "rules STRUCT(variant VARCHAR)[])"),
    ("sources", "STRUCT(property VARCHAR, dataset VARCHAR)[]"),
]


@pytest.mark.parametrize("fragment", [
    "names.primary = 'Jack Park'",
    'names."primary" = \'Jack Park\'',
    "bbox.xmin > 7",
    "names.common.en = 'Jack Park'",  # a MAP key cannot be checked
    "subtype = 'locality'",
    "sources[1].property = 'p'",
])
def test_a_path_that_duckdb_accepts_draws_no_complaint(fragment):
    assert unknown_column_message(fragment, STRUCT_SCHEMA) is None


@pytest.mark.parametrize("fragment, expected", [
    ("names.primry = 'x'", 'Unknown field "names.primry" - did you mean "names.primary"?'),
    ("bbox.xmn > 7", 'Unknown field "bbox.xmn" - did you mean "bbox.xmin"?'),
    ("bbox.zzzzzz > 7", 'Unknown field "bbox.zzzzzz" - "bbox" has no field by that name.'),
    # the root is still checked first
    ("nmes.primary = 'x'", 'Unknown column "nmes" - did you mean "names"?'),
])
def test_a_misspelled_field_is_caught_like_a_misspelled_column(fragment, expected):
    assert unknown_column_message(fragment, STRUCT_SCHEMA) == expected


@pytest.mark.parametrize("fragment, expected", [
    ("sources.property = 'p'",
     '"sources" is a list - use sources[1].property, not sources.property.'),
    ("names.rules.variant = 'x'",
     '"names.rules" is a list - use names.rules[1].variant, not names.rules.variant.'),
])
def test_a_list_of_structs_is_told_how_to_be_indexed(fragment, expected):
    """Dot access into a STRUCT(...)[] is a binder error; the message says
    what does work instead."""
    assert unknown_column_message(fragment, STRUCT_SCHEMA) == expected


def test_with_only_names_and_no_types_paths_are_left_alone():
    """The column list is sometimes just names (the older callers); a path
    cannot be judged without the types, so it is not guessed at."""
    assert unknown_column_message("names.primry = 'x'", ["names", "bbox"]) is None
    assert unknown_column_message("cg = 1", ["names", "bbox"]) is not None


# --- the paths a fragment reads -------------------------------------------

@pytest.mark.parametrize("fragment, expected", [
    ("cg = cv", [("cg",), ("cv",)]),
    ("names.primary = 'x'", [("names", "primary")]),
    ("bbox.xmin > 10 AND bbox.ymax < 20", [("bbox", "xmin"), ("bbox", "ymax")]),
    ("names.rules.variant = 'x'", [("names", "rules", "variant")]),
    ("list_contains(categories.alternate, 'cafe')", [("categories", "alternate")]),
    ('"my column".x = 1', [("my column", "x")]),
    # a keyword between two paths does not join them
    ("names.primary = 'a' AND bbox.xmin > 1",
     [("names", "primary"), ("bbox", "xmin")]),
    ("a = 1 OR b.c = 2", [("a",), ("b", "c")]),
    # each path once, under the spelling it first had
    ("bbox.xmin > 1 AND BBOX.XMIN < 2", [("bbox", "xmin")]),
])
def test_identifier_paths_in(fragment, expected):
    assert [ref.path for ref in identifier_paths_in(fragment)] == expected


def test_a_reference_says_whether_it_was_quoted_and_what_precedes_it():
    first, second = identifier_paths_in('class = "Jack Park"')
    assert (first.path, first.quoted, first.after_comparison) == (("class",), False, False)
    assert (second.path, second.quoted, second.after_comparison) == (
        ("Jack Park",), True, True
    )


# --- the indexed form, which is what DuckDB wants for a list of structs ----

@pytest.mark.parametrize("fragment, expected", [
    ("sources[1].property = 'p'", [("sources[1]", "property")]),
    ("sources[1].propery = 'p'", [("sources[1]", "propery")]),
    ("sources[ 2 ].property = 'p'", [("sources[2]", "property")]),
    ("names.rules[1].variant = 'x'", [("names", "rules[1]", "variant")]),
    # a quoted MAP key is not a subscript: its keys are not in the schema
    ("names.common['en'] = 'x'", [("names", "common")]),
])
def test_a_subscript_stays_with_the_part_it_indexes(fragment, expected):
    assert [ref.path for ref in identifier_paths_in(fragment)] == expected


def test_identifiers_in_drops_the_subscript_from_the_root():
    assert identifiers_in("sources[1].property = 'p'") == ["sources"]


@pytest.mark.parametrize("fragment, expected", [
    ("sources[1].property = 'p'", None),
    ("names.rules[1].variant = 'x'", None),
    ("sources[1].propery = 'p'",
     'Unknown field "sources[1].propery" - did you mean "sources[1].property"?'),
    ("names.rules[1].varient = 'x'",
     'Unknown field "names.rules[1].varient" - did you mean "names.rules[1].variant"?'),
])
def test_an_indexed_path_is_followed_and_still_checked(fragment, expected):
    assert unknown_column_message(fragment, STRUCT_SCHEMA) == expected


@pytest.mark.parametrize("text, cursor, expected", [
    ("sources[1].pro", 14, (11, "pro", ("sources[1]",))),
    ("sources[1].", 11, (11, "", ("sources[1]",))),
    ("names.rules[1].var", 18, (15, "var", ("names", "rules[1]"))),
])
def test_completion_follows_an_indexed_path(text, cursor, expected):
    assert current_word(text, cursor) == expected

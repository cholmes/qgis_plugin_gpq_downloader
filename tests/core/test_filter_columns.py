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
    ("conf", 4, (0, "conf")),
    ("", 0, (0, "")),
    # mid-expression: after AND, inside parens, after an operator
    ("confidence > 0.9 AND cl", 23, (21, "cl")),
    ("(class = 'a' OR sub", 19, (16, "sub")),
    ("height>", 7, (7, "")),
    ("confidence > 0.9 AND ", 21, (21, "")),
    # the cursor need not be at the end of the line
    ("conf AND class = 'a'", 4, (0, "conf")),
    ("xx AND class = 'a'", 2, (0, "xx")),
    # a cursor in the middle of a word takes only what is behind it
    ("class", 3, (0, "cla")),
    # out-of-range cursors are clamped rather than raising
    ("conf", 99, (0, "conf")),
    ("conf", -1, (0, "")),
])
def test_current_word(text, cursor, expected):
    assert current_word(text, cursor) == expected


@pytest.mark.parametrize("text, cursor", [
    # inside a string literal: a value, not a column name
    ("class = 'scho", 13, ),
    ("class = 'school' OR class = 'coll", 33),
    ("name = 'O''Br", 13),
    # after the '.' of a struct path: the completer knows top-level columns only
    ("names.", 6),
    ("names.pri", 9),
    ("bbox.xm", 7),
    # inside a quoted name
    ('"my col', 7),
])
def test_no_completion_where_a_bare_column_name_cannot_be_typed(text, cursor):
    cursor = cursor if isinstance(cursor, int) else cursor[0]
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

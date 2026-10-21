"""The attribute filter's lexical gate and how it is ANDed onto the spatial filter.

validate_where_fragment() is not a SQL parser: these cases pin what it catches
(mistakes, and anything that could end the statement early) and that it lets
real conditions through for DuckDB to judge.
"""

import pytest

from gpq_downloader.core.filters import apply_where_fragment, validate_where_fragment


@pytest.mark.parametrize("fragment", [
    "confidence > 0.9",
    "confidence > 0.9 AND class = 'school'",
    "class IN ('school', 'college') OR height IS NULL",
    "(a = 1 OR b = 2) AND c <> 3",
    "\"my column\" = 'x'",
    "name = 'O''Brien'",  # an escaped quote inside a literal
    "\"odd \"\"name\"\"\" = 1",  # an escaped quote inside an identifier
    "name = ')'",  # parentheses inside a literal are not counted
    "\"(\" IS NOT NULL",  # nor inside an identifier
    "names.primary ILIKE '%park%'",
    "list_contains(categories.alternate, 'cafe')",
    "where_col > 1",  # a column that only starts with "where"
    "  height > 10  ",
    "a - b > 1",  # a single minus is not a comment
    "a / 2 > 1",  # nor a single slash
])
def test_valid_fragments_pass(fragment):
    assert validate_where_fragment(fragment) is None


@pytest.mark.parametrize("fragment", ["", "   ", "\t\n", None])
def test_an_empty_fragment_is_no_filter_not_an_error(fragment):
    assert validate_where_fragment(fragment) is None


@pytest.mark.parametrize("fragment, message", [
    ("a = 1; DROP TABLE x", "cannot contain ';'"),
    ("a = 1;", "cannot contain ';'"),
    # rejected inside a literal too: telling the two apart needs DuckDB's lexer
    ("name = 'a;b'", "cannot contain ';'"),
    ("a = 1 -- the rest", "SQL comment"),
    ("a = 1 /* x */", "SQL comment"),
    ("name = '--'", "SQL comment"),
    ("WHERE a = 1", "Leave out the WHERE keyword"),
    ("where a = 1", "Leave out the WHERE keyword"),
    ("name = 'school", "unclosed string"),
    ("name = 'O''Brien", "unclosed string"),
    ('"my column = 1', "unclosed column name"),
    ("(a = 1", "'(' without a matching ')'"),
    ("((a = 1)", "'(' without a matching ')'"),
    ("a = 1)", "')' without a matching '('"),
    # would close the parenthesis apply_where_fragment() opens
    ("1=1) OR (1=1", "')' without a matching '('"),
])
def test_rejected_fragments_say_why(fragment, message):
    error = validate_where_fragment(fragment)
    assert error is not None and message in error


BASE = '"bbox".xmin <= 11.0 AND "bbox".xmax >= 10.0'


@pytest.mark.parametrize("base, fragment, expected", [
    # no filter: the base condition comes back as it was, character for character
    (BASE, None, BASE),
    (BASE, "", BASE),
    (BASE, "   ", BASE),
    ("", None, ""),
    ("", "  ", ""),
    # a filter: both sides parenthesised so an OR cannot bind across the AND
    (BASE, "confidence > 0.9", f"({BASE}) AND (confidence > 0.9)"),
    (BASE, "  confidence > 0.9 \n", f"({BASE}) AND (confidence > 0.9)"),
    (BASE, "a = 1 OR b = 2", f"({BASE}) AND (a = 1 OR b = 2)"),
    ("x\n AND y", "z", "(x\n AND y) AND (z)"),
    # no spatial condition (a WKB BLOB column is filtered after the download)
    ("", "confidence > 0.9", "(confidence > 0.9)"),
])
def test_apply_where_fragment(base, fragment, expected):
    assert apply_where_fragment(base, fragment) == expected

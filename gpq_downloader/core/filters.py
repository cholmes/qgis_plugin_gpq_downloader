"""The attribute filter (#16): a SQL condition the user types, ANDed onto the
spatial filter of every download.

The fragment is interpolated into the WHERE clause as written: it is SQL, and
quoting it with sql_str() or sql_ident() would turn it into a value. That is
the one exception to the rule in sql.py. It is a deliberate one, but not a
free one: people paste filters they found on the internet, and DuckDB
expressions can read local files and reach the network (read_text(),
read_csv('http://...')). So beyond catching the mistakes that would otherwise
surface as a confusing DuckDB error halfway through a download,
validate_where_fragment() also refuses subqueries and the file/settings
functions. That refusal is a tripwire, not a security boundary: it stops a
pasted filter from doing something surprising, not a determined person at
their own keyboard, who can run any SQL in their own DuckDB anyway.

The lexical scan mirrors DuckDB's lexer for everything it lets through:
'' and "" doubling, backslash escapes in E'...' strings. Dollar-quoted
strings ($$...$$) are refused rather than lexed, as are semicolons and
comments even inside literals, so the statement the fragment lands in stays
one statement and the fragment cannot close the parenthesis
apply_where_fragment() opens. Whether the fragment names real columns and
types is DuckDB's call; the dialog asks it before accepting
(ValidationWorker), and the download reports what it says.
"""

import difflib
import re
from typing import NamedTuple, Optional, Tuple

from .schema import field_names, resolve_path, strip_subscripts

# Subquery openers and the functions that read files, settings or the network.
# Matched only outside string literals, so `note = 'select few'` stays legal.
_SUBQUERY_RE = re.compile(r"\b(select|table)\b", re.IGNORECASE)
_FUNCTION_RE = re.compile(
    r"\b(read_\w+|parquet_scan|sniff_csv|glob|duckdb_secrets|current_setting|getenv)"
    r"\s*\(",
    re.IGNORECASE,
)


def _normalise(fragment):
    return fragment.strip() if isinstance(fragment, str) else ""


def _split_code_from_strings(text):
    """Lex `text` the way DuckDB would, returning (code, error).

    `code` is the fragment's text outside string literals and quoted
    identifiers (each literal replaced by a placeholder), for the keyword and
    function checks. `error` is a rejection message, or None: an unclosed
    literal, an unbalanced parenthesis, or a '$' in code position (the start
    of a dollar-quoted string, which this scanner does not lex).
    """
    code = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char == "$":
            return None, (
                "The filter cannot contain '$'. "
                "Use '...' strings, not $$...$$ ones."
            )
        if char == "'":
            # E'...' strings treat backslash as an escape; a plain '...'
            # string does not. The E must not be the tail of a longer word
            # (LIKE'x' is the plain kind).
            is_escape_string = bool(code) and code[-1] in "eE" and (
                len(code) < 2
                or not (code[-2].isalnum() or code[-2] in "_\"\x00")
            )
            i += 1
            while i < n:
                c = text[i]
                if is_escape_string and c == "\\":
                    i += 2
                    continue
                if c == "'":
                    if i + 1 < n and text[i + 1] == "'":  # '' escape
                        i += 2
                        continue
                    break
                i += 1
            if i >= n:
                return None, (
                    "The filter has an unclosed string: "
                    "a ' without its closing '."
                )
            i += 1  # past the closing quote
            code.append("\x00")
            continue
        if char == '"':
            i += 1
            while i < n:
                if text[i] == '"':
                    if i + 1 < n and text[i + 1] == '"':  # "" escape
                        i += 2
                        continue
                    break
                i += 1
            if i >= n:
                return None, (
                    "The filter has an unclosed column name: "
                    'a " without its closing ".'
                )
            i += 1
            code.append("\x00")
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None, "The filter has a ')' without a matching '('."
        code.append(char)
        i += 1
    if depth > 0:
        return None, "The filter has a '(' without a matching ')'."
    return "".join(code), None


def validate_where_fragment(fragment: str) -> "str | None":
    """Return why `fragment` cannot be used as a filter, or None if it can.

    An empty or blank fragment is no filter, so it is not an error either; the
    caller passes None on. This is a lexical check only (see the module note).
    """
    text = _normalise(fragment)
    if not text:
        return None

    # Rejected anywhere, even inside a string literal: a check that guessed
    # wrong about where a literal ends would let a second statement or a
    # statement-swallowing comment through.
    if ";" in text:
        return "The filter cannot contain ';'. Enter a single condition, without a semicolon."
    if "--" in text or "/*" in text:
        return "The filter cannot contain an SQL comment ('--' or '/*')."

    if text.split(None, 1)[0].upper() == "WHERE":
        return "Leave out the WHERE keyword; enter only the condition, such as confidence > 0.9."

    code, error = _split_code_from_strings(text)
    if error:
        return error

    match = _SUBQUERY_RE.search(code)
    if match:
        return (
            f"The filter cannot contain {match.group(1).upper()}: "
            "it can only test the data's own columns."
        )
    match = _FUNCTION_RE.search(code)
    if match:
        return (
            f"The filter cannot call {match.group(1)}(): "
            "it can only read the data's own columns."
        )
    return None


def apply_where_fragment(base_condition: str, fragment: "str | None") -> str:
    """AND the user's fragment onto the spatial condition.

    No fragment (None, empty or blank) returns `base_condition` untouched, so a
    download without a filter issues exactly the SQL it did before the filter
    existed. Otherwise both sides are parenthesised, "(base) AND (fragment)",
    so an OR in either cannot bind across the AND; with no spatial condition
    (a WKB BLOB column is filtered after the download) it is "(fragment)".
    """
    text = _normalise(fragment)
    if not text:
        return base_condition
    if not base_condition:
        return f"({text})"
    return f"({base_condition}) AND ({text})"


# --- the columns a fragment reads (#217) -----------------------------------
#
# The filter box used to be a bare QLineEdit with a placeholder: the only way
# to learn what a dataset's columns were called was to get the download wrong
# and read DuckDB's Binder Error afterwards ("cg = cv" -> Referenced column
# "cg" not found in FROM clause!). The dialog now fetches the column list and
# both completes names from it and names the ones that do not match, which is
# what the three functions below are for. They are all pure: the widgets are
# in gui/dialog.py, the DESCRIBE in qgis_io/worker.py.

# Characters an unquoted DuckDB identifier is made of. A leading digit makes a
# number, not a name, so the first character is checked against the letters.
_IDENT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)
_IDENT_START = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
)

# Words that can stand on their own in a condition without being a column:
# operators, the type names a CAST may use, and INTERVAL's units. Generous on
# purpose, and only ever consulted for unquoted words. A word wrongly listed
# here costs a warning that is not given; a column name wrongly left out of it
# would be reported as unknown, which is the mistake worth avoiding. Words that
# are also ordinary column names in real data (type, date, name, ...) are
# therefore left out: DuckDB only reads them as syntax next to a keyword that
# is in here, and the check needs them to stay checkable.
_NON_COLUMN_WORDS = frozenset(
    word.lower()
    for word in """
    AND OR NOT NULL IS IN LIKE ILIKE SIMILAR TO ESCAPE BETWEEN SYMMETRIC
    TRUE FALSE UNKNOWN CASE WHEN THEN ELSE END CAST TRY_CAST AS DISTINCT
    FROM ALL ANY SOME EXISTS COLLATE INTERVAL GLOB REGEXP DEFAULT
    ASC DESC NULLS WITHIN GROUP ORDER BY FILTER OVER PARTITION ROWS RANGE
    PRECEDING FOLLOWING UNBOUNDED UNION INTERSECT EXCEPT
    BOOLEAN BOOL LOGICAL TINYINT SMALLINT INTEGER INT INT1 INT2 INT4 INT8
    BIGINT HUGEINT UHUGEINT UTINYINT USMALLINT UINTEGER UBIGINT VARINT
    FLOAT FLOAT4 FLOAT8 REAL DOUBLE DECIMAL NUMERIC
    VARCHAR CHAR BPCHAR NVARCHAR BLOB BYTEA BINARY VARBINARY UUID JSON
    TIMESTAMP TIMESTAMPTZ DATETIME TIMETZ BIT BITSTRING ENUM GEOMETRY
    """.split()
)

# Words a column has to be quoted to be named by: the ones a condition reads
# as syntax, plus the three validate_where_fragment() refuses outright, which
# a bare completion would walk the user straight into.
_QUOTE_THESE = _NON_COLUMN_WORDS | {"select", "table", "where"}

# How alike a column name has to be to be offered as "did you mean".
# difflib's own 0.6 is right for a name long enough to resemble anything
# ("clas" scores 0.89 against "class", "geometrie" 0.82 against "geometry",
# while "hieght" reaches only 0.36 against the nearest column and rightly
# gets no guess). It is far too strict for the short names that started this,
# though: "cg" scores 0.29 against "class", its nearest by a clear margin. So
# a short name gets a loose cutoff, where a bad guess costs little - the
# message already says plainly that the column does not exist - and a long
# one gets the strict cutoff, where silence is better than noise.
_SHORT_NAME = 3
_SHORT_NAME_CUTOFF = 0.25
_SUGGESTION_CUTOFF = 0.6


def _suggest_column(name, columns):
    """The column `name` was most likely meant to be, or None.

    A column that starts with the name comes first, and the shortest of those
    wins: an abbreviation ("conf" for "confidence") is the common case and
    scores too low for difflib to offer. Otherwise it is difflib's nearest
    match, if it clears the cutoff for a name of this length.
    """
    folded = name.lower()
    prefixed = sorted(
        (column for column in columns if str(column).lower().startswith(folded)),
        key=lambda column: (len(str(column)), str(column)),
    )
    if prefixed:
        return str(prefixed[0])
    cutoff = _SHORT_NAME_CUTOFF if len(folded) <= _SHORT_NAME else _SUGGESTION_CUTOFF
    by_name = {str(column).lower(): str(column) for column in columns}
    matches = difflib.get_close_matches(folded, sorted(by_name), n=1, cutoff=cutoff)
    return by_name[matches[0]] if matches else None


def _is_escape_string_quote(text, index):
    """Is the ' at text[index] the opening quote of an E'...' string?

    E'' treats backslash as an escape and a plain '...' does not, so the two
    end in different places. The E must be a word of its own, not the tail of
    a longer one (LIKE'x' is the plain kind).
    """
    if index == 0 or text[index - 1] not in "eE":
        return False
    return index < 2 or text[index - 2] not in _IDENT_CHARS


def _skip_literal(text, index):
    """The index just past the '...' literal opening at text[index].

    Returns len(text) for an unclosed literal, so a half-typed fragment is
    scanned as far as it goes. This is the forgiving twin of
    _split_code_from_strings(), which refuses what it cannot lex.
    """
    escapes = _is_escape_string_quote(text, index)
    i = index + 1
    n = len(text)
    while i < n:
        char = text[i]
        if escapes and char == "\\":
            i += 2
            continue
        if char == "'":
            if i + 1 < n and text[i + 1] == "'":  # '' escape
                i += 2
                continue
            return i + 1
        i += 1
    return n


def _read_quoted_name(text, index):
    """("the name", index just past it) for the "..." opening at text[index]."""
    chars = []
    i = index + 1
    n = len(text)
    while i < n:
        if text[i] == '"':
            if i + 1 < n and text[i + 1] == '"':  # "" escape
                chars.append('"')
                i += 2
                continue
            return "".join(chars), i + 1
        chars.append(text[i])
        i += 1
    return "".join(chars), n


def _skip_number(text, index):
    """The index just past the number starting at text[index].

    Read as one token so that neither 0.9's point nor 1e5's e is mistaken for
    a struct path or a column name.
    """
    i = index
    n = len(text)
    while i < n and (text[i].isdigit() or text[i] == "."):
        i += 1
    if i < n and text[i] in "eE":
        j = i + 1
        if j < n and text[j] in "+-":
            j += 1
        if j < n and text[j].isdigit():
            i = j
            while i < n and text[i].isdigit():
                i += 1
    return i


def _scan_names(text):
    """Yield (name, quoted, start, end) for every name-shaped word in `text`.

    String literals and numbers are stepped over whole; everything else that
    looks like an identifier comes out, keywords included, for the caller to
    sort out. Deliberately forgiving: it runs on text the user is still
    typing.
    """
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char == "'":
            i = _skip_literal(text, i)
        elif char == '"':
            name, end = _read_quoted_name(text, i)
            yield name, True, i, end
            i = end
        elif char.isdigit():
            i = _skip_number(text, i)
        elif char in _IDENT_START:
            start = i
            while i < n and text[i] in _IDENT_CHARS:
                i += 1
            yield text[start:i], False, start, i
            i = max(i, start + 1)
        else:
            i += 1


def _preceded_by_dot(text, start):
    """Is the name at `start` a struct field, like the primary of names.primary?

    A '.' after a digit belongs to that number, not to a path.
    """
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0 or text[i] != ".":
        return False
    j = i - 1
    while j >= 0 and text[j].isspace():
        j -= 1
    return j < 0 or not text[j].isdigit()


def _followed_by(text, end, chars):
    """Is the next non-blank character after `end` one of `chars`?"""
    i = end
    while i < len(text) and text[i].isspace():
        i += 1
    return i < len(text) and text[i] in chars


# What tells a value apart from a column name: the thing in front of it. The
# operators cover `= "Jack Park"` and its relatives; LIKE and ILIKE are the
# two keywords that take a string on the right and nothing else.
_COMPARISON_CHARS = frozenset("=<>!")
_COMPARISON_WORDS = frozenset({"like", "ilike"})


def _preceded_by_comparison(text, start):
    """Does a comparison sit immediately in front of the name at `start`?

    True where a *value* belongs, which is what makes a double-quoted token
    there a mistake rather than an oddly named column.
    """
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0:
        return False
    if text[i] in _COMPARISON_CHARS:
        return True
    end = i + 1
    while i >= 0 and text[i] in _IDENT_CHARS:
        i -= 1
    return text[i + 1:end].lower() in _COMPARISON_WORDS


# A list subscript written after a path part, so that sources[1].property is
# read as the two parts DuckDB reads it as. A quoted MAP key
# (names.common['en']) is not one: its key is nowhere in the schema.
_SUBSCRIPT_AT = re.compile(r"\[\s*\d*\s*\]")


def _read_subscripts(text, end):
    """("[1]", index past it) for the subscripts written after `end`."""
    taken = []
    i = end
    while True:
        j = i
        while j < len(text) and text[j].isspace():
            j += 1
        match = _SUBSCRIPT_AT.match(text, j)
        if match is None:
            return "".join(taken), i
        taken.append("".join(match.group(0).split()))
        i = match.end()


def _subscripts_before(text, end):
    """("[1]", index of its first '[') for the subscripts ending at `end`."""
    taken = []
    i = end
    while True:
        j = i - 1
        while j >= 0 and text[j].isspace():
            j -= 1
        if j < 0 or text[j] != "]":
            return "".join(reversed(taken)), i
        opening = text.rfind("[", 0, j)
        if opening < 0:
            return "".join(reversed(taken)), i
        inner = text[opening + 1:j].strip()
        if inner and not inner.isdigit():
            return "".join(reversed(taken)), i
        taken.append(f"[{inner}]")
        i = opening


def _path_before(text, start):
    """The dotted parts in front of the name starting at `start`.

    ("names",) for the pri of names.pri, ("names", "rules") for
    names.rules.var, and () where no dot precedes it. A '.' that belongs to a
    number ends the walk rather than reading the digits as a name.
    """
    parts = []
    i = start
    while True:
        j = i - 1
        while j >= 0 and text[j].isspace():
            j -= 1
        if j < 0 or text[j] != ".":
            break
        suffix, j = _subscripts_before(text, j)
        k = j - 1
        while k >= 0 and text[k].isspace():
            k -= 1
        if k < 0:
            break
        if text[k] == '"':
            opening = text.rfind('"', 0, k)
            if opening < 0:
                break
            parts.append(text[opening + 1:k] + suffix)
            i = opening
            continue
        if text[k] not in _IDENT_CHARS:
            break
        end = k + 1
        while k >= 0 and text[k] in _IDENT_CHARS:
            k -= 1
        part = text[k + 1:end]
        if not part or part[0] not in _IDENT_START:
            break  # a number, not a name
        parts.append(part + suffix)
        i = k + 1
    parts.reverse()
    return tuple(parts)


class Reference(NamedTuple):
    """One thing `fragment` reads as a column, with the context that judges it.

    `path` is the dotted parts as typed, ("names", "primary"). `quoted` says
    the first part was written in double quotes, which in SQL is an
    identifier and not a string. `after_comparison` says an operator sits
    immediately in front of it, so a value belongs there.
    """

    path: Tuple[str, ...]
    quoted: bool = False
    after_comparison: bool = False


def identifier_paths_in(fragment: str) -> list:
    """The column paths `fragment` reads, each once, in order, as References.

    Left out: string literals, numbers, SQL keywords and type names, and the
    word in front of a '(' (a function) or a quote (the E of E'...'). A
    dotted path comes out whole, so names.primary is one reference of two
    parts rather than a column and a mystery. Unquoted names fold the way
    DuckDB's do, so a path repeated in another case is one path, under the
    spelling it first had.
    """
    text = _normalise(fragment)
    refs = []
    seen = set()
    chain = None

    def flush():
        nonlocal chain
        if chain is None:
            return
        key = tuple(part.lower() for part in chain.path)
        if key not in seen:
            seen.add(key)
            refs.append(chain)
        chain = None

    for name, quoted, start, end in _scan_names(text):
        # sources[1].property is two parts, with the index on the first
        suffix, _after = _read_subscripts(text, end)
        if _preceded_by_dot(text, start):
            if chain is not None:  # a field of the path being built
                chain = chain._replace(path=chain.path + (name + suffix,))
            continue
        flush()
        if _followed_by(text, end, "('"):
            continue  # a function call, or the E of an E'...' string
        if not quoted and name.lower() in _NON_COLUMN_WORDS:
            continue
        chain = Reference(
            (name + suffix,), quoted, _preceded_by_comparison(text, start)
        )
    flush()
    return refs


def identifiers_in(fragment: str) -> list:
    """The root column names `fragment` reads, in order, each one once.

    names.primary reads names; identifier_paths_in() has the whole path.
    """
    names = []
    seen = set()
    for ref in identifier_paths_in(fragment):
        name, _indexed = strip_subscripts(ref.path[0])
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


def _column_names(columns):
    """The top-level names of a column list, which may be names or rows."""
    return [
        str(column) if isinstance(column, str) else str(column[0])
        for column in columns or ()
    ]


def unknown_identifiers(fragment: str, columns) -> list:
    """[(path as typed, closest path or None)] for what `columns` has not got.

    `columns` is either plain names or (name, type) rows as DESCRIBE returns
    them; with only names a dotted path cannot be judged and is left alone.
    An empty `columns` means the column list has not arrived, so nothing is
    unknown: a guess made without the schema would be worse than the silence
    the filter box had before.
    """
    names = _column_names(columns)
    if not names:
        return []
    unknown = []
    for ref in identifier_paths_in(fragment):
        resolved = resolve_path(columns, ref.path)
        if resolved.outcome == "unknown_column":
            unknown.append((resolved.missing, _suggest_column(resolved.missing, names)))
        elif resolved.outcome == "unknown_field":
            suggestion = _suggest_column(
                resolved.missing, field_names(resolved.parent.text)
            )
            parent = ".".join(ref.path[: resolved.index])
            unknown.append((
                f"{parent}.{resolved.missing}",
                f"{parent}.{suggestion}" if suggestion else None,
            ))
    return unknown


def _quote_trap_message(fragment, columns):
    """The double-quoted value trap, or None.

    `names.primary = "Jack Park"` is the report that asked for this: double
    quotes are an identifier in SQL and single quotes a string, so DuckDB
    answers a typed value with `Referenced column "Jack Park" not found`.
    Telling someone that their value is not a column is accurate and
    useless, so where a quoted name the data has not got sits exactly where a
    value belongs, say what to type instead.
    """
    names = {name.lower() for name in _column_names(columns)}
    if not names:
        return None
    for ref in identifier_paths_in(fragment):
        if not (ref.quoted and ref.after_comparison and len(ref.path) == 1):
            continue
        if ref.path[0].lower() in names:
            continue
        literal = ref.path[0].replace("\'", "\'\'")
        return (
            f'"{ref.path[0]}" is not a column. '
            f"For a text value use single quotes: '{literal}'"
        )
    return None


def _list_message(fragment, columns):
    """Why a dot will not follow a list, and what to type instead, or None.

    Dot access into a STRUCT(...)[] is a binder error (Cannot extract field
    ... because it is not a struct), which is a dead end until someone
    mentions the index.
    """
    if not _column_names(columns):
        return None
    for ref in identifier_paths_in(fragment):
        resolved = resolve_path(columns, ref.path)
        if resolved.outcome != "list" or not resolved.list_path:
            continue
        depth = len(resolved.list_path)
        if len(ref.path) <= depth:
            continue  # the list itself, with no dot after it: nothing wrong
        head = ".".join(ref.path[:depth])
        tail = ".".join(ref.path[depth:])
        return f'"{head}" is a list - use {head}[1].{tail}, not {head}.{tail}.'
    return None


def unknown_column_message(fragment: str, columns) -> Optional[str]:
    """Why `fragment` names something this data has not got, or None.

    One line, and only the first thing wrong: the point is to replace a
    DuckDB Binder Error nobody reads with something short under the field,
    and the next problem is reported once this one is fixed. `columns` is
    plain names or (name, type) rows; the dotted-path checks need the types.
    """
    trap = _quote_trap_message(fragment, columns)
    if trap:
        return trap
    unknown = unknown_identifiers(fragment, columns)
    if unknown:
        name, suggestion = unknown[0]
        kind = "field" if "." in name else "column"
        if suggestion:
            return f'Unknown {kind} "{name}" - did you mean "{suggestion}"?'
        if kind == "field":
            parent = name.rsplit(".", 1)[0]
            return f'Unknown field "{name}" - "{parent}" has no field by that name.'
        return f'Unknown column "{name}" - the data has no column by that name.'
    return _list_message(fragment, columns)


class Word(NamedTuple):
    """Where the completer should work: the word, and the struct above it.

    `start` is where `text` begins in the line, so a completion replaces just
    that word. `path` is the dotted prefix in front of it, so names.pri
    completes against the fields of names rather than against the columns.
    """

    start: int
    text: str
    path: Tuple[str, ...] = ()


def current_word(text: str, cursor: int) -> Optional[Word]:
    """The column name or struct field being typed at `cursor` in `text`.

    None where neither can be typed at all - inside a string literal, inside
    a quoted name, or in the middle of a number - so the completer can stay
    out of the way. The word may be empty: at a cursor just after AND, or
    just after `names.`, there is nothing typed yet but everything to offer.

    A QCompleter attached to a QLineEdit completes the whole line, which is no
    use past the first operator; the dialog feeds it this word instead.
    """
    cursor = max(0, min(cursor, len(text)))
    before = text[:cursor]
    i = 0
    while i < cursor:
        char = before[i]
        if char == "'":
            end = _skip_literal(before, i)
            if end >= cursor:
                return None  # the cursor is inside the literal
            i = end
        elif char == '"':
            _, end = _read_quoted_name(before, i)
            if end >= cursor:
                return None  # the cursor is inside the quoted name
            i = end
        else:
            i += 1
    start = cursor
    while start > 0 and before[start - 1] in _IDENT_CHARS:
        start -= 1
    if before[start:].lstrip("0123456789") != before[start:]:
        return None  # the cursor sits in a number, not a name
    return Word(start, before[start:], _path_before(before, start))


def completion_text(name: str) -> str:
    """`name` as it should be typed into a filter: quoted only where it must be.

    A column called "my column", or one spelled like a keyword, has to be
    quoted to be read as a name at all, and that is exactly the case where a
    user would not think to do it. Anything plain is inserted plain, because
    "class" everywhere would read as noise.
    """
    text = str(name)
    plain = (
        bool(text)
        and text[0] in _IDENT_START
        and all(char in _IDENT_CHARS for char in text)
        and text.lower() not in _QUOTE_THESE
    )
    return text if plain else '"' + text.replace('"', '""') + '"'


def completion_candidates(schema) -> list:
    """[(column name, what the completer's popup shows)] for a DESCRIBE result.

    Each row is a (name, type, ...) tuple as DuckDB's DESCRIBE returns it. The
    popup shows the type next to the name - the thing a filter most often
    needs to know - while the name alone is what gets inserted.
    """
    candidates = []
    for row in schema:
        name = str(row[0])
        column_type = row[1] if len(row) > 1 else None
        label = f"{name}  {column_type}" if column_type else name
        candidates.append((name, label))
    return candidates

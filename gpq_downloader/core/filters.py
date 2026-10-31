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


def identifiers_in(fragment: str) -> list:
    """The column names `fragment` reads, in order, each one once.

    Left out: string literals, numbers, SQL keywords and type names, the word
    in front of a '(' (a function) or a quote (the E of E'...'), and every
    part of a dotted path after the first (names.primary reads names).
    Unquoted names fold the way DuckDB's do, so a name repeated in another
    case is one name, under the spelling it first had.
    """
    text = _normalise(fragment)
    names = []
    seen = set()
    for name, quoted, start, end in _scan_names(text):
        if _preceded_by_dot(text, start):
            continue  # a struct field, not a column
        if _followed_by(text, end, "('"):
            continue  # a function call, or the E of an E'...' string
        if not quoted and name.lower() in _NON_COLUMN_WORDS:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def unknown_identifiers(fragment: str, columns) -> list:
    """[(name, closest column or None)] for the names `columns` does not have.

    An empty `columns` means the column list has not arrived (or could not be
    read), so nothing is unknown: a guess made without the schema would be
    worse than the silence the filter box had before.
    """
    names = list(columns)
    known = {str(column).lower() for column in names}
    if not known:
        return []
    return [
        (name, _suggest_column(name, names))
        for name in identifiers_in(fragment)
        if name.lower() not in known
    ]


def unknown_column_message(fragment: str, columns) -> "str | None":
    """Why `fragment` names a column this data has not got, or None.

    Only the first such name is reported: the point is to replace a DuckDB
    Binder Error nobody reads with one short line under the field, and the
    next name is reported once this one is fixed.
    """
    unknown = unknown_identifiers(fragment, columns)
    if not unknown:
        return None
    name, suggestion = unknown[0]
    if suggestion:
        return f'Unknown column "{name}" - did you mean "{suggestion}"?'
    return f'Unknown column "{name}" - the data has no column by that name.'


def current_word(text: str, cursor: int) -> "tuple | None":
    """(start, word) of the bare column name being typed at `cursor` in `text`.

    None where a column name cannot be typed at all - inside a string
    literal, inside a quoted name, or after the '.' of a struct path - so the
    completer can stay out of the way. The word may be empty: at a cursor
    just after AND there is nothing typed yet but everything to offer.

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
    if _preceded_by_dot(before, start):
        return None
    return start, before[start:]


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

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

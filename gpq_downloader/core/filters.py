"""The attribute filter (#16): a SQL condition the user types, ANDed onto the
spatial filter of every download.

The fragment is interpolated into the WHERE clause as written: it is SQL, and
quoting it with sql_str() or sql_ident() would turn it into a value. That is
the one exception to the rule in sql.py, and it is not a hole in it: the user
typed this SQL, and it runs in their own local DuckDB over data they chose,
so there is nothing here it could reach that they cannot reach already.

So validate_where_fragment() is not a security boundary. It catches the
mistakes that would otherwise surface as a confusing DuckDB error halfway
through a download, and it keeps the statement the fragment lands in one
statement: DuckDB's execute() runs every statement in a string, and a comment
would swallow the closing parenthesis and the rest of the query. Whether the
fragment names real columns and types is DuckDB's call; the dialog asks it
before accepting (ValidationWorker), and the download reports what it says.
"""


def _normalise(fragment):
    return fragment.strip() if isinstance(fragment, str) else ""


def validate_where_fragment(fragment: str) -> "str | None":
    """Return why `fragment` cannot be used as a filter, or None if it can.

    An empty or blank fragment is no filter, so it is not an error either; the
    caller passes None on. This is a lexical check only (see the module note).
    """
    text = _normalise(fragment)
    if not text:
        return None

    # Rejected anywhere, even inside a string literal: telling a literal from
    # code needs DuckDB's lexer (E'' escapes, $$ quoting), and a check that
    # guessed wrong would let a second statement through.
    if ";" in text:
        return "The filter cannot contain ';'. Enter a single condition, without a semicolon."
    if "--" in text or "/*" in text:
        return "The filter cannot contain an SQL comment ('--' or '/*')."

    if text.split(None, 1)[0].upper() == "WHERE":
        return "Leave out the WHERE keyword; enter only the condition, such as confidence > 0.9."

    # '' and "" inside a literal or identifier are escapes, and pair up like
    # the delimiters, so tracking which one is open is enough.
    quote = None
    depth = 0
    for char in text:
        if quote is not None:
            if char == quote:
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return "The filter has a ')' without a matching '('."
    if quote == "'":
        return "The filter has an unclosed string: a ' without its closing '."
    if quote == '"':
        return 'The filter has an unclosed column name: a " without its closing ".'
    if depth > 0:
        return "The filter has a '(' without a matching ')'."
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

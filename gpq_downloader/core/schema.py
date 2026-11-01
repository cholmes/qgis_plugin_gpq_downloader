"""Reading the types DuckDB's DESCRIBE prints, so the filter box can offer the
fields of a struct (#217).

Overture's columns are deeply nested, and the type string is the only place
their shape is written down:

    names  STRUCT("primary" VARCHAR, common MAP(VARCHAR, VARCHAR), rules
           STRUCT(variant VARCHAR, ..., "between" DOUBLE[], side VARCHAR)[])

So this is a small recursive-descent parser rather than a split on commas,
which the nesting alone would defeat. It has to cope with reserved words
quoted inside the type ("primary", "between", "version"), [] and [][]
suffixes, MAP(K, V), and GEOMETRY('OGC:CRS84') carrying a quoted literal.

What a path may be followed through was probed against DuckDB 1.5.3 rather
than assumed, because the rules are not uniform:

    names.primary        OK     a reserved word needs no quotes here
    names."primary"      OK
    bbox.xmin            OK
    names.common.en      OK     a MAP is reached by a dot, but its keys are
                                nowhere in the schema, so nothing can be
                                offered or checked past one
    sources.property     FAILS  Cannot extract field 'property' from
                                expression "sources" because it is not a
                                struct - sources is STRUCT(...)[]
    sources[1].property  OK
    names.rules.variant  FAILS  same, one level down

A list of structs therefore offers nothing after a bare dot: completing
`sources.` to a field would complete to a binder error, and silently
completing it to `sources[1].property` would change what the filter means -
element one is not "any element". resolve_path() reports the list instead, so
the dialog can say what to type. Once the index *is* written, a path is
followed as normal: the parts carry their subscripts as typed
("sources[1]", "property"), and _strip_subscripts() takes one list level off
for each.

Nothing here raises. These strings come off a remote file, so a type this
parser cannot read costs the completer its suggestions, not the dialog its
event loop.
"""

import re

STRUCT = "STRUCT"
MAP = "MAP"

# A list subscript written after a path part: sources[1], hierarchies[1][2],
# or the bare sources[]. A quoted MAP key (names.common['en']) deliberately
# does not match: a map's keys are not in the schema to judge.
_SUBSCRIPT_RE = re.compile(r"\[\s*\d*\s*\]\s*$")

# Longer than this and a popup row is wider than the dialog; names' own type
# runs to about 300 characters.
_LABEL_TYPE_LIMIT = 44

# Characters that end a word in a type string. Everything else is a word,
# which keeps DECIMAL(18,3)'s numbers and OGC:CRS84's colon in one piece.
_BREAK = set(" \t\r\n(),[]\"'")


class ColumnType:
    """A parsed DuckDB type: its base name, its struct fields, its list depth.

    `text` is the type as DuckDB printed it, kept for the popup. `fields` is
    ((name, ColumnType), ...) for a STRUCT and () for everything else; the
    names come out as the data spells them, without the quotes the type
    string puts round a reserved word.
    """

    __slots__ = ("base", "fields", "list_depth", "text")

    def __init__(self, base="", fields=(), list_depth=0, text=""):
        self.base = base
        self.fields = tuple(fields)
        self.list_depth = list_depth
        self.text = text

    @property
    def is_struct(self):
        """A plain struct: a dot may be followed into it."""
        return self.base == STRUCT and self.list_depth == 0

    @property
    def is_list(self):
        return self.list_depth > 0

    def field(self, name):
        """The named field's type, matched the way DuckDB matches one, or None."""
        folded = str(name).lower()
        for field_name, field_type in self.fields:
            if field_name.lower() == folded:
                return field_type
        return None

    def __repr__(self):
        return f"ColumnType({self.base!r}, depth={self.list_depth})"


class ResolvedPath:
    """What a dotted path lands on, and the first thing wrong with it.

    `outcome` is one of:
      "struct"         a plain STRUCT: its fields can be offered and checked
      "scalar"         a value; there is nothing below it
      "list"           a LIST, so the dot before `missing` is a binder error;
                       `list_path` is the path of the list itself
      "unknowable"     nothing can be said - no schema yet, a MAP (whose keys
                       are not in the schema), or a type this parser could
                       not read
      "unknown_column" the first part is not a column; `missing` names it
      "unknown_field"  a part is not a field of the struct before it
    """

    __slots__ = ("outcome", "type", "missing", "list_path", "parent", "index")

    def __init__(
        self, outcome, type_=None, missing=None, list_path=None, parent=None, index=None
    ):
        self.outcome = outcome
        self.type = type_ if type_ is not None else ColumnType()
        self.missing = missing
        self.list_path = list_path
        self.parent = parent  # the ColumnType the missing name was looked for in
        self.index = index  # where in the path the trouble is

    def __repr__(self):
        return f"ResolvedPath({self.outcome!r}, missing={self.missing!r})"


def _tokenise(text):
    """[(kind, value, start, end)] for a type string.

    kind is one of "word", "name" (a "quoted" one), "literal" (a 'quoted'
    one) or the punctuation character itself.
    """
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if char in "(),[]":
            tokens.append((char, char, i, i + 1))
            i += 1
            continue
        if char in "\"'":
            closing = char
            kind = "name" if char == '"' else "literal"
            start = i
            i += 1
            chars = []
            while i < n:
                if text[i] == closing:
                    if i + 1 < n and text[i + 1] == closing:  # "" or '' escape
                        chars.append(closing)
                        i += 2
                        continue
                    i += 1
                    break
                chars.append(text[i])
                i += 1
            tokens.append((kind, "".join(chars), start, i))
            continue
        start = i
        while i < n and text[i] not in _BREAK:
            i += 1
        tokens.append(("word", text[start:i], start, i))
    return tokens


def _skip_group(tokens, i):
    """The index past the balanced ( ... ) opening at tokens[i]."""
    depth = 0
    while i < len(tokens):
        kind = tokens[i][0]
        if kind == "(":
            depth += 1
        elif kind == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _parse(text, tokens, i):
    """(ColumnType, next index) for the type starting at tokens[i]."""
    if i >= len(tokens) or tokens[i][0] not in ("word", "name"):
        return ColumnType(), i
    start_token = i
    base = tokens[i][1]
    base = base.upper() if tokens[i][0] == "word" else base
    i += 1
    fields = ()
    if i < len(tokens) and tokens[i][0] == "(":
        if base == STRUCT:
            fields, i = _parse_fields(text, tokens, i + 1)
        else:
            i = _skip_group(tokens, i)
    depth = 0
    while i < len(tokens) and tokens[i][0] == "[":
        # "[]" or a fixed size, "[4]"
        j = i + 1
        if j < len(tokens) and tokens[j][0] == "word":
            j += 1
        if j < len(tokens) and tokens[j][0] == "]":
            depth += 1
            i = j + 1
            continue
        break
    end = tokens[i - 1][3] if i > start_token else tokens[start_token][2]
    return ColumnType(base, fields, depth, text[tokens[start_token][2]:end]), i


def _parse_fields(text, tokens, i):
    """((name, ColumnType), ...) for a STRUCT's field list, past the '('."""
    fields = []
    while i < len(tokens) and tokens[i][0] != ")":
        if tokens[i][0] == ",":
            i += 1
            continue
        if tokens[i][0] not in ("word", "name"):
            i += 1
            continue
        name = tokens[i][1]
        i += 1
        field_type, next_i = _parse(text, tokens, i)
        if next_i == i:  # a name with no type after it: half a string
            i += 1
        else:
            i = next_i
        fields.append((name, field_type))
    if i < len(tokens) and tokens[i][0] == ")":
        i += 1
    return tuple(fields), i


def parse_column_type(text) -> ColumnType:
    """The type DuckDB printed, read into a ColumnType. Never raises."""
    if not isinstance(text, str) or not text.strip():
        return ColumnType()
    try:
        tokens = _tokenise(text)
        parsed, _ = _parse(text, tokens, 0)
        return parsed
    except Exception:  # a type string this parser did not expect
        return ColumnType(text=text)


def field_names(text) -> list:
    """The field names of a STRUCT type string, in order, or []."""
    return [name for name, _type in parse_column_type(text).fields]


def strip_subscripts(part):
    """("sources", 1) for "sources[1]"; ("names", 0) for "names".

    The count is how many list levels the subscripts take off, so a path
    written the way DuckDB wants it can be followed through a list.
    """
    text = str(part)
    taken = 0
    while True:
        match = _SUBSCRIPT_RE.search(text)
        if not match:
            return text.strip(), taken
        taken += 1
        text = text[: match.start()]


def _columns(schema):
    """[(name, ColumnType)] for a DESCRIBE result or a (name, type) list."""
    columns = []
    for row in schema or ():
        if isinstance(row, str):
            columns.append((row, ColumnType()))
            continue
        name = str(row[0])
        text = row[1] if len(row) > 1 else ""
        columns.append((name, parse_column_type(text)))
    return columns


def resolve_path(schema, path) -> ResolvedPath:
    """Follow `path` through `schema`; see ResolvedPath for what comes back.

    An empty schema resolves to "unknowable": until the DESCRIBE has come
    back there is nothing to check a name against, and a guess made without
    it would be worse than the silence the filter box had before.
    """
    columns = _columns(schema)
    if not columns or not path:
        return ResolvedPath("unknowable")
    by_name = {name.lower(): (name, type_) for name, type_ in columns}
    name, indexed = strip_subscripts(path[0])
    first = by_name.get(name.lower())
    if first is None:
        return ResolvedPath("unknown_column", missing=name, index=0)
    current = _indexed(first[1], indexed)
    walked = [str(path[0])]
    for position, part in enumerate(path[1:], start=1):
        if current.is_list:
            return ResolvedPath(
                "list", current, list_path=tuple(walked), index=position
            )
        if current.base == MAP:
            # The keys are not in the schema, so neither this part nor
            # anything under it can be judged
            return ResolvedPath("unknowable", current)
        if not current.is_struct:
            return ResolvedPath("unknowable", current)
        name, indexed = strip_subscripts(part)
        field = current.field(name)
        if field is None:
            return ResolvedPath(
                "unknown_field", current, missing=name, parent=current, index=position
            )
        walked.append(str(part))
        current = _indexed(field, indexed)
    if current.is_list:
        return ResolvedPath(
            "list", current, list_path=tuple(walked), index=len(path)
        )
    if current.base == MAP:
        return ResolvedPath("unknowable", current)
    if current.is_struct:
        return ResolvedPath("struct", current)
    if not current.base:
        return ResolvedPath("unknowable", current)
    return ResolvedPath("scalar", current)


def _indexed(column_type, times):
    """`column_type` with `times` list levels taken off by subscripts."""
    if not times or not column_type.list_depth:
        return column_type
    depth = max(0, column_type.list_depth - times)
    return ColumnType(column_type.base, column_type.fields, depth, column_type.text)


def type_label(text) -> str:
    """A type string short enough to sit beside a name in a popup row."""
    text = " ".join(str(text or "").split())
    if len(text) > _LABEL_TYPE_LIMIT:
        return text[: _LABEL_TYPE_LIMIT - 3] + "..."
    return text


def field_candidates(schema, path) -> list:
    """[(field name, what the popup shows)] for the fields of `path`, or [].

    Empty unless `path` lands on a plain STRUCT: a LIST cannot be followed by
    a dot at all, and a MAP's keys are not in the schema to offer. Names come
    out unquoted, which is what DuckDB accepts even for a reserved word
    (names.primary binds); the quoted form stays legal if one is ever
    ambiguous.
    """
    resolved = resolve_path(schema, path)
    if resolved.outcome != "struct":
        return []
    return [
        (name, f"{name}  {type_label(field_type.text)}".rstrip())
        for name, field_type in resolved.type.fields
    ]

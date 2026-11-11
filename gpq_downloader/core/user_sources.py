"""The user-defined sources config: its shape, sanitization, and merge with
the bundled presets.

A hand-edited or future-version `user_sources.json` can contain anything, so
sanitize_config() is the single trust boundary between that file and the rest
of the plugin (#84): it never raises, and it always returns the shape in the
plan's Design section, with unknown or malformed pieces dropped rather than
tripping up the dialog. parse_config()/serialize_config() are the file-format
edges around it; apply_hidden() is the read-side merge with the bundled
presets dict (core/presets.py). The GUI layer owns writing the config file
and reading it at dialog-construction time — this module only deals in dicts.
"""

import json
import re

# Sources whose radio can be hidden entirely. Custom URL is never in this
# list: it is the escape hatch and the fallback for a stale radio selection.
# Source Cooperative is no longer a built-in - it is seeded into
# custom_sources on first run (source_cooperative_group(), below) and hidden
# or renamed the same way any other custom group is, so a legacy
# "source_cooperative" entry here is simply dropped by sanitize_config().
HIDEABLE_SOURCES = ("overture", "openstreetmap")

# Sources whose individual datasets can be hidden. For Overture the hideable
# unit is each of its seven top-level presets: hiding "base" or "divisions"
# takes their subtype rows with them, and hiding "buildings" takes its
# modifiers, which are only reachable through it (dialog.py's Overture page).
# Source Cooperative is no longer a built-in (see HIDEABLE_SOURCES above); a
# legacy "source_cooperative" entry here is dropped the same way.
DATASET_HIDEABLE_SOURCES = ("overture", "openstreetmap")


def default_config():
    """A fresh, empty config — the starting point when no file exists yet."""
    return {
        "version": 1,
        "hidden_sources": [],
        "hidden_datasets": {},
        "custom_sources": [],
    }


def source_cooperative_group(presets):
    """The seeded "Source Cooperative" custom group, built from the
    bundled presets' source_cooperative section (missing -> empty datasets).

    Used to seed a first-run user config (gui/user_sources_store.py) so
    Source Cooperative starts life as an ordinary, fully editable custom
    group rather than a hardcoded built-in.
    """
    datasets = []
    for key, entry in presets.get("source_cooperative", {}).items():
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url:
            continue
        dataset = {
            "name": entry.get("display_name") or key.title(),
            "url": url,
            "info_url": entry.get("info_url", ""),
            "group": "",
        }
        # A bundled preset that does not need validation already guarantees
        # a standard "bbox" covering (Task B / #84 follow-up); carrying that
        # forward as a stored validation result lets a download of it skip
        # the pre-validation probe entirely, the same as any dataset a user
        # has already had probed through the Data Sources manager.
        if entry.get("needs_validation") is False:
            dataset["validation"] = {
                "has_bbox": True,
                "bbox_column": "bbox",
                "geometry_column": "geometry",
            }
        datasets.append(dataset)
    return {
        "name": "Source Cooperative",
        "info_url": "https://source.coop",
        "datasets": datasets,
    }


def _clean_str(value):
    """A stripped string, or "" for anything that isn't a string."""
    return value.strip() if isinstance(value, str) else ""


def _sanitize_hidden_sources(raw):
    hidden_sources = raw.get("hidden_sources")
    if not isinstance(hidden_sources, list):
        return []
    seen = set()
    result = []
    for entry in hidden_sources:
        if isinstance(entry, str) and entry in HIDEABLE_SOURCES and entry not in seen:
            seen.add(entry)
            result.append(entry)
    return result


def _sanitize_hidden_datasets(raw):
    hidden_datasets = raw.get("hidden_datasets")
    if not isinstance(hidden_datasets, dict):
        return {}
    result = {}
    for source, keys in hidden_datasets.items():
        if source not in DATASET_HIDEABLE_SOURCES or not isinstance(keys, list):
            continue
        seen = set()
        cleaned = []
        for key in keys:
            name = _clean_str(key)
            if name and name not in seen:
                seen.add(name)
                cleaned.append(name)
        if cleaned:
            result[source] = cleaned
    return result


def _sanitize_dataset_validation(raw):
    """The normalized "validation" dict for a dataset, or None when `raw`
    is not a dict - a dataset with no usable validation simply has no
    "validation" key at all, which stays a legal (unvalidated) dataset."""
    if not isinstance(raw, dict):
        return None
    geometry_column = _clean_str(raw.get("geometry_column")) or "geometry"
    bbox_column = _clean_str(raw.get("bbox_column")) or None
    return {
        "has_bbox": bool(raw.get("has_bbox")),
        "bbox_column": bbox_column,
        "geometry_column": geometry_column,
    }


def _sanitize_dataset(raw):
    """The cleaned dataset dict, or None if it has no usable name/url."""
    if not isinstance(raw, dict):
        return None
    name = _clean_str(raw.get("name"))
    url = _clean_str(raw.get("url"))
    if not name or not url:
        return None
    dataset = {
        "name": name,
        "url": url,
        "info_url": _clean_str(raw.get("info_url")),
        "group": _clean_str(raw.get("group")),
    }
    validation = _sanitize_dataset_validation(raw.get("validation"))
    if validation is not None:
        dataset["validation"] = validation
    return dataset


def _sanitize_custom_source(raw):
    """The cleaned custom-source dict, or None if it has no usable name."""
    if not isinstance(raw, dict):
        return None
    name = _clean_str(raw.get("name"))
    if not name:
        return None

    datasets_raw = raw.get("datasets")
    datasets = []
    seen = set()
    if isinstance(datasets_raw, list):
        for item in datasets_raw:
            dataset = _sanitize_dataset(item)
            if dataset is None:
                continue
            key = dataset["name"].lower()
            if key in seen:
                continue
            seen.add(key)
            datasets.append(dataset)

    return {
        "name": name,
        "info_url": _clean_str(raw.get("info_url")),
        "datasets": datasets,
    }


def _sanitize_custom_sources(raw):
    custom_sources_raw = raw.get("custom_sources")
    if not isinstance(custom_sources_raw, list):
        return []
    result = []
    seen = set()
    for item in custom_sources_raw:
        source = _sanitize_custom_source(item)
        if source is None:
            continue
        key = source["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(source)
    return result


def sanitize_config(raw):
    """A valid config built from `raw`, which may be any shape at all.

    Unknown or malformed pieces are dropped rather than raised on: this is
    what stands between a hand-edited or corrupted user_sources.json and the
    dialog, which must always be able to open.
    """
    try:
        if not isinstance(raw, dict):
            return default_config()
        return {
            "version": 1,
            "hidden_sources": _sanitize_hidden_sources(raw),
            "hidden_datasets": _sanitize_hidden_datasets(raw),
            "custom_sources": _sanitize_custom_sources(raw),
        }
    except Exception:
        # Belt and suspenders: sanitize_config must never raise, no matter
        # what shape of garbage it is handed.
        return default_config()


def parse_config(text):
    """The sanitized config decoded from `text`.

    Raises ValueError on undecodable JSON or a non-dict top level — both
    callers (the config store) treat that the same as a missing file.
    """
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in user config: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("user config must be a JSON object")
    return sanitize_config(parsed)


def serialize_config(config):
    """`config` as JSON text, key order preserved (not sorted)."""
    return json.dumps(config, indent=2)


def apply_hidden(presets, config):
    """A copy of `presets` with the datasets listed in `config` removed.

    Only datasets of DATASET_HIDEABLE_SOURCES are ever filtered, and a whole
    source is never removed — the dialog handles `hidden_sources` itself, at
    the radio level, and still needs the source's dict shape (e.g. Overture's)
    even when its radio ends up hidden. `presets` is never mutated.
    """
    result = {
        source: dict(datasets) if isinstance(datasets, dict) else datasets
        for source, datasets in presets.items()
    }
    config = config if isinstance(config, dict) else {}
    hidden_datasets = config.get("hidden_datasets", {})
    if not isinstance(hidden_datasets, dict):
        return result
    for source in DATASET_HIDEABLE_SOURCES:
        if source not in result or not isinstance(result[source], dict):
            continue
        keys = hidden_datasets.get(source)
        if not isinstance(keys, list):
            continue
        for key in keys:
            result[source].pop(key, None)
    return result


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def slug(name):
    """`name` lowercased, with runs of non-alphanumerics collapsed to `_`."""
    return _NON_ALNUM_RE.sub("_", str(name).lower()).strip("_")

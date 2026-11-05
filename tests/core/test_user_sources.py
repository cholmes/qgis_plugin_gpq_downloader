"""The user-config model: sanitization, parsing, and merge with bundled presets.

sanitize_config() is the trust boundary — it is the only thing standing
between a hand-edited or corrupted user_sources.json and the dialog, so these
cases pin that it never raises and always returns something the rest of the
plugin can rely on (the shape in the plan's Design section), no matter what
garbage comes in.
"""

import pytest

from gpq_downloader.core.user_sources import (
    DATASET_HIDEABLE_SOURCES,
    HIDEABLE_SOURCES,
    apply_hidden,
    default_config,
    parse_config,
    sanitize_config,
    serialize_config,
    slug,
)


def test_default_config_shape():
    config = default_config()
    assert config == {
        "version": 1,
        "hidden_sources": [],
        "hidden_datasets": {},
        "custom_sources": [],
    }
    # Two calls share no objects: mutating one must not affect the other.
    other = default_config()
    other["hidden_sources"].append("overture")
    other["hidden_datasets"]["x"] = ["y"]
    other["custom_sources"].append({"name": "x"})
    assert default_config() == {
        "version": 1,
        "hidden_sources": [],
        "hidden_datasets": {},
        "custom_sources": [],
    }


def test_sanitize_passes_valid_config_through():
    raw = {
        "version": 1,
        "hidden_sources": ["overture", "openstreetmap"],
        "hidden_datasets": {"source_cooperative": ["usda_crop"]},
        "custom_sources": [
            {
                "name": "My Company Data",
                "info_url": "https://example.com/catalog",
                "datasets": [
                    {
                        "name": "Parcels 2025",
                        "url": "https://example.com/parcels.parquet",
                        "info_url": "https://example.com/parcels",
                        "group": "Boundaries",
                    }
                ],
            }
        ],
    }
    assert sanitize_config(raw) == raw


def test_sanitize_drops_unhideable_sources():
    raw = {"hidden_sources": ["overture", "custom_url", "junk", "openstreetmap"]}
    result = sanitize_config(raw)
    assert result["hidden_sources"] == ["overture", "openstreetmap"]
    for name in result["hidden_sources"]:
        assert name in HIDEABLE_SOURCES


def test_sanitize_limits_hidden_datasets_to_flat_sources():
    raw = {
        "hidden_datasets": {
            "overture": ["buildings"],
            "source_cooperative": ["usda_crop"],
        }
    }
    result = sanitize_config(raw)
    assert "overture" not in result["hidden_datasets"]
    assert result["hidden_datasets"] == {"source_cooperative": ["usda_crop"]}
    for key in result["hidden_datasets"]:
        assert key in DATASET_HIDEABLE_SOURCES


def test_sanitize_drops_nameless_and_urlless_datasets():
    raw = {
        "custom_sources": [
            {
                "name": "Good Source",
                "datasets": [
                    {"name": "Has URL", "url": "https://example.com/a.parquet"},
                    {"name": "", "url": "https://example.com/b.parquet"},
                    {"name": "No URL"},
                    {"url": "https://example.com/c.parquet"},
                    "not a dict",
                ],
            }
        ]
    }
    result = sanitize_config(raw)
    [source] = result["custom_sources"]
    names = [d["name"] for d in source["datasets"]]
    assert names == ["Has URL"]


def test_sanitize_dedupes_source_and_dataset_names_case_insensitively():
    raw = {
        "custom_sources": [
            {
                "name": "My Source",
                "datasets": [
                    {"name": "Dataset A", "url": "https://example.com/a"},
                    {"name": "dataset a", "url": "https://example.com/dupe"},
                    {"name": "Dataset B", "url": "https://example.com/b"},
                ],
            },
            {
                "name": "my source",
                "datasets": [
                    {"name": "Other", "url": "https://example.com/other"},
                ],
            },
        ]
    }
    result = sanitize_config(raw)
    assert len(result["custom_sources"]) == 1
    source = result["custom_sources"][0]
    assert source["name"] == "My Source"
    dataset_names = [d["name"] for d in source["datasets"]]
    assert dataset_names == ["Dataset A", "Dataset B"]
    # First wins: the kept dataset is the first one seen, not the duplicate.
    assert source["datasets"][0]["url"] == "https://example.com/a"


def test_sanitize_strips_whitespace_and_coerces_missing_optional_fields():
    raw = {
        "custom_sources": [
            {
                "name": "  Spacey  ",
                "datasets": [
                    {"name": "  Padded  ", "url": "  https://example.com/x  "}
                ],
            }
        ]
    }
    result = sanitize_config(raw)
    source = result["custom_sources"][0]
    assert source["name"] == "Spacey"
    assert source["info_url"] == ""
    dataset = source["datasets"][0]
    assert dataset["name"] == "Padded"
    assert dataset["url"] == "https://example.com/x"
    assert dataset["info_url"] == ""
    assert dataset["group"] == ""


@pytest.mark.parametrize(
    "garbage",
    [
        None,
        42,
        "a string",
        ["a", "list"],
        object(),
        {"hidden_sources": "nope"},
        {"hidden_datasets": "nope"},
        {"custom_sources": "nope"},
        {"custom_sources": [None, 1, "x", {"name": 1, "datasets": "nope"}]},
    ],
)
def test_sanitize_never_raises_on_any_input_shape(garbage):
    result = sanitize_config(garbage)
    assert result["version"] == 1
    assert isinstance(result["hidden_sources"], list)
    assert isinstance(result["hidden_datasets"], dict)
    assert isinstance(result["custom_sources"], list)


def test_parse_config_raises_on_bad_json_and_non_dict():
    with pytest.raises(ValueError):
        parse_config("{not json")
    with pytest.raises(ValueError):
        parse_config("[1, 2, 3]")
    with pytest.raises(ValueError):
        parse_config('"just a string"')


def test_serialize_then_parse_roundtrips():
    config = sanitize_config(
        {
            "hidden_sources": ["overture"],
            "hidden_datasets": {"openstreetmap": ["roads"]},
            "custom_sources": [
                {
                    "name": "Co",
                    "info_url": "https://example.com",
                    "datasets": [
                        {
                            "name": "D",
                            "url": "https://example.com/d",
                            "info_url": "https://example.com/d-info",
                            "group": "G",
                        }
                    ],
                }
            ],
        }
    )
    text = serialize_config(config)
    assert parse_config(text) == config


def test_apply_hidden_removes_listed_datasets_only():
    presets = {
        "overture": {"buildings": {"url": "x"}, "places": {"url": "y"}},
        "source_cooperative": {"a": {"url": "1"}, "b": {"url": "2"}},
        "openstreetmap": {"roads": {"url": "3"}, "poi": {"url": "4"}},
    }
    config = sanitize_config(
        {
            "hidden_datasets": {
                "source_cooperative": ["a"],
                "overture": ["buildings"],  # dropped by sanitize; not flat-hideable
            }
        }
    )
    result = apply_hidden(presets, config)
    assert set(result["source_cooperative"]) == {"b"}
    assert set(result["openstreetmap"]) == {"roads", "poi"}
    # Overture is never filtered at the dataset level, and the whole source
    # dict is never removed.
    assert set(result["overture"]) == {"buildings", "places"}
    assert set(result.keys()) == set(presets.keys())


def test_apply_hidden_leaves_original_presets_untouched():
    presets = {
        "source_cooperative": {"a": {"url": "1"}, "b": {"url": "2"}},
    }
    config = sanitize_config({"hidden_datasets": {"source_cooperative": ["a"]}})
    result = apply_hidden(presets, config)
    assert "a" in presets["source_cooperative"]
    assert result is not presets
    assert result["source_cooperative"] is not presets["source_cooperative"]


def test_slug():
    assert slug("My Data (2025)!") == "my_data_2025"
    assert slug("usda_crop") == "usda_crop"
    assert slug("  leading and trailing  ") == "leading_and_trailing"

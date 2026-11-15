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
    normalize_style,
    parse_config,
    sanitize_config,
    serialize_config,
    slug,
    source_cooperative_group,
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
        "hidden_datasets": {"openstreetmap": ["addresses"]},
        "custom_sources": [
            {
                "name": "My Company Data",
                "info_url": "https://example.com/catalog",
                "style": "checkboxes",
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


def test_sanitize_drops_legacy_source_cooperative_hidden_entries():
    """Source Cooperative is no longer a built-in (it is seeded into
    custom_sources instead), so a "source_cooperative" entry left over from
    an older session's hidden_sources/hidden_datasets is simply dropped
    rather than resurrected as something meaningful."""
    raw = {
        "hidden_sources": ["source_cooperative", "overture"],
        "hidden_datasets": {"source_cooperative": ["usda_crop"], "overture": ["buildings"]},
    }
    result = sanitize_config(raw)
    assert result["hidden_sources"] == ["overture"]
    assert result["hidden_datasets"] == {"overture": ["buildings"]}


def test_sanitize_limits_hidden_datasets_to_dataset_hideable_sources():
    raw = {
        "hidden_datasets": {
            "overture": ["buildings"],
            "openstreetmap": ["addresses"],
            "custom_url": ["anything"],
            "junk": ["whatever"],
        }
    }
    result = sanitize_config(raw)
    assert result["hidden_datasets"] == {
        "overture": ["buildings"],
        "openstreetmap": ["addresses"],
    }
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


# --- dataset "validation" (Task B / #84 follow-up: URL pre-validation) ---


def test_sanitize_keeps_a_well_formed_validation_dict():
    raw = {
        "custom_sources": [
            {
                "name": "Co",
                "datasets": [
                    {
                        "name": "D",
                        "url": "https://example.com/d.parquet",
                        "validation": {
                            "has_bbox": True,
                            "bbox_column": "bbox",
                            "geometry_column": "geom",
                        },
                    }
                ],
            }
        ]
    }
    result = sanitize_config(raw)
    dataset = result["custom_sources"][0]["datasets"][0]
    assert dataset["validation"] == {
        "has_bbox": True,
        "bbox_column": "bbox",
        "geometry_column": "geom",
    }


def test_sanitize_normalizes_validation_types_and_defaults():
    raw = {
        "custom_sources": [
            {
                "name": "Co",
                "datasets": [
                    {
                        "name": "D",
                        "url": "https://example.com/d.parquet",
                        # has_bbox coerced to bool; bbox_column blank -> None;
                        # geometry_column missing -> defaults to "geometry".
                        "validation": {"has_bbox": 1, "bbox_column": "  "},
                    }
                ],
            }
        ]
    }
    result = sanitize_config(raw)
    dataset = result["custom_sources"][0]["datasets"][0]
    assert dataset["validation"] == {
        "has_bbox": True,
        "bbox_column": None,
        "geometry_column": "geometry",
    }


@pytest.mark.parametrize("bad_validation", [None, "nope", 42, [], {"has_bbox": True}])
def test_sanitize_drops_malformed_validation_but_keeps_the_dataset(bad_validation):
    """A dataset's "validation" is not even a dict (or, for the last case, a
    dict missing a usable shape is still handled - _sanitize_dataset_validation
    only refuses a non-dict): either way the dataset itself is never dropped,
    it just ends up unvalidated or with a best-effort validation dict."""
    raw = {
        "custom_sources": [
            {
                "name": "Co",
                "datasets": [
                    {
                        "name": "D",
                        "url": "https://example.com/d.parquet",
                        "validation": bad_validation,
                    }
                ],
            }
        ]
    }
    result = sanitize_config(raw)
    [dataset] = result["custom_sources"][0]["datasets"]
    assert dataset["name"] == "D"
    if isinstance(bad_validation, dict):
        assert dataset["validation"] == {
            "has_bbox": True,
            "bbox_column": None,
            "geometry_column": "geometry",
        }
    else:
        assert "validation" not in dataset


def test_sanitize_dataset_without_validation_key_stays_legal():
    raw = {
        "custom_sources": [
            {"name": "Co", "datasets": [{"name": "D", "url": "https://example.com/d"}]}
        ]
    }
    result = sanitize_config(raw)
    dataset = result["custom_sources"][0]["datasets"][0]
    assert "validation" not in dataset


def test_validation_roundtrips_through_serialize_and_parse():
    config = sanitize_config(
        {
            "custom_sources": [
                {
                    "name": "Co",
                    "datasets": [
                        {
                            "name": "D",
                            "url": "https://example.com/d",
                            "validation": {
                                "has_bbox": True,
                                "bbox_column": "bbox",
                                "geometry_column": "geometry",
                            },
                        },
                        {"name": "Unvalidated", "url": "https://example.com/u"},
                    ],
                }
            ]
        }
    )
    text = serialize_config(config)
    assert parse_config(text) == config
    datasets = parse_config(text)["custom_sources"][0]["datasets"]
    by_name = {d["name"]: d for d in datasets}
    assert "validation" in by_name["D"]
    assert "validation" not in by_name["Unvalidated"]


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
        "openstreetmap": {"roads": {"url": "3"}, "poi": {"url": "4"}},
    }
    config = sanitize_config(
        {
            "hidden_datasets": {
                "openstreetmap": ["poi"],
                "overture": ["buildings"],
            }
        }
    )
    result = apply_hidden(presets, config)
    assert set(result["openstreetmap"]) == {"roads"}
    assert set(result["overture"]) == {"places"}
    # The whole source dict is never removed, even when every dataset is.
    assert set(result.keys()) == set(presets.keys())


def test_apply_hidden_leaves_original_presets_untouched():
    presets = {
        "openstreetmap": {"a": {"url": "1"}, "b": {"url": "2"}},
    }
    config = sanitize_config({"hidden_datasets": {"openstreetmap": ["a"]}})
    result = apply_hidden(presets, config)
    assert "a" in presets["openstreetmap"]
    assert result is not presets
    assert result["openstreetmap"] is not presets["openstreetmap"]


def test_slug():
    assert slug("My Data (2025)!") == "my_data_2025"
    assert slug("usda_crop") == "usda_crop"
    assert slug("  leading and trailing  ") == "leading_and_trailing"


# --- source_cooperative_group(): the seeded first-run custom group --------


def test_source_cooperative_group_shape():
    presets = {
        "source_cooperative": {
            "vida_buildings": {
                "url": "s3://vida/*.parquet",
                "info_url": "https://source.coop/vida",
                "display_name": "VIDA Buildings",
            },
        }
    }
    group = source_cooperative_group(presets)
    assert group == {
        "name": "Source Cooperative",
        "info_url": "https://source.coop",
        "style": "dropdown",
        "datasets": [
            {
                "name": "VIDA Buildings",
                "url": "s3://vida/*.parquet",
                "info_url": "https://source.coop/vida",
                "group": "",
            }
        ],
    }


def test_source_cooperative_group_falls_back_to_titled_key():
    presets = {"source_cooperative": {"usda_crop": {"url": "https://example.com/x"}}}
    group = source_cooperative_group(presets)
    [dataset] = group["datasets"]
    assert dataset["name"] == "Usda_Crop"
    assert dataset["info_url"] == ""


def test_source_cooperative_group_skips_entries_without_a_usable_url():
    presets = {
        "source_cooperative": {
            "good": {"url": "https://example.com/good.parquet"},
            "no_url": {"display_name": "No URL"},
            "blank_url": {"url": "", "display_name": "Blank URL"},
            "not_a_dict": "junk",
        }
    }
    group = source_cooperative_group(presets)
    names = [d["name"] for d in group["datasets"]]
    assert names == ["Good"]


def test_source_cooperative_group_carries_validation_for_needs_validation_false():
    """A bundled SC entry whose publisher guarantees a standard GeoParquet
    "bbox" covering (needs_validation: false) is seeded with that result
    already stored, so a download of it never has to probe; nhd_flowlines
    (needs_validation: true) gets no "validation" key at all, exactly like
    an unvalidated dataset a user added themselves (Task B / #84 follow-up).
    """
    presets = {
        "source_cooperative": {
            "vida_buildings": {
                "url": "s3://vida/*.parquet",
                "display_name": "VIDA Buildings",
                "needs_validation": False,
            },
            "nhd_flowlines": {
                "url": "s3://nhd/*.parquet",
                "display_name": "NHD Flowlines",
                "needs_validation": True,
            },
        }
    }
    group = source_cooperative_group(presets)
    by_name = {d["name"]: d for d in group["datasets"]}
    assert by_name["VIDA Buildings"]["validation"] == {
        "has_bbox": True,
        "bbox_column": "bbox",
        "geometry_column": "geometry",
    }
    assert "validation" not in by_name["NHD Flowlines"]


def test_source_cooperative_group_missing_section_gives_empty_datasets():
    assert source_cooperative_group({}) == {
        "name": "Source Cooperative",
        "info_url": "https://source.coop",
        "style": "dropdown",
        "datasets": [],
    }


# --- per-source page style (checkboxes vs drop-down) ---------------------


@pytest.mark.parametrize(
    "raw_style, expected",
    [
        ("dropdown", "dropdown"),
        ("checkboxes", "checkboxes"),
        (None, "checkboxes"),
        ("", "checkboxes"),
        ("Dropdown", "checkboxes"),
        ("combo", "checkboxes"),
        (1, "checkboxes"),
        (["dropdown"], "checkboxes"),
        ({"style": "dropdown"}, "checkboxes"),
    ],
)
def test_normalize_style(raw_style, expected):
    assert normalize_style(raw_style) == expected


@pytest.mark.parametrize(
    "raw_style, expected",
    [
        ("dropdown", "dropdown"),
        ("checkboxes", "checkboxes"),
        ("junk", "checkboxes"),
        (42, "checkboxes"),
        (None, "checkboxes"),
    ],
)
def test_sanitize_normalizes_a_custom_sources_style(raw_style, expected):
    source = {"name": "Co", "datasets": []}
    if raw_style is not None:
        source["style"] = raw_style
    [cleaned] = sanitize_config({"custom_sources": [source]})["custom_sources"]
    assert cleaned["style"] == expected


def test_sanitize_gives_every_custom_source_a_style():
    """A config written before the key existed still reads as the checkbox
    grid it always rendered as - no source ever comes out without a style."""
    raw = {
        "custom_sources": [
            {"name": "Old Config Group", "datasets": []},
            {"name": "Dropdown Group", "style": "dropdown", "datasets": []},
        ]
    }
    styles = {
        source["name"]: source["style"]
        for source in sanitize_config(raw)["custom_sources"]
    }
    assert styles == {"Old Config Group": "checkboxes", "Dropdown Group": "dropdown"}


def test_style_roundtrips_through_serialize_and_parse():
    config = default_config()
    config["custom_sources"] = [
        {
            "name": "Picker",
            "info_url": "",
            "style": "dropdown",
            "datasets": [
                {
                    "name": "D",
                    "url": "https://example.com/d.parquet",
                    "info_url": "",
                    "group": "",
                }
            ],
        },
        {
            "name": "Grid",
            "info_url": "",
            "style": "checkboxes",
            "datasets": [
                {
                    "name": "E",
                    "url": "https://example.com/e.parquet",
                    "info_url": "",
                    "group": "",
                }
            ],
        },
    ]
    assert parse_config(serialize_config(config)) == config

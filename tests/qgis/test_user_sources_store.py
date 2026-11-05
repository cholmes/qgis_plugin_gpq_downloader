"""user_sources_store.py: load/save the user's custom-sources config file.

Every test here already runs under tests/qgis/conftest.py's autouse
isolated_user_sources fixture, so user_config_path() points into this test's
own tmp_path rather than the developer's real QGIS profile.
"""

import os

import pytest

from gpq_downloader.core.user_sources import default_config
from gpq_downloader.gui import user_sources_store


def test_missing_file_returns_defaults():
    assert not os.path.exists(user_sources_store.user_config_path())

    assert user_sources_store.load_user_config() == default_config()


def test_unreadable_file_returns_defaults(logged):
    path = user_sources_store.user_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"{ not valid json !!!")

    assert user_sources_store.load_user_config() == default_config()
    assert len(logged) == 1


def test_invalid_utf8_returns_defaults(logged):
    path = user_sources_store.user_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\xff\xfe\x00junk")

    assert user_sources_store.load_user_config() == default_config()
    assert len(logged) == 1


def test_save_then_load_roundtrips():
    config = default_config()
    config["hidden_sources"] = ["overture"]

    user_sources_store.save_user_config(config)

    assert user_sources_store.load_user_config() == config


def test_save_creates_parent_directory(tmp_path, monkeypatch):
    # The autouse fixture's path sits directly in tmp_path, which pytest has
    # already created; point at a not-yet-existing subdirectory instead so
    # this actually exercises the makedirs() call.
    nested_path = tmp_path / "profile" / "gpq_downloader" / "user_sources.json"
    monkeypatch.setattr(
        user_sources_store, "user_config_path", lambda: str(nested_path)
    )
    assert not nested_path.parent.exists()

    user_sources_store.save_user_config(default_config())

    assert nested_path.exists()


def test_autouse_fixture_points_path_into_tmp(tmp_path):
    assert user_sources_store.user_config_path().startswith(str(tmp_path))


def test_write_failure_is_not_masked_by_a_missing_temp_file(monkeypatch):
    """If the temp file is already gone by the time the except block tries
    to clean it up (os.replace() failing part-way, say), the cleanup must
    not raise its own FileNotFoundError in place of the original error -
    that would tell the caller the wrong thing happened."""

    def flaky_replace(src, dst):
        os.remove(src)  # gone before the except block's os.remove() runs
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", flaky_replace)

    with pytest.raises(OSError, match="disk went away"):
        user_sources_store.save_user_config(default_config())

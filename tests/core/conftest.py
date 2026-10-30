"""Fixtures for the headless characterization tests.

core_support is imported first: it puts QGIS stand-ins in sys.modules when the
real qgis is not importable, before anything imports gpq_downloader.
"""

import core_support  # noqa: F401  (must run before gpq_downloader is imported)
import pytest

from gpq_downloader.core import overture


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """No test here reaches the network, and none sees another's STAC cache.

    overture_part_extents() swallows any error from urlopen, so an unexpected
    request is recorded and failed at teardown rather than only raised. A test
    that serves the STAC itself patches urlopen again, which replaces this one.
    """
    calls = []

    def urlopen(url, *args, **kwargs):
        calls.append(url)
        raise OSError(f"network blocked in tests: {url}")

    monkeypatch.setattr(overture.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(overture, "_overture_extent_cache", {})
    yield calls
    assert not calls, f"unexpected network requests: {calls}"

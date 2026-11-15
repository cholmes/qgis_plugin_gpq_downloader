"""Every import inside the package must be relative.

QGIS loads a plugin under the name of its directory in the profile's
plugins/ folder. That is never `gpq_downloader`: scripts/build_plugin.py
names every folder in the published zip `qgis_plugin_gpq_downloader`,
because that is what plugins.qgis.org publishes the plugin as, so every
installed copy in the world unpacks under that name. There is no top-level
`gpq_downloader` module at runtime, and an absolute
`from gpq_downloader... import` raises ModuleNotFoundError the moment
classFactory() touches it - for every user, on first load.

Nothing else catches this. pytest puts the repo root on sys.path, where the
package directory really is named `gpq_downloader`, so absolute imports
resolve in every test and in CI and fail only in a real QGIS.

A relative import that climbs past the package top (`from ...core import x`
inside gpq_downloader/gui/) fails the same way and for the same reason, so
it is checked here too. What this cannot see is a name assembled at runtime
- importlib.import_module("gpq_downloader.core") and friends. Nothing in
the package imports that way; if something ever needs to, it needs its own
guard.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "gpq_downloader"


def _unresolvable_imports(path):
    """Every (line, statement) in path that will not resolve inside QGIS.

    Two kinds: an import naming the package absolutely, and a relative
    import with more dots than there are packages above the module.
    """
    # gpq_downloader/gui/settings_page.py sits two packages deep, so `from
    # ..x import` is the furthest it may climb; one more leaves the package.
    depth = len(path.relative_to(PACKAGE.parent).parts) - 1
    tree = ast.parse(path.read_text(), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module and node.module.split(".")[0] == "gpq_downloader":
                    found.append((node.lineno, f"from {node.module} import ..."))
            elif node.level > depth:
                dots = "." * node.level
                found.append((
                    node.lineno,
                    f"from {dots}{node.module or ''} import ... "
                    f"({node.level} levels up from a module {depth} deep)",
                ))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "gpq_downloader":
                    found.append((node.lineno, f"import {alias.name}"))
    return found


def test_every_import_in_the_package_resolves_under_its_installed_name():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for lineno, statement in _unresolvable_imports(path):
            offenders.append(f"{path.relative_to(PACKAGE.parent)}:{lineno}: {statement}")

    assert not offenders, (
        "These imports raise ModuleNotFoundError in QGIS, where the plugin "
        "is loaded under its directory's name - they either name the package "
        "absolutely or climb out of it. Use a relative import that stays "
        "inside the package:\n  "
        + "\n  ".join(offenders)
    )

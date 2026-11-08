"""Every import inside the package must be relative.

QGIS loads a plugin under the name of its directory in the profile's
plugins/ folder, which is whatever the person cloned or symlinked it as -
`qgis_plugin_gpq_downloader` for this repo's own README instructions, and
the zip in dist/ unpacks to a third name again. So `gpq_downloader` is not
an importable top-level module at runtime, and an absolute
`from gpq_downloader... import` raises ModuleNotFoundError the moment
classFactory() touches it.

Nothing else catches this. pytest puts the repo root on sys.path, where the
package directory really is named `gpq_downloader`, so absolute imports
resolve in every test and in CI and fail only in a real QGIS.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "gpq_downloader"


def _absolute_self_imports(path):
    """Every (line, statement) in path importing the package by its own name."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found = []
    for node in ast.walk(tree):
        # level > 0 is a relative import - `from . import x` - and fine.
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module and node.module.split(".")[0] == "gpq_downloader":
                found.append((node.lineno, f"from {node.module} import ..."))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "gpq_downloader":
                    found.append((node.lineno, f"import {alias.name}"))
    return found


def test_the_package_never_imports_itself_by_name():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for lineno, statement in _absolute_self_imports(path):
            offenders.append(f"{path.relative_to(PACKAGE.parent)}:{lineno}: {statement}")

    assert not offenders, (
        "These imports name the package absolutely, so they raise "
        "ModuleNotFoundError in QGIS, where the plugin is loaded under its "
        "directory's name. Use a relative import instead:\n  "
        + "\n  ".join(offenders)
    )

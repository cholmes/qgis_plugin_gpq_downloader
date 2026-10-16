#!/usr/bin/env python3
"""Build the plugin zip that goes to GitHub and plugins.qgis.org.

    python3 scripts/build_plugin.py                  # dist/qgis_plugin_gpq_downloader-<version>.zip
    python3 scripts/build_plugin.py --print-version  # just the version in metadata.txt
    python3 scripts/build_plugin.py --print-notes    # this version's CHANGELOG.md section

The source lives in gpq_downloader/, but QGIS and plugins.qgis.org know the
plugin as qgis_plugin_gpq_downloader, so that is the folder name inside the zip.
The zip's metadata.txt gets two things the checked-in one does not carry:

- `changelog=`, the entries of this version's CHANGELOG.md section, which the
  QGIS Plugin Manager shows on the plugin's "Changelog" tab.
- `experimental=True` for a pre-release version (one with a hyphen, such as
  0.9.0-beta.1), so plugins.qgis.org only offers it to users who opted in to
  experimental plugins. A plain version gets `experimental=False`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "gpq_downloader"
METADATA = SOURCE / "metadata.txt"
CHANGELOG = ROOT / "CHANGELOG.md"
PACKAGE_NAME = "qgis_plugin_gpq_downloader"
REPO = "cholmes/qgis_plugin_gpq_downloader"

EXCLUDED_DIRS = {"__pycache__", "tests", ".pytest_cache"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip"}
EXCLUDED_NAMES = {".DS_Store", ".gitignore"}

ENTRY_RE = re.compile(r"^- (?P<title>.+?) by \[@")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def read_version(metadata: str) -> str:
    match = re.search(r"^version=(.+)$", metadata, re.MULTILINE)
    if not match:
        raise SystemExit("error: no version= line in metadata.txt")
    return match.group(1).strip()


def is_prerelease(version: str) -> bool:
    return "-" in version


def changelog_section(changelog: str, version: str) -> str | None:
    """The body of the `## v<version>` section, without its heading."""
    match = re.search(rf"^## v{re.escape(version)}(?=\s|$).*$", changelog, re.MULTILINE)
    if not match:
        return None
    end = changelog.find("\n## ", match.end())
    return changelog[match.end() : end if end != -1 else None].strip()


def plugin_manager_changelog(version: str, section: str) -> str:
    """The section's entries as plain text for metadata.txt's changelog key.

    The Plugin Manager renders this as text, so the markdown links go: each line
    is the pull request title and nothing else.
    """
    titles = [m["title"] for m in map(ENTRY_RE.match, section.splitlines()) if m]
    lines = [version] + ["- " + MD_LINK_RE.sub(r"\1", t) for t in titles]
    return "\n".join(lines)


def release_metadata(metadata: str, version: str, changelog: str | None) -> str:
    """metadata.txt as it should ship: experimental flag and changelog set."""
    # Drop any checked-in values for the keys this build owns, including the
    # indented continuation lines of a multi-line changelog.
    out, skipping = [], False
    for line in metadata.splitlines():
        if re.match(r"^(experimental|changelog)\s*=", line):
            skipping = True
            continue
        if skipping and (line.startswith((" ", "\t")) and line.strip()):
            continue
        skipping = False
        out.append(line)

    try:
        general = next(i for i, line in enumerate(out) if line.strip() == "[general]")
        # After the version line, so the [general] keys stay together.
        version_line = next(
            i for i, line in enumerate(out) if i > general and line.startswith("version=")
        )
    except StopIteration:
        raise SystemExit(
            "error: metadata.txt needs a [general] section with a version= line"
        ) from None
    insert = [f"experimental={is_prerelease(version)}"]
    if changelog:
        first, *rest = changelog.splitlines()
        insert.append(f"changelog={first}")
        insert.extend(f"    {line}" for line in rest)
    out[version_line + 1 : version_line + 1] = insert
    return "\n".join(out) + "\n"


def included(path: Path) -> bool:
    rel = path.relative_to(SOURCE)
    if any(part in EXCLUDED_DIRS for part in rel.parts):
        return False
    return path.name not in EXCLUDED_NAMES and path.suffix not in EXCLUDED_SUFFIXES


def source_files() -> list[Path]:
    """The files that go in the zip, symlinks skipped.

    Inside a git checkout the list is what git tracks under the source dir, so
    a stray untracked file (.env, an editor swap file) can never ship from a
    manual local build. The excludes-based walk is only the fallback for a
    source export without git.
    """
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--", SOURCE.name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip("\0"):
        files = [ROOT / name for name in result.stdout.split("\0") if name]
    else:
        files = list(SOURCE.rglob("*"))
    return sorted(p for p in files if p.is_file() and not p.is_symlink() and included(p))


def build(out_dir: Path, require_changelog: bool) -> Path:
    metadata = METADATA.read_text()
    version = read_version(metadata)
    section = changelog_section(CHANGELOG.read_text(), version) if CHANGELOG.exists() else None
    if section is None and require_changelog:
        raise SystemExit(
            f"error: CHANGELOG.md has no '## v{version}' section. "
            "Generate it with scripts/release_notes.py before releasing."
        )
    changelog = plugin_manager_changelog(version, section) if section else None

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{PACKAGE_NAME}-{version}.zip"
    license_path = ROOT / "LICENSE"
    if not license_path.is_file():
        raise SystemExit(f"error: no LICENSE file at {license_path}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in source_files():
            arcname = f"{PACKAGE_NAME}/{path.relative_to(SOURCE).as_posix()}"
            if path == METADATA:
                zf.writestr(arcname, release_metadata(metadata, version, changelog))
            else:
                zf.write(path, arcname)
        zf.write(license_path, f"{PACKAGE_NAME}/LICENSE")
    return zip_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--print-version", action="store_true")
    parser.add_argument(
        "--print-notes",
        action="store_true",
        help="print this version's CHANGELOG.md section, for the GitHub release notes",
    )
    parser.add_argument(
        "--require-changelog",
        action="store_true",
        help="fail unless CHANGELOG.md has a section for this version (the release workflow sets this)",
    )
    args = parser.parse_args(argv)

    if args.print_version:
        print(read_version(METADATA.read_text()))
        return 0

    if args.print_notes:
        version = read_version(METADATA.read_text())
        section = changelog_section(CHANGELOG.read_text(), version) if CHANGELOG.exists() else None
        if section is None:
            raise SystemExit(f"error: CHANGELOG.md has no '## v{version}' section.")
        print(section)
        print()
        print(f"Full changelog: https://github.com/{REPO}/blob/main/CHANGELOG.md")
        return 0
    print(build(args.out_dir, args.require_changelog))
    return 0


if __name__ == "__main__":
    sys.exit(main())

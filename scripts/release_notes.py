#!/usr/bin/env python3
"""Build a CHANGELOG.md release section from the merged pull requests.

GitHub's own release-note generator already knows every pull request between two
tags, who wrote it, and who contributed for the first time. This script asks for
that list and groups it into Keep a Changelog sections, so every release reads
the same way and nobody hand-writes the bullets.

    python3 scripts/release_notes.py 0.9.0                 # print the section
    python3 scripts/release_notes.py 0.9.0 --write         # splice into CHANGELOG.md
    python3 scripts/release_notes.py 0.9.0 --contributors  # who is new, and what they sent
    python3 scripts/release_notes.py 0.9.0 --bump          # set the version in metadata.txt and pyproject.toml

Pull request titles here are plain sentences ("Fix ...", "Add ..."), so the
section comes from the title's first word. Where that guesses wrong,
`release_overrides.json` sets the section and/or the wording for one pull
request, keyed by its number.

The summary paragraphs are not generated. The section is written with a
placeholder where they belong, and the release skill (.claude/skills/release/)
fills it in and stops for human review before anything is tagged.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date as date_cls
from pathlib import Path

REPO = "cholmes/qgis_plugin_gpq_downloader"
ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
METADATA = ROOT / "gpq_downloader" / "metadata.txt"
PYPROJECT = ROOT / "pyproject.toml"
OVERRIDES_PATH = Path(__file__).resolve().parent / "release_overrides.json"

# Thanked in the summary as new contributors; the maintainer is never new, even
# when GitHub calls a co-authored pull request a "first contribution".
MAINTAINERS = {"cholmes"}

SECTIONS = ("Added", "Changed", "Fixed", "Documentation", "Internal")

# Counted, not listed: CI and repo chores are not what a changelog is for. The
# compare link at the end of the section still reaches every one of them.
OMITTED_SECTIONS = ("Internal",)

# First word of the title -> section. Anything else is Changed.
VERB_SECTIONS = {
    "add": "Added",
    "adds": "Added",
    "allow": "Added",
    "enable": "Added",
    "implement": "Added",
    "introduce": "Added",
    "support": "Added",
    "new": "Added",
    "fix": "Fixed",
    "fixes": "Fixed",
    "fixed": "Fixed",
    "correct": "Fixed",
    "repair": "Fixed",
    "prevent": "Fixed",
    "stop": "Fixed",
    "handle": "Fixed",
    "clear": "Fixed",
    "docs": "Documentation",
    "document": "Documentation",
    "readme": "Documentation",
    "bump": "Internal",
    "ci": "Internal",
    "test": "Internal",
    "tests": "Internal",
    "chore": "Internal",
    "release": "Internal",
}

# Conventional-commit prefixes, for the contributors who use them.
PREFIX_SECTIONS = {
    "feat": "Added",
    "fix": "Fixed",
    "perf": "Changed",
    "refactor": "Changed",
    "docs": "Documentation",
}
PREFIX_RE = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]*\))?!?:\s*")

SUMMARY_PLACEHOLDER = "<!-- TODO: 2-4 paragraphs on the highlights of this release. -->"

AUTHOR = r"[\w.\[\]-]+"
# "* <title> by @<author>[ with @<coauthor>] in <url>", the shape GitHub generates.
ENTRY_RE = re.compile(
    rf"^\* (?P<title>.+?) by @(?P<author>{AUTHOR})(?: with @(?P<coauthor>{AUTHOR}))? in "
    r"https://github\.com/[\w.-]+/[\w.-]+/pull/(?P<number>\d+)\s*$"
)
NEW_CONTRIBUTOR_RE = re.compile(
    rf"^\* @(?P<author>{AUTHOR})(?: with @{AUTHOR})? made their first contribution in "
    r"(?P<url>\S+)"
)
ISSUE_RE = re.compile(r"(?<![\w/\[])#(\d+)\b")
MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
VERSION_HEADING_RE = re.compile(r"^## ", re.MULTILINE)


class ReleaseNotesError(RuntimeError):
    """The changelog or the generated notes are not in a shape we can use."""


@dataclass(frozen=True)
class Entry:
    title: str
    author: str
    number: int
    coauthor: str | None = None
    section: str | None = None  # set by an override; otherwise classified from the title

    @property
    def url(self) -> str:
        return f"https://github.com/{REPO}/pull/{self.number}"


@dataclass
class Notes:
    entries: list[Entry] = field(default_factory=list)
    new_contributors: list[tuple[str, str]] = field(default_factory=list)  # (author, url)
    full_changelog: str = ""


def parse(body: str) -> Notes:
    """Split GitHub's generated notes into entries, new contributors and the link."""
    notes = Notes()
    section = ""
    for line in body.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if line.startswith("**Full Changelog**"):
            notes.full_changelog = line.strip()
            continue
        if section == "New Contributors":
            match = NEW_CONTRIBUTOR_RE.match(line)
            if match and match["author"] not in MAINTAINERS:
                notes.new_contributors.append((match["author"], match["url"]))
            continue
        match = ENTRY_RE.match(line)
        if match:
            notes.entries.append(
                Entry(
                    title=match["title"].strip(),
                    author=match["author"],
                    number=int(match["number"]),
                    coauthor=match["coauthor"],
                )
            )
    return notes


def load_overrides(path: Path) -> dict[int, dict[str, str]]:
    """Read the checked-in overrides. A missing file means none.

    Each value is {"section": ..., "title": ...}; either key may be left out.
    """
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text())
    overrides = {}
    for key, value in raw.items():
        if key.startswith("_"):
            continue
        section = value.get("section")
        if section is not None and section not in SECTIONS:
            raise ReleaseNotesError(
                f"override for #{key} names section {section!r}; use one of {', '.join(SECTIONS)}"
            )
        overrides[int(key)] = value
    return overrides


def apply_overrides(notes: Notes, overrides: dict[int, dict[str, str]]) -> Notes:
    notes.entries = [
        Entry(
            title=overrides.get(e.number, {}).get("title", e.title),
            author=e.author,
            number=e.number,
            coauthor=e.coauthor,
            section=overrides.get(e.number, {}).get("section"),
        )
        for e in notes.entries
    ]
    return notes


def classify(entry: Entry) -> str:
    """Return the section an entry belongs in."""
    if entry.section:
        return entry.section
    if entry.author.endswith("[bot]"):
        return "Internal"
    prefix = PREFIX_RE.match(entry.title)
    if prefix:
        return PREFIX_SECTIONS.get(prefix["type"], "Internal")
    first = re.split(r"[\s:(]", entry.title.strip(), maxsplit=1)[0].lower()
    return VERB_SECTIONS.get(first, "Changed")


def display_title(title: str) -> str:
    """Strip a conventional-commit prefix and capitalize, so every line reads alike.

    Markdown links are reduced to their text and `<` is escaped, so a retitled
    pull request cannot plant a link or HTML in the release notes.
    """
    title = PREFIX_RE.sub("", title).strip()
    title = MD_LINK_RE.sub(r"\1", title).replace("<", "&lt;")
    return title[:1].upper() + title[1:]


def contributor_brief(notes: Notes) -> dict[str, list[tuple[int, str]]]:
    """Every pull request each first-time contributor wrote in this range.

    The summary has to thank them for what they actually did, and GitHub's
    "New Contributors" list names only the first one.
    """
    brief: dict[str, list[tuple[int, str]]] = {author: [] for author, _ in notes.new_contributors}
    for entry in notes.entries:
        for name in (entry.author, entry.coauthor):
            if name in brief:
                brief[name].append((entry.number, entry.title))
    return {author: sorted(prs) for author, prs in brief.items()}


def _linkify_issues(title: str) -> str:
    return ISSUE_RE.sub(
        lambda m: f"[#{m.group(1)}](https://github.com/{REPO}/issues/{m.group(1)})", title
    )


def _user(name: str) -> str:
    return f"[@{name}](https://github.com/{name.replace('[bot]', '')})"


def _render_entry(entry: Entry) -> str:
    by = _user(entry.author)
    if entry.coauthor:
        by += f" with {_user(entry.coauthor)}"
    return f"- {_linkify_issues(display_title(entry.title))} by {by} in [#{entry.number}]({entry.url})"


def render(notes: Notes, version: str, date: str, summary: str | None = None) -> str:
    """Render one changelog section: heading, summary, grouped entries, footer."""
    buckets: dict[str, list[Entry]] = {name: [] for name in SECTIONS}
    for entry in notes.entries:
        buckets[classify(entry)].append(entry)

    lines = [f"## v{version} ({date})", "", summary or SUMMARY_PLACEHOLDER, ""]
    for name in SECTIONS:
        if not buckets[name] or name in OMITTED_SECTIONS:
            continue
        lines += [f"### {name}", ""]
        lines += [_render_entry(entry) for entry in buckets[name]]
        lines.append("")

    if notes.new_contributors:
        lines += ["### New Contributors", ""]
        lines += [
            f"- {_user(author)} made their first contribution in {url}"
            for author, url in notes.new_contributors
        ]
        lines.append("")

    omitted = sum(len(buckets[name]) for name in OMITTED_SECTIONS)
    if omitted:
        noun = "internal change is" if omitted == 1 else "internal changes are"
        lines += [f"_{omitted} {noun} not listed here; the full changelog has them._", ""]

    if notes.full_changelog:
        lines += [notes.full_changelog, ""]
    return "\n".join(lines)


def splice(changelog: str, section: str) -> str:
    """Put the section above the newest release, dropping any Unreleased block."""
    version = section.splitlines()[0].split()[1]
    if re.search(rf"^## {re.escape(version)}(?=\s|$)", changelog, re.MULTILINE):
        raise ReleaseNotesError(
            f"CHANGELOG.md already has a {version} section; remove it before rewriting."
        )
    headings = [m.start() for m in VERSION_HEADING_RE.finditer(changelog)]
    if not headings:
        return changelog.rstrip("\n") + "\n\n" + section
    head, rest = changelog[: headings[0]], changelog[headings[0] :]
    if rest.startswith("## Unreleased"):
        rest = rest[headings[1] - headings[0] :] if len(headings) > 1 else ""
    return f"{head}{section}\n{rest}" if rest else f"{head}{section}"


def bump(version: str) -> None:
    """Set the version in metadata.txt (what QGIS reads) and pyproject.toml."""
    for path, pattern, replacement in (
        (METADATA, r"^version=.*$", f"version={version}"),
        (PYPROJECT, r'^version = ".*"$', f'version = "{version}"'),
    ):
        text, count = re.subn(pattern, replacement, path.read_text(), count=1, flags=re.MULTILINE)
        if count != 1:
            raise ReleaseNotesError(f"no version line found in {path.name}")
        path.write_text(text)


def latest_tag() -> str | None:
    """The newest published, non-draft release tag, as GitHub ranks them.

    None means the repository genuinely has no releases yet. Any other failure
    raises: a wrong baseline would silently fold pre-releases into the range.
    """
    result = subprocess.run(
        ["gh", "release", "view", "--repo", REPO, "--json", "tagName", "--jq", ".tagName"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "release not found" in stderr.lower() or "HTTP 404" in stderr:
            return None
        raise ReleaseNotesError(f"gh release view failed: {stderr}")
    return result.stdout.strip() or None


def generate(version: str, previous: str | None) -> Notes:
    """Ask GitHub for the notes between the previous tag and this version."""
    args = [
        "gh", "api", "-X", "POST", f"repos/{REPO}/releases/generate-notes",
        "-f", f"tag_name=v{version}",
        "-f", "target_commitish=main",
    ]  # fmt: skip
    if previous:
        args += ["-f", f"previous_tag_name={previous}"]
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ReleaseNotesError(f"gh api failed: {result.stderr.strip()}")
    return parse(json.loads(result.stdout)["body"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version", help="version being released, without the v (0.9.0)")
    parser.add_argument(
        "--previous",
        help="previous tag (default: the latest GitHub release, so a beta is not the baseline "
        "unless you say so)",
    )
    parser.add_argument("--write", action="store_true", help="splice into CHANGELOG.md")
    parser.add_argument(
        "--contributors",
        action="store_true",
        help="list each first-time contributor and every PR they wrote",
    )
    parser.add_argument("--bump", action="store_true", help="set the version in metadata.txt and pyproject.toml")
    args = parser.parse_args(argv)

    try:
        if args.bump:
            bump(args.version)
            print(f"Set version {args.version} in {METADATA.name} and {PYPROJECT.name}.")
            return 0

        previous = args.previous or latest_tag()
        notes = apply_overrides(generate(args.version, previous), load_overrides(OVERRIDES_PATH))
        if args.contributors:
            for author, prs in contributor_brief(notes).items():
                print(f"@{author}")
                for number, title in prs:
                    print(f"  #{number} {title}")
            return 0

        section = render(notes, args.version, date_cls.today().isoformat())
        if not args.write:
            print(section, end="")
            # So a misfiled user-facing PR is caught in review; never written.
            internal = [e for e in notes.entries if classify(e) == "Internal"]
            if internal:
                print("### Internal (not listed in the changelog)")
                print()
                for entry in internal:
                    print(_render_entry(entry))
            return 0
        CHANGELOG.write_text(splice(CHANGELOG.read_text(), section))
    except ReleaseNotesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    since = f"since {previous}" if previous else "first release"
    print(f"Wrote the v{args.version} section to {CHANGELOG.name} ({since}).")
    print("Now replace the summary placeholder and check each entry's section.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

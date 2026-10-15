"""Tests for the release tooling: scripts/release_notes.py and scripts/build_plugin.py."""

import configparser
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_plugin  # noqa: E402
import release_notes as rn  # noqa: E402

PR = "https://github.com/cholmes/qgis_plugin_gpq_downloader/pull"

GITHUB_NOTES = f"""## What's Changed
* Support all three Overture divisions boundary types by @cholmes with @Copilot in {PR}/147
* Add MinIO URL support for dataset loading by @nils-grob in {PR}/122
* fix: read overturemaps new catalog json to obtain latest release by @lubosmato in {PR}/169
* Clear feature selection on AOI dialog exit by @mtravis in {PR}/159
* Make Cancel interrupt the running DuckDB query by @ll3an in {PR}/171
* Fix Source Cooperative presets, see #12 by @ll3an in {PR}/170
* Bump actions/checkout from 2 to 4 by @dependabot[bot] in {PR}/180
* Support QGIS 4 in the dev setup script by @cholmes in {PR}/173

## New Contributors
* @cholmes with @Copilot made their first contribution in {PR}/147
* @lubosmato made their first contribution in {PR}/169
* @ll3an made their first contribution in {PR}/170

**Full Changelog**: https://github.com/cholmes/qgis_plugin_gpq_downloader/compare/v0.8.6...v0.9.0
"""


@pytest.fixture
def notes():
    return rn.parse(GITHUB_NOTES)


def sections(notes):
    return {e.number: rn.classify(e) for e in notes.entries}


def test_parse_reads_entries_coauthors_and_skips_maintainer_as_new(notes):
    assert len(notes.entries) == 8
    first = notes.entries[0]
    assert (first.author, first.coauthor, first.number) == ("cholmes", "Copilot", 147)
    assert [a for a, _ in notes.new_contributors] == ["lubosmato", "ll3an"]
    assert notes.full_changelog.startswith("**Full Changelog**")


def test_classify_by_first_word_and_conventional_prefix(notes):
    assert sections(notes) == {
        147: "Added",
        122: "Added",
        169: "Fixed",
        159: "Fixed",
        171: "Changed",
        170: "Fixed",
        180: "Internal",
        173: "Added",
    }


def test_overrides_set_section_and_title(notes, tmp_path):
    path = tmp_path / "o.json"
    path.write_text(json.dumps({"_comment": "x", "171": {"section": "Fixed"}, "173": {"section": "Internal", "title": "Dev setup"}}))
    notes = rn.apply_overrides(notes, rn.load_overrides(path))
    got = sections(notes)
    assert got[171] == "Fixed" and got[173] == "Internal"
    assert next(e for e in notes.entries if e.number == 173).title == "Dev setup"


def test_overrides_reject_unknown_section(tmp_path):
    path = tmp_path / "o.json"
    path.write_text(json.dumps({"1": {"section": "Breaking"}}))
    with pytest.raises(rn.ReleaseNotesError):
        rn.load_overrides(path)


def test_display_title_strips_markdown_links_and_escapes_html():
    assert rn.display_title("fix: add [evil](https://phish.example) link") == "Add evil link"
    assert rn.display_title("Handle <img src=x onerror=x> input") == "Handle &lt;img src=x onerror=x> input"


def test_contributor_brief_includes_coauthored_prs():
    notes = rn.Notes(
        entries=[rn.Entry(title="Fix a thing", author="cholmes", number=5, coauthor="newbie")],
        new_contributors=[("newbie", f"{PR}/5")],
    )
    assert rn.contributor_brief(notes) == {"newbie": [(5, "Fix a thing")]}


def fake_run(returncode, stdout="", stderr=""):
    return lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout, stderr)


def test_latest_tag_none_only_when_no_release_exists(monkeypatch):
    monkeypatch.setattr(rn.subprocess, "run", fake_run(0, stdout="v0.8.6\n"))
    assert rn.latest_tag() == "v0.8.6"
    monkeypatch.setattr(rn.subprocess, "run", fake_run(1, stderr="release not found"))
    assert rn.latest_tag() is None
    monkeypatch.setattr(rn.subprocess, "run", fake_run(1, stderr="HTTP 404: Not Found"))
    assert rn.latest_tag() is None
    monkeypatch.setattr(rn.subprocess, "run", fake_run(1, stderr="HTTP 403: rate limit exceeded"))
    with pytest.raises(rn.ReleaseNotesError, match="rate limit"):
        rn.latest_tag()


def test_preview_prints_internal_bucket(monkeypatch, capsys):
    monkeypatch.setattr(rn, "latest_tag", lambda: "v0.8.6")
    monkeypatch.setattr(rn, "generate", lambda version, previous: rn.parse(GITHUB_NOTES))
    assert rn.main(["0.9.0"]) == 0
    out = capsys.readouterr().out
    assert "### Internal (not listed in the changelog)" in out
    assert "Bump actions/checkout" in out
    # the written section itself still omits it
    assert out.index("**Full Changelog**") < out.index("### Internal (not listed")


def test_render(notes):
    out = rn.render(notes, "0.9.0", "2026-10-15")
    assert out.startswith("## v0.9.0 (2026-10-15)\n\n" + rn.SUMMARY_PLACEHOLDER)
    assert out.index("### Added") < out.index("### Changed") < out.index("### Fixed")
    # conventional prefix stripped and capitalized; issue linked; co-author credited
    assert "- Read overturemaps new catalog json" in out
    assert "[#12](https://github.com/cholmes/qgis_plugin_gpq_downloader/issues/12)" in out
    assert "by [@cholmes](https://github.com/cholmes) with [@Copilot](https://github.com/Copilot)" in out
    # bot bump counted, not listed
    assert "checkout" not in out
    assert "_1 internal change is not listed here" in out
    assert "### New Contributors" in out and "@cholmes) made their first" not in out


def test_splice_replaces_unreleased_and_refuses_duplicates():
    log = "# Changelog\n\nIntro.\n\n## Unreleased\n\n- wip\n\n## v0.8.6 (2025-11-19)\n\nOld.\n"
    out = rn.splice(log, "## v0.9.0 (2026-10-15)\n\nNew.\n")
    assert "Unreleased" not in out and "wip" not in out
    assert out.index("## v0.9.0") < out.index("## v0.8.6")
    with pytest.raises(rn.ReleaseNotesError):
        rn.splice(out, "## v0.9.0 (2026-10-16)\n\nAgain.\n")
    # a beta section is not the final release's section
    beta = rn.splice(log, "## v0.9.0-beta.1 (2026-03-13)\n\nBeta.\n")
    assert "## v0.9.0 (" in rn.splice(beta, "## v0.9.0 (2026-10-15)\n\nNew.\n")


def test_splice_into_changelog_with_only_unreleased():
    out = rn.splice("# Changelog\n\n## Unreleased\n", "## v0.9.0 (2026-10-15)\n\nNew.\n")
    assert out == "# Changelog\n\n## v0.9.0 (2026-10-15)\n\nNew.\n"


def test_bump(tmp_path, monkeypatch):
    meta = tmp_path / "metadata.txt"
    meta.write_text("[general]\nname=x\nversion=0.8.6\nqgisMinimumVersion=3.16\n")
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "x"\nversion = "0.8.6"\n\n[tool.black]\ntarget-version = ["py37"]\n')
    monkeypatch.setattr(rn, "METADATA", meta)
    monkeypatch.setattr(rn, "PYPROJECT", pyproject)
    rn.bump("0.9.0")
    assert "version=0.9.0\n" in meta.read_text()
    assert 'version = "0.9.0"' in pyproject.read_text()
    assert 'target-version = ["py37"]' in pyproject.read_text()


SECTION = (
    "Summary.\n\n### Fixed\n\n"
    "- Fix [#12](https://x/issues/12) presets by [@ll3an](https://github.com/ll3an) in [#170](https://x)\n"
    "- Clear selection by [@mtravis](https://github.com/mtravis) in [#159](https://x)\n"
)


def test_changelog_section_and_plugin_manager_text():
    log = f"# Changelog\n\n## v0.9.0 (2026-10-15)\n\n{SECTION}\n## v0.8.6 (2025-11-19)\n\nOld.\n"
    section = build_plugin.changelog_section(log, "0.9.0")
    assert section.startswith("Summary.") and "Old." not in section
    assert build_plugin.changelog_section(log, "0.9") is None
    beta = "## v0.9.0-beta.1 (2026-03-13)\n\nBeta.\n"
    assert build_plugin.changelog_section(beta, "0.9.0") is None
    assert build_plugin.changelog_section(beta, "0.9.0-beta.1") == "Beta."
    assert build_plugin.plugin_manager_changelog("0.9.0", section) == (
        "0.9.0\n- Fix #12 presets\n- Clear selection"
    )


METADATA = """[general]
name=Test
version={version}
experimental=False
changelog=old
    - stale entry
supportsQt6=yes
about=Line one.
    Line two.
"""


@pytest.mark.parametrize("version, experimental", [("0.9.0", "False"), ("0.9.0-beta.1", "True")])
def test_release_metadata(version, experimental):
    out = build_plugin.release_metadata(METADATA.format(version=version), version, "0.9.0\n- A\n- B")
    cfg = configparser.ConfigParser()
    cfg.read_string(out)
    general = cfg["general"]
    assert general["experimental"] == experimental
    assert general["changelog"] == "0.9.0\n- A\n- B"
    assert general["about"] == "Line one.\nLine two."
    assert general["supportsQt6"] == "yes"
    assert "stale" not in out


def test_build_zip(tmp_path, monkeypatch):
    src = tmp_path / "gpq_downloader"
    (src / "tests").mkdir(parents=True)
    (src / "__pycache__").mkdir()
    (src / "__init__.py").write_text("")
    (src / "tests" / "test_x.py").write_text("")
    (src / "__pycache__" / "x.pyc").write_text("")
    (src / "metadata.txt").write_text("[general]\nname=T\nversion=1.0.0\n")
    (tmp_path / "LICENSE").write_text("GPL")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n")
    for name, value in (("ROOT", tmp_path), ("SOURCE", src), ("METADATA", src / "metadata.txt"), ("CHANGELOG", changelog)):
        monkeypatch.setattr(build_plugin, name, value)

    with pytest.raises(SystemExit):
        build_plugin.build(tmp_path / "dist", require_changelog=True)

    changelog.write_text(f"# Changelog\n\n## v1.0.0 (2026-10-15)\n\n{SECTION}")
    path = build_plugin.build(tmp_path / "dist", require_changelog=True)
    assert path.name == "qgis_plugin_gpq_downloader-1.0.0.zip"
    names = zipfile.ZipFile(path).namelist()
    assert sorted(names) == [
        "qgis_plugin_gpq_downloader/LICENSE",
        "qgis_plugin_gpq_downloader/__init__.py",
        "qgis_plugin_gpq_downloader/metadata.txt",
    ]
    meta = zipfile.ZipFile(path).read("qgis_plugin_gpq_downloader/metadata.txt").decode()
    assert "experimental=False" in meta and "- Clear selection" in meta


def test_build_zip_from_git_listing_skips_untracked_and_symlinks(tmp_path, monkeypatch):
    src = tmp_path / "gpq_downloader"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "metadata.txt").write_text("[general]\nname=T\nversion=1.0.0\n")
    (src / "link.py").symlink_to("__init__.py")
    (tmp_path / "LICENSE").write_text("GPL")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(f"# Changelog\n\n## v1.0.0 (2026-10-15)\n\n{SECTION}")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    (src / ".env").write_text("SECRET=1")  # stray untracked file must not ship
    for name, value in (("ROOT", tmp_path), ("SOURCE", src), ("METADATA", src / "metadata.txt"), ("CHANGELOG", changelog)):
        monkeypatch.setattr(build_plugin, name, value)

    path = build_plugin.build(tmp_path / "dist", require_changelog=True)
    assert sorted(zipfile.ZipFile(path).namelist()) == [
        "qgis_plugin_gpq_downloader/LICENSE",
        "qgis_plugin_gpq_downloader/__init__.py",
        "qgis_plugin_gpq_downloader/metadata.txt",
    ]


def test_build_fails_clearly_without_license_or_general_section(tmp_path, monkeypatch):
    src = tmp_path / "gpq_downloader"
    src.mkdir()
    (src / "metadata.txt").write_text("[general]\nname=T\nversion=1.0.0\n")
    for name, value in (("ROOT", tmp_path), ("SOURCE", src), ("METADATA", src / "metadata.txt"), ("CHANGELOG", tmp_path / "CHANGELOG.md")):
        monkeypatch.setattr(build_plugin, name, value)
    with pytest.raises(SystemExit, match="LICENSE"):
        build_plugin.build(tmp_path / "dist", require_changelog=False)
    with pytest.raises(SystemExit, match=r"\[general\]"):
        build_plugin.release_metadata("name=T\nversion=1.0.0\n", "1.0.0", None)


def test_print_notes(tmp_path, monkeypatch, capsys):
    meta = tmp_path / "metadata.txt"
    meta.write_text("[general]\nname=T\nversion=0.9.0\n")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(f"# Changelog\n\n## v0.9.0 (2026-10-15)\n\n{SECTION}\n## v0.8.6 (2025-11-19)\n\nOld.\n")
    monkeypatch.setattr(build_plugin, "METADATA", meta)
    monkeypatch.setattr(build_plugin, "CHANGELOG", changelog)
    assert build_plugin.main(["--print-notes"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Summary.")
    assert "Old." not in out
    assert "Full changelog: https://github.com/cholmes/qgis_plugin_gpq_downloader/blob/main/CHANGELOG.md" in out
    changelog.write_text("# Changelog\n")
    with pytest.raises(SystemExit, match="no '## v0.9.0' section"):
        build_plugin.main(["--print-notes"])

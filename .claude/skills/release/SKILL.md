---
name: release
description: Use when cutting a GeoParquet Downloader plugin release - builds the CHANGELOG section from merged PRs, writes the highlights, stops for human review, then opens the version-bump PR whose merge publishes to GitHub and plugins.qgis.org.
---

# Releasing the GeoParquet Downloader plugin

Every release section of `CHANGELOG.md` is generated, never hand-written: GitHub's
own list of merged pull requests, one line each with author and number, grouped into
Added / Changed / Fixed / Documentation, under two to four paragraphs of highlights
that you write.

Publishing is automatic. When a change to the `version=` line in
`gpq_downloader/metadata.txt` lands on `main`, `.github/workflows/release.yml` runs
the tests, builds the zip, tags `v<version>`, creates the GitHub release with the
CHANGELOG section as its notes, and uploads the zip to plugins.qgis.org.

**There is a mandatory human review checkpoint at step 5. Nothing is pushed before
the user approves the changelog section.**

## 1. Preflight

```bash
git checkout main && git pull
gh run list --branch main --limit 3      # main must be green
git status --short                       # must be clean
gh release list --limit 3                # what the last release was
```

Pick the version. This plugin is pre-1.0: new capabilities or presets mean a minor
bump (0.9.0 → 0.10.0), fixes alone mean a patch. A version with a hyphen
(`0.10.0-beta.1`) is a pre-release: it becomes a GitHub pre-release and is **not**
uploaded to plugins.qgis.org unless the workflow is run by hand with
`upload_prerelease` ticked, in which case it goes up flagged experimental.

## 2. Generate the section

```bash
python3 scripts/release_notes.py <version>            # preview
python3 scripts/release_notes.py <version> --write    # replace "## Unreleased" in CHANGELOG.md
```

The range starts at the latest full GitHub release, so betas are folded into the
final release. Pass `--previous v<tag>` to start elsewhere. `--write` refuses to
write a version the changelog already has; delete that section to regenerate.

## 3. Check every entry's section

Titles here are plain sentences, so the section comes from the first word ("Add",
"Support" → Added; "Fix", "Clear" → Fixed; anything else → Changed). Read each entry
and fix misfiles in `scripts/release_overrides.json`, keyed by PR number, then
regenerate. Do not edit the generated bullets by hand.

```json
"171": {"section": "Fixed"},
"154": {"section": "Internal"},
"169": {"title": "Read Overture's STAC catalog to find the latest release"}
```

- `Internal` is for dev tooling, CI and repo chores. It is counted but not listed.
- Use `title` when a PR title will not make sense to a QGIS user.

## 4. Write the highlights

Replace the `<!-- TODO ... -->` placeholder with two to four paragraphs, written
after reading the entries and, where needed, the PRs themselves. Cover, in order:

1. What is newly possible: new presets, sources, AOI tools, output options.
2. The theme of the fixes, named concretely ("the dialog crashed on QGIS 4"), not
   "various bug fixes".
3. Anything a user has to do differently: a raised QGIS or DuckDB minimum, a preset
   that moved, a changed default.
4. **Every first-time contributor, by name, with what they contributed.**

The last paragraph is not optional. Get the material from:

```bash
python3 scripts/release_notes.py <version> --contributors
```

For each person, say how many PRs they sent when it was more than one, summarize the
substance of the work in a sentence or two so they can see it was read, thank them,
and invite them back.

Write for a QGIS user deciding whether to upgrade, not for a developer.

## 5. Human review — STOP HERE

Show the user the rendered section and wait for explicit approval. Ask them to check:

- the version number
- the summary paragraphs
- any entry you moved with an override

## 6. Open the release PR

```bash
git checkout -b release/v<version>
python3 scripts/release_notes.py <version> --bump   # metadata.txt + pyproject.toml
python3 scripts/build_plugin.py --require-changelog # sanity check: builds dist/*.zip
git add CHANGELOG.md gpq_downloader/metadata.txt pyproject.toml scripts/release_overrides.json
git commit -m "Release v<version>"
git push -u origin release/v<version>
gh pr create --title "Release v<version>" --body-file <(sed -n '/^## v<version> /,/^## v/p' CHANGELOG.md | sed '$d')
```

Merging the PR fires the release workflow. Watch it:

```bash
gh run list --workflow release.yml --limit 1
gh run watch <run-id>
```

## 7. Verify

```bash
gh release view v<version> --json assets,isPrerelease --jq '{pre: .isPrerelease, assets: [.assets[].name]}'
```

The release carries one asset, `qgis_plugin_gpq_downloader-<version>.zip`. On
plugins.qgis.org the new version first sits in a security scan; once approved it
appears on https://plugins.qgis.org/plugins/qgis_plugin_gpq_downloader/ and in the
QGIS Plugin Manager, whose Changelog tab shows this release's entries.

## If something fails

- **Tests or the build fail**: nothing was tagged. Fix on `main` with a normal PR,
  then run the workflow by hand: `gh workflow run release.yml --ref main`.
- **The plugins.qgis.org upload fails** after the GitHub release exists (expired
  token, scan rejection, site down): fix the cause, then
  `gh workflow run release.yml --ref main -f upload_existing=true`. It re-uploads
  the zip attached to the existing GitHub release.
- **The token**: a per-plugin token from the plugin's page on plugins.qgis.org
  ("Tokens"), stored as the `QGIS_PLUGIN_TOKEN` secret on the `plugins-qgis-org`
  environment: `gh secret set QGIS_PLUGIN_TOKEN --env plugins-qgis-org`.

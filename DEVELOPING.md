# Developing the GeoParquet Downloader Plugin

## Quick Start

### 1. Link the plugin to QGIS

Instead of copying files into QGIS manually, create a symlink so QGIS loads the code directly from your checkout:

```bash
bash scripts/setup-dev.sh        # every QGIS version with a profile (QGIS 3 and/or QGIS 4)
bash scripts/setup-dev.sh 4      # only QGIS 4 (or 3, or all)
```

This replaces any installed copy in the default profile with a symlink. Your edits are immediately available to QGIS. QGIS 3 and QGIS 4 keep separate settings directories (`QGIS3/` and `QGIS4/`), so link each version you test with.

### 2. Install Plugin Reloader

In QGIS, go to **Plugins > Manage and Install Plugins**, search for **Plugin Reloader**, and install it. This lets you reload the plugin in one click without restarting QGIS.

Configure it to reload `qgis_plugin_gpq_downloader`:
- Go to **Plugins > Plugin Reloader > Configure**
- Select the plugin from the dropdown

### 3. Development cycle

1. Edit code in your editor
2. In QGIS, press the Plugin Reloader button (or **Plugins > Plugin Reloader > Reload**)
3. Test your changes

No file copying. No QGIS restart (in most cases).

**Note:** Changes to `__init__.py` or adding new dependencies require a full QGIS restart.

## Testing a PR

```bash
# Fetch and checkout the PR
git fetch origin pull/<PR_NUMBER>/head:pr-<PR_NUMBER>
git checkout pr-<PR_NUMBER>

# In QGIS, reload the plugin — done
```

Since the symlink points to your repo, switching branches instantly updates what QGIS sees.

## Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# The headless tests (no QGIS needed)
pytest tests/core --no-cov

# The in-QGIS tests
pytest tests/qgis

# With coverage
pytest --cov=gpq_downloader tests/qgis
```

Tests require a QGIS environment. The CI runs them inside a QGIS Docker container (see `.github/workflows/tests.yml`).

## Linting and the Security Scan

CI runs two checks besides the tests, both quick to reproduce locally:

```bash
# Unused imports, undefined names, bare excepts
pip install ruff==0.17.1 && ruff check --select F,E9,E722 gpq_downloader/ scripts/ tests/

# The same bandit scan plugins.qgis.org runs on an uploaded plugin, over what
# the zip ships. Its result is published on the plugin's security tab, so a
# finding that lands on main is public before a release is even cut.
pip install bandit==1.9.4 && bandit -r gpq_downloader/
```

Bandit cannot see that the queries in `utils.py` and `qgis_io/worker.py` quote
everything through `sql_str()`/`sql_ident()`, or that the pip command in
`qgis_io/duckdb_setup.py` never touches a shell, so those lines carry a
`# nosec <id>` comment with the reason directly above. Waive a finding that
way, in the source, rather than by configuring bandit: plugins.qgis.org scans
with its own config and will only honour the comments. Check what the line
actually interpolates first.

## Project Structure

```
qgis_plugin_gpq_downloader/       # repo root
├── gpq_downloader/               # plugin source (this is what QGIS loads)
│   ├── __init__.py               # plugin entry point (classFactory)
│   ├── plugin.py                 # main plugin class
│   ├── dialog.py                 # UI dialog
│   ├── utils.py                  # download/validation workers
│   ├── logger.py                 # logging utility
│   ├── metadata.txt              # QGIS plugin metadata & version
│   ├── data/presets.json         # pre-configured data sources
│   └── icons/                    # plugin icons
├── tests/
│   ├── core/                     # headless tests, plain Python
│   └── qgis/                     # tests that need the real QGIS
├── scripts/setup-dev.sh          # dev environment setup
├── make_release.sh               # build the release zip into dist/
├── scripts/build_plugin.py       # zip builder used by the release workflow
├── scripts/release_notes.py      # CHANGELOG section generator
├── CHANGELOG.md                  # release notes
├── pyproject.toml                # Python project config
└── DEVELOPING.md                 # this file
```

**Naming note:** The source directory is `gpq_downloader/` but QGIS knows the plugin as `qgis_plugin_gpq_downloader` (the release script handles the rename). The setup script creates the symlink with the correct name.

## Making a Release

Releases are published by `.github/workflows/release.yml` whenever a change to the
`version=` line in `gpq_downloader/metadata.txt` lands on `main`. It runs the tests,
builds the zip, tags `v<version>`, creates the GitHub release with that version's
`CHANGELOG.md` section as the notes, and uploads the zip to plugins.qgis.org.

1. Generate the changelog section from the merged PRs, then replace its summary
   placeholder with a few paragraphs of highlights (and thank new contributors):
   ```bash
   python3 scripts/release_notes.py 0.10.0 --write
   ```
   If an entry lands in the wrong section, fix it in `scripts/release_overrides.json`
   and regenerate.
2. Set the version and open a PR titled `Release v0.10.0`:
   ```bash
   python3 scripts/release_notes.py 0.10.0 --bump
   ```
3. Merge it. A version with a hyphen (`0.10.0-beta.1`) becomes a GitHub pre-release
   and is not sent to plugins.qgis.org unless you run the workflow by hand with
   `upload_prerelease`.

The full checklist, including recovery steps, is in
[`.claude/skills/release/SKILL.md`](.claude/skills/release/SKILL.md); with Claude Code,
ask it to "cut a release".

To build the zip locally without releasing: `bash make_release.sh` (writes `dist/`).

**One-time setup:** create an upload token on the plugin's page at
plugins.qgis.org ("Tokens"), then
`gh secret set QGIS_PLUGIN_TOKEN --env plugins-qgis-org`. Then restrict the
environment to main so no other branch's workflow can read the token: GitHub
Settings → Environments → `plugins-qgis-org` → Deployment branches: `main` only
(or `gh api -X PUT repos/<owner>/<repo>/environments/plugins-qgis-org` with a
custom branch policy for `main`).

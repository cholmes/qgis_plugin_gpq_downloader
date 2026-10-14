# Developing the GeoParquet Downloader Plugin

## Quick Start

### 1. Link the plugin to QGIS

Instead of copying files into QGIS manually, create a symlink so QGIS loads the code directly from your checkout:

```bash
bash scripts/setup-dev.sh
```

This replaces any installed copy with a symlink. Your edits are immediately available to QGIS.

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

# Run tests
pytest gpq_downloader/tests/

# With coverage
pytest --cov=gpq_downloader gpq_downloader/tests/
```

Tests require a QGIS environment. The CI runs them inside a QGIS Docker container (see `.github/workflows/tests.yml`).

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
│   ├── icons/                    # plugin icons
│   └── tests/                    # test suite
├── scripts/setup-dev.sh          # dev environment setup
├── make_release.sh               # build release zip
├── pyproject.toml                # Python project config
└── DEVELOPING.md                 # this file
```

**Naming note:** The source directory is `gpq_downloader/` but QGIS knows the plugin as `qgis_plugin_gpq_downloader` (the release script handles the rename). The setup script creates the symlink with the correct name.

## Making a Release

```bash
bash make_release.sh
```

This creates a zip with the version from `metadata.txt`, ready to upload to the QGIS Plugin Repository.

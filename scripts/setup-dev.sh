#!/bin/bash
#
# Sets up a symlink so QGIS loads the plugin directly from this repo.
# After running this, code edits are immediately available in QGIS
# (just reload the plugin — no manual file copying needed).
#

set -e

PLUGIN_NAME="qgis_plugin_gpq_downloader"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_DIR="${REPO_DIR}/gpq_downloader"

# Detect QGIS plugins directory
if [[ "$OSTYPE" == "darwin"* ]]; then
    PLUGINS_DIR="$HOME/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins"
elif [[ "$OSTYPE" == "linux"* ]]; then
    PLUGINS_DIR="$HOME/.local/share/QGIS/QGIS3/profiles/default/python/plugins"
else
    echo "Error: Unsupported platform. On Windows, create the symlink manually:"
    echo "  mklink /D \"%APPDATA%\\QGIS\\QGIS3\\profiles\\default\\python\\plugins\\${PLUGIN_NAME}\" \"${SOURCE_DIR}\""
    exit 1
fi

LINK_PATH="${PLUGINS_DIR}/${PLUGIN_NAME}"

echo "Source:  ${SOURCE_DIR}"
echo "Target:  ${LINK_PATH}"
echo ""

# Check source exists
if [ ! -d "$SOURCE_DIR" ]; then
    echo "Error: Source directory not found: ${SOURCE_DIR}"
    exit 1
fi

# Handle existing installation
if [ -L "$LINK_PATH" ]; then
    EXISTING_TARGET="$(readlink "$LINK_PATH")"
    if [ "$EXISTING_TARGET" = "$SOURCE_DIR" ]; then
        echo "Symlink already exists and points to the right place. Nothing to do."
        exit 0
    fi
    echo "Existing symlink points to: ${EXISTING_TARGET}"
    echo "Replacing with link to this repo."
    rm "$LINK_PATH"
elif [ -d "$LINK_PATH" ]; then
    BACKUP="${LINK_PATH}.backup.$(date +%Y%m%d%H%M%S)"
    echo "Found existing plugin installation (not a symlink)."
    echo "Backing up to: ${BACKUP}"
    mv "$LINK_PATH" "$BACKUP"
elif [ ! -d "$PLUGINS_DIR" ]; then
    echo "Creating plugins directory: ${PLUGINS_DIR}"
    mkdir -p "$PLUGINS_DIR"
fi

# Create the symlink
ln -s "$SOURCE_DIR" "$LINK_PATH"
echo "Symlink created."
echo ""
echo "Next steps:"
echo "  1. Restart QGIS (or reload the plugin if you have Plugin Reloader installed)"
echo "  2. Install 'Plugin Reloader' from the QGIS Plugin Manager for fast reloads"
echo "     (Plugins > Manage and Install Plugins > search 'Plugin Reloader')"
echo ""
echo "See DEVELOPING.md for the full development workflow."

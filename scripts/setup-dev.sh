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

usage() {
    echo "Usage: $0 [3|4|all]"
    echo "  Link the plugin into the QGIS 3 and/or QGIS 4 default profile."
    echo "  Without an argument, links every QGIS version that has a profile directory."
}

# QGIS 3 and QGIS 4 keep separate settings directories (QGIS3, QGIS4)
if [[ "$OSTYPE" == "darwin"* ]]; then
    QGIS_SETTINGS_DIR="$HOME/Library/Application Support/QGIS"
elif [[ "$OSTYPE" == "linux"* ]]; then
    QGIS_SETTINGS_DIR="$HOME/.local/share/QGIS"
else
    echo "Error: Unsupported platform. On Windows, create the symlink manually, e.g. for QGIS 4:"
    echo "  mklink /D \"%APPDATA%\\QGIS\\QGIS4\\profiles\\default\\python\\plugins\\${PLUGIN_NAME}\" \"${SOURCE_DIR}\""
    echo "  (use QGIS3 instead of QGIS4 for QGIS 3)"
    exit 1
fi

case "${1:-}" in
    3) VERSIONS="3" ;;
    4) VERSIONS="4" ;;
    all) VERSIONS="3 4" ;;
    "")
        VERSIONS=""
        for v in 3 4; do
            [ -d "${QGIS_SETTINGS_DIR}/QGIS${v}/profiles" ] && VERSIONS="${VERSIONS} ${v}"
        done
        if [ -z "$VERSIONS" ]; then
            echo "No QGIS 3 or QGIS 4 profile found under ${QGIS_SETTINGS_DIR}."
            echo "Start QGIS once to create a profile, or pass 3 or 4 explicitly."
            exit 1
        fi
        ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 1 ;;
esac

# Check source exists
if [ ! -d "$SOURCE_DIR" ]; then
    echo "Error: Source directory not found: ${SOURCE_DIR}"
    exit 1
fi

link_plugin() {
    local plugins_dir="${QGIS_SETTINGS_DIR}/QGIS$1/profiles/default/python/plugins"
    local link_path="${plugins_dir}/${PLUGIN_NAME}"

    echo "QGIS $1"
    echo "  Source:  ${SOURCE_DIR}"
    echo "  Target:  ${link_path}"

    # Handle existing installation
    if [ -L "$link_path" ]; then
        local existing_target
        existing_target="$(readlink "$link_path")"
        if [ "$existing_target" = "$SOURCE_DIR" ]; then
            echo "  Symlink already points to this repo. Nothing to do."
            return
        fi
        echo "  Existing symlink points to: ${existing_target}"
        echo "  Replacing with link to this repo."
        rm "$link_path"
    elif [ -d "$link_path" ]; then
        local backup="${link_path}.backup.$(date +%Y%m%d%H%M%S)"
        echo "  Found existing plugin installation (not a symlink)."
        echo "  Backing up to: ${backup}"
        mv "$link_path" "$backup"
    elif [ ! -d "$plugins_dir" ]; then
        echo "  Creating plugins directory: ${plugins_dir}"
        mkdir -p "$plugins_dir"
    fi

    ln -s "$SOURCE_DIR" "$link_path"
    echo "  Symlink created."
}

for v in $VERSIONS; do
    link_plugin "$v"
    echo ""
done

echo "Next steps:"
echo "  1. Restart QGIS (or reload the plugin if you have Plugin Reloader installed)"
echo "  2. Install 'Plugin Reloader' from the QGIS Plugin Manager for fast reloads"
echo "     (Plugins > Manage and Install Plugins > search 'Plugin Reloader')"
echo ""
echo "See DEVELOPING.md for the full development workflow."

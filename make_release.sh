#!/bin/bash

# Function to extract version from metadata.txt
get_version_from_metadata() {
  if [ -f "gpq_downloader/metadata.txt" ]; then
    VERSION=$(grep "^version=" gpq_downloader/metadata.txt | cut -d'=' -f2 | tr -d '[:space:]')
    if [ -n "$VERSION" ]; then
      echo "Found version $VERSION in metadata.txt"
      return 0
    fi
  fi
  echo "Warning: Could not extract version from metadata.txt"
  return 1
}

# Get version from command line argument or metadata.txt or use date
if [ -n "$1" ]; then
  VERSION=$1
  echo "Using provided version: $VERSION"
else
  if ! get_version_from_metadata; then
    VERSION=$(date +"%Y%m%d")
    echo "Using date-based version: $VERSION"
  fi
fi

ZIP_FILENAME="gpq_downloader_${VERSION}.zip"

echo "Creating release zip: ${ZIP_FILENAME}"

# Make sure LICENSE is in both directories
echo "Copying LICENSE to both directories..."
cp -f LICENSE qgis_plugin_gpq_downloader/ 2>/dev/null || echo "LICENSE already exists in qgis_plugin_gpq_downloader/"
cp -f LICENSE gpq_downloader/ 2>/dev/null || echo "LICENSE already exists in gpq_downloader/"

# Create zip file excluding unwanted files
zip -r "${ZIP_FILENAME}" qgis_plugin_gpq_downloader/ gpq_downloader/ \
  -x "*.DS_Store" "*.gitignore" "*/.git/*" "*/__pycache__/*" "*.pyc" "*.pyo" "*.zip"

# Clean up the copied LICENSE files
echo "Cleaning up copied LICENSE files..."
rm -f qgis_plugin_gpq_downloader/LICENSE 2>/dev/null
rm -f gpq_downloader/LICENSE 2>/dev/null

echo "Release zip created: ${ZIP_FILENAME}"
echo "You can now upload this file to the QGIS Plugin Repository." 
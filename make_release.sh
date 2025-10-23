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
TEMP_DIR=$(mktemp -d)

echo "Creating release zip: ${ZIP_FILENAME}"

# Create a temporary directory with the renamed plugin
echo "Creating temporary directory with renamed plugin..."
cp -r gpq_downloader/ "${TEMP_DIR}/qgis_plugin_gpq_downloader"

# Copy LICENSE file if it exists
if [ -f "LICENSE" ]; then
  echo "Copying LICENSE file..."
  cp LICENSE "${TEMP_DIR}/qgis_plugin_gpq_downloader/"
else
  echo "Warning: LICENSE file not found"
fi

# Navigate to the temp directory
cd "${TEMP_DIR}"

# Create zip file excluding unwanted files
echo "Creating zip file..."
zip -r "${ZIP_FILENAME}" qgis_plugin_gpq_downloader/ \
  -x "*.DS_Store" "*.gitignore" "*/.git/*" "*/__pycache__/*" "*.pyc" "*.pyo" "*.zip" "*/tests/*"

# Move the zip file back to the original directory
mv "${ZIP_FILENAME}" "${OLDPWD}/"

# Clean up
cd "${OLDPWD}"
rm -rf "${TEMP_DIR}"

echo "Release zip created: ${ZIP_FILENAME}"
echo "You can now upload this file to the QGIS Plugin Repository." 
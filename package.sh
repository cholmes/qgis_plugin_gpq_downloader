#!/bin/bash

# Define the output zip filename
ZIP_NAME="qgis_plugin_gpq_downloader.zip"

# Remove any existing zip file
rm -f "$ZIP_NAME"

# Create the zip, excluding unnecessary files including this script
zip -r "$ZIP_NAME" * -x ".git*" ".github*" "release*" "*.zip" "package.sh" 
# Print success message
echo "Created $ZIP_NAME"

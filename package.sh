#!/bin/bash
# Remove any existing zip file
rm gpq_downloader.zip 

# Create the zip, excluding unnecessary files including this script
zip -r gpq_downloader.zip gpq_downloader/ -x "*.DS_Store" "*.gitignore" "*/.git/*"


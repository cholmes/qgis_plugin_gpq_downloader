#!/bin/bash
# Build the plugin zip into dist/. Releases are published by
# .github/workflows/release.yml; see DEVELOPING.md "Making a Release".
exec python3 "$(dirname "$0")/scripts/build_plugin.py" "$@"

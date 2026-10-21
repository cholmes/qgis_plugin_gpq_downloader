"""The preset datasets the dialog offers, read from data/presets.json.

The dialog and the ValidationWorker each read the file inline; this is that
code as one function (#195), with the path resolved from the package root
rather than from the module that read it.
"""

import json
import os


def load_presets():
    """The parsed contents of data/presets.json."""
    base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    presets_path = os.path.join(base_path, "data", "presets.json")
    with open(presets_path, "r") as f:
        return json.load(f)

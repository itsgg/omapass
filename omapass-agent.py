#!/usr/bin/env python3
"""OmaPass helper: the backend for the Omarchy 1Password bar widget.

This file is only the entry point; the implementation lives in the omapass
package beside it. See omapass/__init__.py for what each module owns.

    ./omapass-agent.py status
    ./omapass-agent.py list "github"
    echo '{"action": "status"}' | ./omapass-agent.py request -
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from omapass import paths
from omapass.cli import main

# The daemon respawns itself by path, so it has to know which file was run.
paths.set_entry_script(__file__)

if __name__ == "__main__":
    main()

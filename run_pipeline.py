#!/usr/bin/env python
"""
Main execution script for Video-to-Transcript Data Pipeline.
"""

import sys
from pathlib import Path

# Ensure UTF-8 output handling on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure package directory is on sys.path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from pipeline.cli import run_cli

if __name__ == "__main__":
    run_cli()

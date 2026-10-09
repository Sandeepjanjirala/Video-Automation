#!/usr/bin/env python3
"""CLI utility to execute or rehearse SQLite to PostgreSQL migration for Telugu Reel Studio."""
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from studio.importer import main

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Run the local read-only dashboard for the ORB bot.

Usage:
    python scripts/run_dashboard.py [--config config.yaml] [--port 8765]

Polls the same status.json / trades.csv the live bot writes (see
scripts/run_live.py) - run this alongside the bot, not instead of it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orb_bot.dashboard.app import main

if __name__ == "__main__":
    main()

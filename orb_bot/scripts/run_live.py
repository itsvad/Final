#!/usr/bin/env python3
"""Run the ORB bot live against Tradovate (demo or live, per config.yaml).

Usage:
    python scripts/run_live.py [--config config.yaml] [--env .env]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orb_bot.config import load_config
from orb_bot.live_runner import LiveRunner


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ORB bot live on Tradovate")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--env", default=".env", help="Path to .env with Tradovate credentials")
    args = parser.parse_args()

    config = load_config(args.config, args.env)
    LiveRunner(config).run()


if __name__ == "__main__":
    main()

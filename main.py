"""
Local / cron entrypoint.

Usage:
  python main.py --once           # run one cycle (live)
  python main.py --once --dry-run # run one cycle, print instead of posting to Slack
"""
from __future__ import annotations

import argparse
import logging
import sys

from config import load_config
from pipeline import run_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="insider-agent: polls SEC EDGAR Form 4 for insider purchase signals."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one poll cycle and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print signals to stdout instead of posting to Slack.",
    )
    args = parser.parse_args()

    if not args.once:
        parser.print_help()
        print("\nError: --once is required (continuous mode not yet implemented).")
        return 1

    try:
        cfg = load_config()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    notified = run_once(cfg, dry_run=args.dry_run)
    logger.info("Done. %d signal(s) processed.", notified)
    return 0


if __name__ == "__main__":
    sys.exit(main())

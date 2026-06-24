"""
GCP Cloud Functions gen2 HTTP entrypoint.

Deploy with functions-framework:
  functions-framework --target=run_pipeline --port=8080

Or via gcloud (see README / Makefile for full deploy commands).
"""
from __future__ import annotations

import logging

import functions_framework  # type: ignore

from config import load_config
from pipeline import run_once

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@functions_framework.http
def run_pipeline(request):
    """HTTP Cloud Function — triggered by Cloud Scheduler every 15 min."""
    dry_run = request.args.get("dry_run", "false").lower() in ("1", "true", "yes")

    try:
        cfg = load_config()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return (f"Configuration error: {exc}", 500)

    try:
        notified = run_once(cfg, dry_run=dry_run)
        msg = f"OK — {notified} signal(s) notified."
        logger.info(msg)
        return (msg, 200)
    except Exception as exc:
        logger.exception("Pipeline error: %s", exc)
        return (f"Pipeline error: {exc}", 500)

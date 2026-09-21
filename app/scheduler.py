from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import CLOSEOUT_MINUTES, HOOK_POLL_SECONDS, SCRAPE_INTERVAL_HOURS
from app.hooks import assign_hooks_for_active, closeout_ended, fire_due_hooks
from app.scraper import scrape_once

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _run_scrape,
        IntervalTrigger(hours=SCRAPE_INTERVAL_HOURS),
        id="catalog_scan",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
        next_run_time=datetime.now(timezone.utc),
    )
    scheduler.add_job(
        _run_hooks,
        IntervalTrigger(seconds=HOOK_POLL_SECONDS),
        id="auction_hooks",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        closeout_ended,
        IntervalTrigger(minutes=CLOSEOUT_MINUTES),
        id="closeout",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def _run_scrape() -> None:
    logger.info("Catalog scan starting")
    try:
        result = scrape_once()
    except Exception:
        logger.exception("Catalog scan failed")
        raise
    logger.info("Catalog scan finished: %s", result)


def _run_hooks() -> None:
    assign_hooks_for_active()
    fire_due_hooks()


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None

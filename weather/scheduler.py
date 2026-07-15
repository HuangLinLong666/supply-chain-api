"""Optional in-process APScheduler integration."""

import logging
from typing import Any

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:
    BackgroundScheduler = None  # type: ignore[assignment]

from weather.config import WeatherSettings
from weather.service import update_ports

_scheduler: Any | None=None

def start_scheduler() -> None:
    global _scheduler
    settings=WeatherSettings()
    if not settings.scheduler_enabled or _scheduler: return
    if BackgroundScheduler is None:
        logging.getLogger(__name__).warning("APScheduler is not installed; automatic weather updates are disabled")
        return
    _scheduler=BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(update_ports,"interval",minutes=settings.update_interval_minutes,id="port-weather-update",max_instances=1,coalesce=True,replace_existing=True)
    _scheduler.start()

def stop_scheduler() -> None:
    global _scheduler
    if _scheduler: _scheduler.shutdown(wait=False); _scheduler=None

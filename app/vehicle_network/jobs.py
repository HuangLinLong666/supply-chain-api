from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from apscheduler.schedulers.background import BackgroundScheduler

from app.vehicle_network.models import LocationIngestRequest
from app.vehicle_network.services import LocationIngestionService


logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def refresh_locations_job() -> None:
    """简化版定时任务；生产环境可将同一服务调用迁移到 Celery worker。"""
    trace_id = f"scheduled_{uuid4().hex}"
    try:
        asyncio.run(LocationIngestionService().ingest(LocationIngestRequest(), trace_id))
    except Exception:
        logger.exception("定时地点更新失败 trace_id=%s", trace_id)


def start_vehicle_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(refresh_locations_job, "cron", hour=2, id="vehicle_location_refresh", replace_existing=True)
    _scheduler.start()


def stop_vehicle_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None

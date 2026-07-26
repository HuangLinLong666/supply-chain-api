from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ais.config import AisSettings
from ais.repository import AisRepository


router = APIRouter(tags=["AIS Port Traffic"])


def _repository() -> AisRepository:
    return AisRepository()


def _database_call(callback):
    try:
        return callback()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/health/ais", summary="AISStream provider health")
def ais_health() -> dict[str, Any]:
    settings = AisSettings.from_environment()
    return _database_call(lambda: _repository().provider_status(settings))


@router.get("/api/providers/status", summary="External provider health statuses")
def provider_statuses() -> dict[str, Any]:
    settings = AisSettings.from_environment()
    providers = _database_call(lambda: _repository().provider_statuses(settings))
    return {"count": len(providers), "providers": providers}


@router.get("/api/ais/targets", summary="AIS observation target areas")
def ais_targets() -> dict[str, Any]:
    targets = _database_call(lambda: _repository().list_targets())
    return {"count": len(targets), "targets": targets}


@router.get("/api/ais/targets/{target_id}/traffic", summary="Latest target traffic aggregate")
def target_traffic(target_id: str) -> dict[str, Any]:
    result = _database_call(lambda: _repository().target_traffic(target_id))
    if result is None:
        raise HTTPException(status_code=404, detail="AIS observation target not found")
    return result


@router.get("/api/ports/{port_id}/traffic", summary="Latest observed port traffic")
def port_traffic(port_id: str) -> dict[str, Any]:
    result = _database_call(lambda: _repository().port_traffic(port_id))
    if result is None:
        raise HTTPException(status_code=404, detail="Port not found")
    return result


@router.get("/api/vessels/{mmsi}", summary="Latest AIS vessel state")
def vessel(mmsi: str) -> dict[str, Any]:
    if not mmsi.isdigit() or len(mmsi) != 9:
        raise HTTPException(status_code=422, detail="MMSI must be exactly nine digits")
    result = _database_call(lambda: _repository().vessel(mmsi))
    if result is None:
        raise HTTPException(status_code=404, detail="Vessel not found")
    return result

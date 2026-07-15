"""Port weather update orchestration."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from weather.client import OpenMeteoClient
from weather.config import WeatherSettings, load_rules
from weather.repository import ensure_schema, list_ports, write_weather
from weather.risk import calculate_risk

LOGGER=logging.getLogger(__name__)
UPDATE_LOCK=threading.Lock()


def hourly_rows(payload: dict[str, Any], marine: dict[str, Any]) -> list[dict[str, Any]]:
    hourly=payload.get("hourly",{}); marine_hourly=marine.get("hourly",{})
    rows=[]
    for index,_ in enumerate(hourly.get("time",[])[:24]):
        row={key:(values[index] if index<len(values) else None) for key,values in hourly.items() if key!="time" and isinstance(values,list)}
        for key,values in marine_hourly.items():
            if key!="time" and isinstance(values,list): row[key]=values[index] if index<len(values) else None
        rows.append(row)
    return rows


def update_ports(port_ids: list[str] | None=None, force: bool=False, dry_run: bool=False, client: OpenMeteoClient | None=None) -> dict[str, Any]:
    if not UPDATE_LOCK.acquire(blocking=False): raise RuntimeError("Weather update is already running")
    started=datetime.now(timezone.utc); job_id=str(uuid.uuid4()); settings=WeatherSettings(); own=client is None; client=client or OpenMeteoClient(settings)
    summary={"job_id":job_id,"started_at":started.isoformat(),"ports_scanned":0,"ports_skipped":0,"weather_requests":0,"marine_requests":0,"successful_ports":0,"failed_ports":0,"geocoding_required":0,"geocoding_unresolved":0,"neo4j_records_written":0,"routes_updated":0,"errors":[]}
    try:
        ensure_schema(); ports=list_ports(port_ids); summary["ports_scanned"]=len(ports)
        eligible=[p for p in ports if p.get("latitude") is not None and p.get("longitude") is not None]
        summary["geocoding_required"]=len(ports)-len(eligible); summary["ports_skipped"]=len(ports)-len(eligible)
        for start in range(0,len(eligible),settings.batch_size):
            batch=eligible[start:start+settings.batch_size]
            try: weather_payloads=client.weather_batch(batch); summary["weather_requests"]+=1
            except Exception as exc:
                summary["failed_ports"]+=len(batch); summary["errors"].append({"stage":"weather","error_type":type(exc).__name__,"error_message":str(exc)}); continue
            try: marine_payloads=client.marine_batch(batch); summary["marine_requests"]+=1
            except Exception as exc:
                marine_payloads=[{} for _ in batch]; summary["errors"].append({"stage":"marine","error_type":type(exc).__name__,"error_message":str(exc)})
            for port,payload,marine in zip(batch,weather_payloads,marine_payloads):
                try:
                    current=dict(payload.get("current",{})); current.update(marine.get("current",{})); hourly=hourly_rows(payload,marine); risk=calculate_risk(current,hourly)
                    observed=str(payload.get("current",{}).get("time") or started.isoformat()); fetched=datetime.now(timezone.utc).isoformat(); version=load_rules()["version"]
                    values={"snapshot_id":f"{port['port_id']}|{observed}|{version}","observed_at":observed,"fetched_at":fetched,"risk_score":risk["score"],"risk_level":risk["level"],"confidence":risk["confidence"],"completeness":risk["data_completeness"],"trend":risk["trend"],"summary":risk["summary"],"max6":risk["max_risk_6h"],"max24":risk["max_risk_24h"],"avg24":risk["average_risk_24h"],"temperature":current.get("temperature_2m"),"humidity":current.get("relative_humidity_2m"),"precipitation":current.get("precipitation"),"visibility":hourly[0].get("visibility") if hourly else None,"wind_speed":current.get("wind_speed_10m"),"wind_gusts":current.get("wind_gusts_10m"),"wind_direction":current.get("wind_direction_10m"),"wave_height":current.get("wave_height"),"wave_period":current.get("wave_period"),"weather_code":current.get("weather_code"),"marine_source":"Open-Meteo Marine API" if marine else "unavailable","scoring_version":version,"factors_json":json.dumps(risk["factors"],ensure_ascii=False)}
                    summary["routes_updated"]+=write_weather(port,values,dry_run); summary["neo4j_records_written"]+=0 if dry_run else 1; summary["successful_ports"]+=1
                except Exception as exc:
                    summary["failed_ports"]+=1; summary["errors"].append({"port_id":port["port_id"],"port_name":port["name"],"stage":"process","error_type":type(exc).__name__,"error_message":str(exc),"retry_count":settings.max_retries})
        finished=datetime.now(timezone.utc); summary.update({"finished_at":finished.isoformat(),"duration_ms":round((finished-started).total_seconds()*1000)})
        LOGGER.info("weather_update_summary %s",json.dumps(summary,ensure_ascii=False)); return summary
    finally:
        if own: client.close()
        UPDATE_LOCK.release()

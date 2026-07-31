from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neo4j import READ_ACCESS, WRITE_ACCESS

from database.location_identity import (
    LOCATION_ID_CONFIRMATION,
    LOCATION_ID_VERSION,
    build_location_id_plan,
)
from database.neo4j_client import close_driver, get_driver, get_settings, to_jsonable


class LocationIdMigrationRepository:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _options(self, access_mode: str) -> dict[str, Any]:
        options: dict[str, Any] = {"default_access_mode": access_mode}
        if self.settings.database:
            options["database"] = self.settings.database
        return options

    def locations(self) -> list[dict[str, Any]]:
        with get_driver().session(**self._options(READ_ACCESS)) as session:
            result = session.run(
                """
                MATCH (location:TransportLocation)
                RETURN elementId(location) AS element_id,labels(location) AS labels,
                       properties(location) AS properties
                ORDER BY location.location_id
                """
            )
            return [to_jsonable(record.data()) for record in result]

    def reference_counts(self, old_ids: list[str]) -> dict[str, int]:
        with get_driver().session(**self._options(READ_ACCESS)) as session:
            rows = session.run(
                """
                WITH $old_ids AS old_ids
                CALL (old_ids) {
                  MATCH (node) WHERE node.origin_id IN old_ids
                  RETURN count(node) AS origin_id
                }
                CALL (old_ids) {
                  MATCH (node) WHERE node.destination_id IN old_ids
                  RETURN count(node) AS destination_id
                }
                CALL (old_ids) {
                  MATCH (node) WHERE properties(node)['port_id'] IN old_ids
                  RETURN count(node) AS port_id
                }
                CALL (old_ids) {
                  MATCH (target:AisObservationTarget)
                  WHERE any(item IN coalesce(target.port_ids,[]) WHERE item IN old_ids)
                  RETURN count(target) AS ais_targets
                }
                RETURN origin_id,destination_id,port_id,ais_targets
                """,
                {"old_ids": old_ids},
            ).single()
            return {key: int(rows[key]) for key in rows.keys()} if rows else {}

    def execute(self, rows: list[dict[str, Any]], migrated_at: datetime) -> dict[str, int]:
        changed_rows = [row for row in rows if row["changed"]]
        port_alias_rows = [row for row in rows if row["kind"] == "port"]
        parameters = {
            "rows": rows,
            "changed_rows": changed_rows,
            "port_alias_rows": port_alias_rows,
            "version": LOCATION_ID_VERSION,
            "migrated_at": migrated_at.isoformat(),
        }
        with get_driver().session(**self._options(WRITE_ACCESS)) as session:
            def write(transaction):
                counters: dict[str, int] = {}

                def run(name: str, query: str) -> None:
                    result = transaction.run(query, parameters)
                    record = result.single()
                    result.consume()
                    counters[name] = int(record["count"] if record else 0)

                run(
                    "temporary_ids",
                    """
                    UNWIND $changed_rows AS row
                    MATCH (location:TransportLocation) WHERE elementId(location)=row.element_id
                    SET location.location_id=row.temporary_id
                    RETURN count(location) AS count
                    """,
                )
                run(
                    "locations",
                    """
                    UNWIND $rows AS row
                    MATCH (location:TransportLocation) WHERE elementId(location)=row.element_id
                    SET location.location_id=row.new_id,
                        location.canonical_location_id=row.new_id,
                        location.location_aliases=row.aliases,
                        location.previous_location_id=CASE WHEN row.changed THEN row.old_id ELSE location.previous_location_id END,
                        location.location_id_version=$version,
                        location.country_code=CASE WHEN row.country_code='XX' THEN location.country_code ELSE row.country_code END,
                        location.location_id_migrated_at=datetime($migrated_at)
                    RETURN count(location) AS count
                    """,
                )
                for field in ("origin_id", "destination_id"):
                    run(
                        field,
                        f"""
                        UNWIND $rows AS row
                        MATCH (node) WHERE node.{field} IN row.reference_aliases
                          AND node.{field}<>row.new_id
                        SET node.{field}=row.new_id
                        RETURN count(node) AS count
                        """,
                    )
                run(
                    "port_id",
                    """
                    UNWIND $port_alias_rows AS row
                    MATCH (node) WHERE node.port_id IN row.aliases
                    SET node.port_id=row.new_id
                    RETURN count(DISTINCT node) AS count
                    """,
                )
                run(
                    "ais_targets",
                    """
                    MATCH (target:AisObservationTarget)-[:REPRESENTS_PORT]->(port:Port)
                    SET target.port_ids=[port.location_id]
                    RETURN count(target) AS count
                    """,
                )
                return counters

            return session.execute_write(write)

    def validation(self) -> dict[str, Any]:
        with get_driver().session(**self._options(READ_ACCESS)) as session:
            row = session.run(
                """
                MATCH (location:TransportLocation)
                WITH collect(location) AS locations
                RETURN size(locations) AS total,
                       size([node IN locations WHERE node.location_id_version=$version]) AS migrated,
                       size([node IN locations WHERE node.location_id =~ '^(PORT|AIR|RAIL|ROAD|FAC|WH)-[A-Z0-9-]+$']) AS valid_format,
                       size([node IN locations WHERE size(coalesce(node.location_aliases,[]))>0]) AS with_aliases,
                       size([node IN locations WHERE node.location_id STARTS WITH 'MIGRATING-']) AS temporary_ids
                """,
                {"version": LOCATION_ID_VERSION},
            ).single()
            duplicates = session.run(
                """
                MATCH (location:TransportLocation)
                WITH location.location_id AS id,count(*) AS count
                WHERE count>1 RETURN id,count ORDER BY id
                """
            )
            return {
                **({key: int(row[key]) for key in row.keys()} if row else {}),
                "duplicates": [to_jsonable(record.data()) for record in duplicates],
            }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一 TransportLocation 地点ID为 location-id-v2 格式")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只生成迁移计划（默认）")
    mode.add_argument("--execute", action="store_true", help="执行两阶段主键替换")
    parser.add_argument("--confirm", default="", help=f"执行时必须填写 {LOCATION_ID_CONFIRMATION}")
    parser.add_argument("--output", type=Path, default=Path("artifacts/location_id_v2_plan.json"))
    args = parser.parse_args()
    if args.execute and args.confirm != LOCATION_ID_CONFIRMATION:
        parser.error(f"--execute 必须同时提供 --confirm {LOCATION_ID_CONFIRMATION}")
    return args


def main() -> int:
    args = parse_args()
    repository = LocationIdMigrationRepository()
    try:
        get_driver().verify_connectivity()
        plan = build_location_id_plan(repository.locations())
        plan["generated_at"] = datetime.now(timezone.utc).isoformat()
        plan["mode"] = "execute" if args.execute else "dry-run"
        plan["reference_counts"] = repository.reference_counts(
            sorted({alias for row in plan["rows"] for alias in row["reference_aliases"]})
        )
        if args.execute:
            plan["execution"] = repository.execute(plan["rows"], datetime.now(timezone.utc))
            plan["validation"] = repository.validation()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "mode": plan["mode"],
                    "version": plan["version"],
                    "locations": plan["location_count"],
                    "changed": plan["changed_count"],
                    "collisions": plan["collision_count"],
                    "referenceCounts": plan["reference_counts"],
                    "execution": plan.get("execution"),
                    "validation": plan.get("validation"),
                    "plan": str(args.output),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        close_driver()


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neo4j import READ_ACCESS, WRITE_ACCESS

from database.country_identity import COUNTRY_NAMING_VERSION, canonical_country_fields
from database.location_identity import country_code as inferred_location_country_code
from database.neo4j_client import close_driver, get_driver, get_settings, to_jsonable


CONFIRMATION = "APPLY_COUNTRY_NAMING_V1"


class CountryNamingMigrationRepository:
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
                RETURN elementId(location) AS element_id,properties(location) AS properties
                ORDER BY location.location_id
                """
            )
            return [to_jsonable(record.data()) for record in result]

    def execute(self, rows: list[dict[str, Any]], migrated_at: datetime) -> int:
        with get_driver().session(**self._options(WRITE_ACCESS)) as session:
            def write(transaction) -> int:
                record = transaction.run(
                    """
                    UNWIND $rows AS row
                    MATCH (location:TransportLocation) WHERE elementId(location)=row.element_id
                    SET location.country_code=row.country_code,
                        location.country=row.country,
                        location.country_name_en=row.country_name_en,
                        location.country_name_zh=row.country_name_zh,
                        location.country_aliases=row.country_aliases,
                        location.country_naming_version=$version,
                        location.country_naming_migrated_at=datetime($migrated_at)
                    RETURN count(location) AS count
                    """,
                    rows=rows,
                    version=COUNTRY_NAMING_VERSION,
                    migrated_at=migrated_at.isoformat(),
                ).single()
                return int(record["count"] if record else 0)

            return session.execute_write(write)

    def validation(self) -> dict[str, Any]:
        with get_driver().session(**self._options(READ_ACCESS)) as session:
            row = session.run(
                """
                MATCH (location:TransportLocation)
                WITH collect(location) AS locations
                RETURN size(locations) AS total,
                       size([node IN locations WHERE node.country_naming_version=$version]) AS migrated,
                       size([node IN locations WHERE node.country_code =~ '^[A-Z]{2}$']) AS valid_codes,
                       size([node IN locations WHERE node.country IS NOT NULL]) AS with_english_name,
                       size([node IN locations WHERE node.country_name_zh IS NOT NULL]) AS with_chinese_name,
                       size([node IN locations WHERE size(coalesce(node.country_aliases,[]))>0]) AS with_aliases
                """,
                version=COUNTRY_NAMING_VERSION,
            ).single()
            return {key: int(row[key]) for key in row.keys()} if row else {}


def build_plan(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for node in nodes:
        properties = dict(node.get("properties") or {})
        fields = canonical_country_fields(properties)
        if fields is None:
            inferred_code = inferred_location_country_code(properties)
            fields = canonical_country_fields({**properties, "country_code": inferred_code})
        if fields is None:
            unresolved.append(
                {
                    "element_id": node["element_id"],
                    "location_id": properties.get("location_id"),
                    "country": properties.get("country"),
                    "country_code": properties.get("country_code"),
                }
            )
            continue
        row = {"element_id": node["element_id"], **fields}
        row["changed"] = any(properties.get(field) != value for field, value in fields.items())
        rows.append(row)
    return {
        "version": COUNTRY_NAMING_VERSION,
        "location_count": len(nodes),
        "resolvable_count": len(rows),
        "changed_count": sum(1 for row in rows if row["changed"]),
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "rows": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一 TransportLocation 国家代码和多语言显示名称")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只生成迁移计划（默认）")
    mode.add_argument("--execute", action="store_true", help="执行国家字段标准化")
    parser.add_argument("--confirm", default="", help=f"执行时必须填写 {CONFIRMATION}")
    parser.add_argument("--output", type=Path, default=Path("artifacts/country_naming_v1_plan.json"))
    args = parser.parse_args()
    if args.execute and args.confirm != CONFIRMATION:
        parser.error(f"--execute 必须同时提供 --confirm {CONFIRMATION}")
    return args


def main() -> int:
    args = parse_args()
    repository = CountryNamingMigrationRepository()
    try:
        get_driver().verify_connectivity()
        plan = build_plan(repository.locations())
        plan["generated_at"] = datetime.now(timezone.utc).isoformat()
        plan["mode"] = "execute" if args.execute else "dry-run"
        if args.execute:
            if plan["unresolved_count"]:
                raise RuntimeError("存在无法确定国家的地点，已拒绝部分迁移")
            plan["updated_count"] = repository.execute(plan["rows"], datetime.now(timezone.utc))
            plan["validation"] = repository.validation()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "mode": plan["mode"],
                    "version": plan["version"],
                    "locations": plan["location_count"],
                    "resolvable": plan["resolvable_count"],
                    "changed": plan["changed_count"],
                    "unresolved": plan["unresolved_count"],
                    "updated": plan.get("updated_count"),
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

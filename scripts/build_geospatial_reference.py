from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import shapefile
from shapely.geometry import box, shape
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.country_identity import resolve_country_code
from database.neo4j_client import close_driver
from geography.geometry import GEOSPATIAL_VERSION, geodesic_circle, geometry_mapping, stable_hash
from geography.repository import list_locations


CANONICAL_PORT_CODES = {
    "CNSHA": "CNSHG",
    "CNNAN": "CNNSA",
    "CNNGB": "CNNBG",
    "CNSZX": "CNSZP",
}

MIDDLE_EAST_COUNTRIES = {
    "Bahrain",
    "Egypt",
    "Iran",
    "Iraq",
    "Israel",
    "Jordan",
    "Kuwait",
    "Lebanon",
    "Oman",
    "Qatar",
    "Saudi Arabia",
    "Syria",
    "Turkey",
    "United Arab Emirates",
    "Yemen",
}

PORT_ZONE_LOCATIONS = {
    "port-shanghai": "CN-SHA",
    "port-singapore": "SGSIN",
    "port-rotterdam": "NLRTM",
}

SOURCE_CATALOG = {
    "unlocode": {
        "name": "UN/LOCODE 2025-1",
        "provider": "UNECE",
        "source_url": "https://unlocode.unece.org/publications/",
        "download_mirror": "https://github.com/datasets/un-locode",
        "license": "ODC PDDL-1.0",
        "commercial_use": "allowed",
        "adopted": True,
        "usage": "Port and transport-location registry coordinates and canonical codes",
    },
    "ourairports": {
        "name": "OurAirports airports.csv",
        "provider": "OurAirports",
        "source_url": "https://ourairports.com/data/",
        "license": "Public Domain",
        "commercial_use": "allowed",
        "adopted": True,
        "usage": "Airport reference coordinates matched by IATA code",
    },
    "geonames": {
        "name": "GeoNames cities15000",
        "provider": "GeoNames",
        "source_url": "https://download.geonames.org/export/dump/",
        "license": "CC BY 4.0",
        "commercial_use": "allowed with attribution",
        "adopted": True,
        "usage": "Low-confidence city-centroid fallback only",
    },
    "natural_earth": {
        "name": "Natural Earth 5.1.x",
        "provider": "Natural Earth",
        "source_url": "https://www.naturalearthdata.com/downloads/",
        "license": "Public Domain",
        "commercial_use": "allowed",
        "adopted": True,
        "usage": "Marine region polygons and small-scale country land mask",
    },
    "marine_regions": {
        "name": "Marine Regions Gazetteer",
        "provider": "Flanders Marine Institute (VLIZ)",
        "source_url": "https://www.marineregions.org/",
        "license": "CC BY 4.0",
        "commercial_use": "review required; not for navigation or legal delimitation",
        "adopted": True,
        "usage": "Published bounding extents for the Strait of Hormuz and Suez Canal",
    },
    "nominatim": {
        "name": "OpenStreetMap Nominatim public service",
        "provider": "OpenStreetMap Foundation",
        "source_url": "https://operations.osmfoundation.org/policies/nominatim/",
        "license": "ODbL 1.0",
        "commercial_use": "allowed with attribution and share-alike obligations",
        "adopted": False,
        "reason": "Not embedded as a periodic or bulk production geocoder; downloadable datasets are used instead",
    },
}


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").casefold()
    return " ".join(text.replace("'", " ").replace("-", " ").split())


def parse_unlocode_coordinate(value: str | None) -> tuple[float, float] | None:
    parts = str(value or "").strip().split()
    if len(parts) != 2:
        return None
    latitude_text, longitude_text = parts
    try:
        latitude = int(latitude_text[:2]) + int(latitude_text[2:4]) / 60
        longitude = int(longitude_text[:3]) + int(longitude_text[3:5]) / 60
    except (TypeError, ValueError):
        return None
    if latitude_text[-1] == "S":
        latitude *= -1
    if longitude_text[-1] == "W":
        longitude *= -1
    return round(latitude, 6), round(longitude, 6)


def load_unlocode(path: Path) -> dict[str, dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {
            f"{row['Country']}{row['Location']}": row
            for row in csv.DictReader(handle)
        }


def load_airports(path: Path) -> dict[str, list[dict[str, str]]]:
    airports: dict[str, list[dict[str, str]]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("iata_code"):
                airports[row["iata_code"].upper()].append(row)
    return airports


def load_geonames(path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    with zipfile.ZipFile(path) as archive:
        filename = next(name for name in archive.namelist() if name.endswith(".txt"))
        with archive.open(filename) as raw_handle:
            for raw_line in raw_handle:
                parts = raw_line.decode("utf-8").rstrip("\n").split("\t")
                if len(parts) < 19:
                    continue
                row = {
                    "geoname_id": parts[0],
                    "name": parts[1],
                    "latitude": float(parts[4]),
                    "longitude": float(parts[5]),
                    "country_code": parts[8],
                    "population": int(parts[14] or 0),
                }
                names = {parts[1], parts[2], *(parts[3].split(",") if parts[3] else [])}
                for name in {normalize_text(item) for item in names if item}:
                    index[(name, row["country_code"])].append(row)
                    index[(name, "")].append(row)
    return index


def country_code(location: dict[str, Any]) -> str | None:
    resolved = resolve_country_code(
        location.get("country_code"),
        location.get("country"),
        location.get("country_name_en"),
        location.get("country_name_zh"),
    )
    if resolved:
        return resolved
    location_id = str(location.get("location_id") or "")
    return (
        location_id[:2].upper()
        if len(location_id) >= 5 and location_id[2:3] == "-" and location_id[:2].isalpha()
        else None
    )


def unavailable_coordinate(
    location: dict[str, Any],
    canonical_unlocode: str | None,
    identity_status: str | None,
) -> dict[str, Any]:
    return {
        "location_id": location["location_id"],
        "latitude": None,
        "longitude": None,
        "coordinate_source": None,
        "coordinate_source_url": None,
        "coordinate_license": None,
        "coordinate_collected_at": None,
        "coordinate_confidence": 0.0,
        "coordinate_status": "unavailable",
        "coordinate_record_id": None,
        "canonical_unlocode": canonical_unlocode,
        "identity_status": identity_status,
        "missing_reason": "no_exact_registry_coordinate_or_city_centroid_match",
    }


def build_location_catalog(
    locations: list[dict[str, Any]],
    unlocode: dict[str, dict[str, str]],
    airports: dict[str, list[dict[str, str]]],
    geonames: dict[tuple[str, str], list[dict[str, Any]]],
    collected_at: str,
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for location in locations:
        location_id = str(location.get("location_id") or "")
        labels = set(location.get("labels") or [])
        raw_unlocode = str(location.get("unlocode") or location_id).replace("-", "").upper()
        canonical_unlocode = CANONICAL_PORT_CODES.get(raw_unlocode, raw_unlocode) if "Port" in labels else None
        identity_status = None
        if canonical_unlocode:
            identity_status = (
                "canonical_code_corrected_alias_preserved"
                if canonical_unlocode != raw_unlocode
                else "canonical_code_confirmed"
            )
        record: dict[str, Any] | None = None
        if "Airport" in labels:
            iata = str(location.get("iata") or (location_id if len(location_id) == 3 else "")).upper()
            candidates = airports.get(iata) or []
            candidates.sort(
                key=lambda item: (
                    item.get("scheduled_service") == "yes",
                    item.get("type") == "large_airport",
                    item.get("type") == "medium_airport",
                ),
                reverse=True,
            )
            if candidates:
                airport = candidates[0]
                record = {
                    "location_id": location_id,
                    "latitude": round(float(airport["latitude_deg"]), 6),
                    "longitude": round(float(airport["longitude_deg"]), 6),
                    "coordinate_source": "OurAirports airports.csv",
                    "coordinate_source_url": f"https://ourairports.com/airports/{airport['ident']}/",
                    "coordinate_license": "Public Domain",
                    "coordinate_collected_at": collected_at,
                    "coordinate_confidence": 0.9,
                    "coordinate_status": "reference",
                    "coordinate_record_id": airport["ident"],
                    "canonical_unlocode": None,
                    "identity_status": None,
                    "matched_name": airport["name"],
                }
        if record is None and "Port" in labels and canonical_unlocode:
            unlocode_record = unlocode.get(canonical_unlocode)
            coordinate = parse_unlocode_coordinate(
                unlocode_record.get("Coordinates") if unlocode_record else None
            )
            if unlocode_record and coordinate:
                record = {
                    "location_id": location_id,
                    "latitude": coordinate[0],
                    "longitude": coordinate[1],
                    "coordinate_source": "UN/LOCODE 2025-1",
                    "coordinate_source_url": "https://unlocode.unece.org/publications/",
                    "coordinate_license": "ODC PDDL-1.0",
                    "coordinate_collected_at": collected_at,
                    "coordinate_confidence": 0.85,
                    "coordinate_status": "reference",
                    "coordinate_record_id": canonical_unlocode,
                    "canonical_unlocode": canonical_unlocode,
                    "identity_status": identity_status,
                    "matched_name": unlocode_record["Name"],
                }
        if record is None:
            code = country_code(location)
            city = normalize_text(location.get("city"))
            candidates = list(geonames.get((city, code or ""), [])) if city else []
            if candidates:
                city_record = max(candidates, key=lambda item: item["population"])
                exact_country_match = bool(code)
                record = {
                    "location_id": location_id,
                    "latitude": round(city_record["latitude"], 6),
                    "longitude": round(city_record["longitude"], 6),
                    "coordinate_source": "GeoNames cities15000 city centroid",
                    "coordinate_source_url": f"https://www.geonames.org/{city_record['geoname_id']}",
                    "coordinate_license": "CC BY 4.0",
                    "coordinate_collected_at": collected_at,
                    "coordinate_confidence": 0.45 if exact_country_match else 0.35,
                    "coordinate_status": "estimated",
                    "coordinate_record_id": city_record["geoname_id"],
                    "canonical_unlocode": canonical_unlocode,
                    "identity_status": identity_status,
                    "matched_name": city_record["name"],
                    "fallback_method": (
                        "country_matched_city_centroid"
                        if exact_country_match
                        else "global_city_centroid_without_country"
                    ),
                }
        if record is None:
            record = unavailable_coordinate(location, canonical_unlocode, identity_status)
        record["coordinate_hash"] = stable_hash(
            record["latitude"],
            record["longitude"],
            record["coordinate_source"],
            record["coordinate_status"],
            record["coordinate_record_id"],
            record["canonical_unlocode"],
            record["identity_status"],
        )
        catalog[location_id] = record
    return catalog


def extract_zip(zip_path: Path, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(target)
    return target


def shapefile_rows(path: Path) -> list[tuple[dict[str, Any], Any]]:
    reader = shapefile.Reader(str(path))
    return [
        (shape_record.record.as_dict(), shape(shape_record.shape.__geo_interface__))
        for shape_record in reader.iterShapeRecords()
    ]


def marine_features(rows: list[tuple[dict[str, Any], Any]], *names: str) -> Any:
    expected = {normalize_text(name) for name in names}
    matches = [
        geometry
        for record, geometry in rows
        if {
            normalize_text(record.get("name_en")),
            normalize_text(record.get("name")),
        }
        & expected
    ]
    if not matches:
        raise RuntimeError(f"Natural Earth marine feature not found: {sorted(expected)}")
    return unary_union(matches)


def country_union(rows: list[tuple[dict[str, Any], Any]], names: set[str]) -> Any:
    matches = [
        geometry
        for record, geometry in rows
        if str(record.get("ADMIN") or record.get("NAME") or "") in names
    ]
    if not matches:
        raise RuntimeError("Natural Earth country features were not found")
    return unary_union(matches)


def build_zone_catalog(
    zone_config: dict[str, Any],
    marine_rows: list[tuple[dict[str, Any], Any]],
    country_rows: list[tuple[dict[str, Any], Any]],
    locations: dict[str, dict[str, Any]],
    collected_at: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    suez_bbox = box(32.29, 29.91, 32.59, 31.28)
    geometries = {
        "red-sea": unary_union(
            [
                marine_features(marine_rows, "Red Sea"),
                marine_features(marine_rows, "Gulf of Suez"),
                marine_features(marine_rows, "Gulf of Aqaba"),
                suez_bbox,
            ]
        ),
        "malacca-strait": marine_features(marine_rows, "Strait of Malacca"),
        "indian-ocean": marine_features(marine_rows, "Indian Ocean"),
        "pacific-ocean": marine_features(
            marine_rows, "North Pacific Ocean", "South Pacific Ocean"
        ),
        "middle-east": country_union(country_rows, MIDDLE_EAST_COUNTRIES),
        "hormuz-strait": box(55.2055, 25.7096, 57.3411, 27.2113),
        "south-china-sea": marine_features(marine_rows, "South China Sea"),
    }
    for zone_id, location_id in PORT_ZONE_LOCATIONS.items():
        location = locations.get(location_id) or {}
        if location.get("latitude") is None or location.get("longitude") is None:
            raise RuntimeError(f"Port zone {zone_id} has no sourced coordinate")
        geometries[zone_id] = geodesic_circle(
            float(location["latitude"]), float(location["longitude"]), 50.0
        )
    zones: dict[str, dict[str, Any]] = {}
    for zone in zone_config["zones"]:
        zone_id = zone["id"]
        geometry = geometries[zone_id].simplify(0.03, preserve_topology=True)
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if zone_id == "hormuz-strait":
            source = "Marine Regions Gazetteer MRGID 24076 bounding extent"
            source_url = "https://marineregions.org/gazetteer.php?id=24076&p=details"
            license_name = "CC BY 4.0"
            status = "estimated_bbox_from_gazetteer"
            confidence = 0.45
        elif zone_id == "red-sea":
            source = "Natural Earth marine polygons + Marine Regions Suez Canal extent"
            source_url = "https://www.naturalearthdata.com/downloads/10m-physical-vectors/"
            license_name = "Public Domain + CC BY 4.0"
            status = "reference_composite"
            confidence = 0.7
        elif zone_id == "middle-east":
            source = "Natural Earth 1:110m admin-0 country union"
            source_url = "https://www.naturalearthdata.com/downloads/110m-cultural-vectors/"
            license_name = "Public Domain"
            status = "reference_small_scale"
            confidence = 0.65
        elif zone_id.startswith("port-"):
            location = locations[PORT_ZONE_LOCATIONS[zone_id]]
            source = f"50 km operational buffer around {location['coordinate_source']} coordinate"
            source_url = location["coordinate_source_url"]
            license_name = location["coordinate_license"]
            status = "estimated_operational_buffer"
            confidence = round(float(location["coordinate_confidence"]) * 0.6, 4)
        else:
            source = "Natural Earth 1:10m geography marine polygons"
            source_url = "https://www.naturalearthdata.com/downloads/10m-physical-vectors/"
            license_name = "Public Domain"
            status = "reference_small_scale"
            confidence = 0.75
        applicable_modes = ["sea"]
        if zone_id == "middle-east":
            applicable_modes = ["sea", "air", "rail", "road"]
        elif zone_id.startswith("port-"):
            applicable_modes = ["sea", "rail", "road"]
        geojson = geometry_mapping(geometry, precision=5)
        rounded_geometry = shape(geojson)
        if not rounded_geometry.is_valid:
            geojson = geometry_mapping(rounded_geometry.buffer(0), precision=6)
        zones[zone_id] = {
            "zone_id": zone_id,
            "name": zone["name"],
            "zone_type": zone["type"],
            "geometry_geojson": geojson,
            "geometry_source": source,
            "geometry_source_url": source_url,
            "geometry_license": license_name,
            "geometry_collected_at": collected_at,
            "geometry_status": status,
            "geometry_confidence": confidence,
            "applicable_modes": applicable_modes,
            "geometry_is_navigational": False,
            "geometry_hash": stable_hash(geojson, source, status, applicable_modes),
        }
    land = unary_union([geometry for _, geometry in country_rows]).simplify(
        0.05, preserve_topology=True
    )
    land_mask = {
        "geometry_geojson": geometry_mapping(land, precision=5),
        "geometry_source": "Natural Earth 1:110m admin-0 country polygons",
        "geometry_source_url": "https://www.naturalearthdata.com/downloads/110m-cultural-vectors/",
        "geometry_license": "Public Domain",
        "geometry_collected_at": collected_at,
        "geometry_hash": stable_hash(geometry_mapping(land, precision=5)),
    }
    return zones, land_mask


def build_reference(args: argparse.Namespace) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    locations = list_locations()
    location_catalog = build_location_catalog(
        locations,
        load_unlocode(args.unlocode_csv),
        load_airports(args.ourairports_csv),
        load_geonames(args.geonames_zip),
        generated_at,
    )
    work_dir = args.work_dir
    marine_dir = extract_zip(args.natural_earth_marine_zip, work_dir / "marine")
    countries_dir = extract_zip(args.natural_earth_countries_zip, work_dir / "countries")
    marine_path = next(marine_dir.glob("*.shp"))
    countries_path = next(countries_dir.glob("*.shp"))
    zone_config = json.loads(args.zone_config.read_text(encoding="utf-8"))
    zones, land_mask = build_zone_catalog(
        zone_config,
        shapefile_rows(marine_path),
        shapefile_rows(countries_path),
        location_catalog,
        generated_at,
    )
    return {
        "geospatial_version": GEOSPATIAL_VERSION,
        "generated_at": generated_at,
        "source_catalog": SOURCE_CATALOG,
        "locations": location_catalog,
        "zones": zones,
        "land_mask": land_mask,
        "summary": {
            "locations_total": len(location_catalog),
            "locations_with_coordinates": sum(
                1 for row in location_catalog.values() if row["latitude"] is not None
            ),
            "locations_unavailable": sum(
                1 for row in location_catalog.values() if row["latitude"] is None
            ),
            "zones_with_geometry": len(zones),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the compact stage-6 geospatial reference catalog")
    parser.add_argument("--unlocode-csv", type=Path, required=True)
    parser.add_argument("--ourairports-csv", type=Path, required=True)
    parser.add_argument("--geonames-zip", type=Path, required=True)
    parser.add_argument("--natural-earth-marine-zip", type=Path, required=True)
    parser.add_argument("--natural-earth-countries-zip", type=Path, required=True)
    parser.add_argument("--zone-config", type=Path, default=Path("config/gdelt_risk_zones.json"))
    parser.add_argument("--work-dir", type=Path, default=Path("/tmp/supply-chain-stage6-reference"))
    parser.add_argument("--output", type=Path, default=Path("config/geospatial_reference.json"))
    args = parser.parse_args()
    try:
        result = build_reference(args)
    finally:
        close_driver()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

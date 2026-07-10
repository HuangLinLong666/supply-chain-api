"""Neo4j AuraDB client helpers for the FastAPI service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, Neo4jError, ServiceUnavailable

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is listed in requirements.txt
    load_dotenv = None  # type: ignore[assignment]


if load_dotenv is not None:
    load_dotenv()


@dataclass(frozen=True)
class Neo4jSettings:
    uri: str
    username: str
    password: str
    database: str


_driver: Any | None = None


def get_settings() -> Neo4jSettings:
    """Read AuraDB connection settings from environment variables."""
    uri = os.getenv("AURA_NEO4J_URI") or os.getenv("NEO4J_URI", "")
    username = os.getenv("AURA_NEO4J_USERNAME") or os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("AURA_NEO4J_PASSWORD") or os.getenv("NEO4J_PASSWORD", "")
    database = os.getenv("AURA_NEO4J_DATABASE") or os.getenv("NEO4J_DATABASE", "neo4j")
    if not uri:
        raise RuntimeError("Missing AURA_NEO4J_URI in .env")
    if not password:
        raise RuntimeError("Missing AURA_NEO4J_PASSWORD in .env")
    return Neo4jSettings(uri=uri, username=username, password=password, database=database)


def get_driver() -> Any:
    """Create or reuse the Neo4j driver."""
    global _driver
    if _driver is None:
        settings = get_settings()
        _driver = GraphDatabase.driver(settings.uri, auth=(settings.username, settings.password))
    return _driver


def close_driver() -> None:
    """Close the shared Neo4j driver."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def verify_connectivity() -> None:
    """Verify AuraDB credentials and network connectivity."""
    get_driver().verify_connectivity()


def run_query(query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a read query against AuraDB and return JSON-friendly dictionaries."""
    settings = get_settings()
    try:
        with get_driver().session(database=settings.database) as session:
            result = session.run(query, parameters or {})
            return [to_jsonable(record.data()) for record in result]
    except AuthError as exc:
        raise RuntimeError("AuraDB authentication failed. Check AURA_NEO4J_USERNAME and AURA_NEO4J_PASSWORD.") from exc
    except ServiceUnavailable as exc:
        raise RuntimeError("AuraDB is unavailable. Check URI, network access, and instance status.") from exc
    except Neo4jError as exc:
        raise RuntimeError(f"Neo4j query failed: {exc.message}") from exc


def to_jsonable(value: Any) -> Any:
    """Convert Neo4j values to JSON-safe primitives."""
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "iso_format"):
        return value.iso_format()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value

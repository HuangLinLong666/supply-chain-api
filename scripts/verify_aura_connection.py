#!/usr/bin/env python3
"""Verify that the project can connect to Neo4j AuraDB."""

from __future__ import annotations

import argparse
import os
from getpass import getpass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError, ClientError, ServiceUnavailable
except ImportError as exc:
    raise SystemExit("Missing neo4j driver. Run: pip install -r requirements.txt") from exc


def load_environment() -> None:
    if load_dotenv is not None:
        dotenv_path = Path(__file__).resolve().parent.parent / ".env"
        load_dotenv(dotenv_path=dotenv_path, override=True)


def parse_args() -> argparse.Namespace:
    load_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=os.getenv("AURA_NEO4J_URI"))
    parser.add_argument("--user", default=os.getenv("AURA_NEO4J_USERNAME", "neo4j"))
    parser.add_argument("--password", default=os.getenv("AURA_NEO4J_PASSWORD"))
    parser.add_argument("--database", default=os.getenv("AURA_NEO4J_DATABASE") or None)
    parser.add_argument("--prompt-password", action="store_true", help="Prompt for the AuraDB password instead of reading .env")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.uri:
        raise SystemExit("Missing AURA_NEO4J_URI. Add it to .env or pass --uri.")
    if args.prompt_password or not args.password:
        args.password = getpass(f"AuraDB password for {args.user}: ")
    if not args.password:
        raise SystemExit("Missing AuraDB password.")

    driver = None
    try:
        driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
        driver.verify_connectivity()
        session_options = {"database": args.database} if args.database else {}
        with driver.session(**session_options) as session:
            ok_record = session.run("RETURN 1 AS ok").single()
            if ok_record is None:
                raise SystemExit("AuraDB did not return a result for RETURN 1.")
            ok = ok_record["ok"]

            node_count_record = session.run("MATCH (n) RETURN count(n) AS count").single()
            if node_count_record is None:
                raise SystemExit("AuraDB did not return a node count.")
            node_count = node_count_record["count"]

            rel_count_record = session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()
            if rel_count_record is None:
                raise SystemExit("AuraDB did not return a relationship count.")
            rel_count = rel_count_record["count"]
    except AuthError as exc:
        raise SystemExit("Authentication failed. Check AuraDB username and database password, not your Neo4j website login.") from exc
    except ClientError as exc:
        if exc.code == "Neo.ClientError.Database.DatabaseNotFound":
            raise SystemExit(
                f"AuraDB database {args.database!r} does not exist. "
                "Remove AURA_NEO4J_DATABASE from .env to use the default database, "
                "or set it to the database name shown in your Aura credentials."
            ) from exc
        raise
    except ServiceUnavailable as exc:
        raise SystemExit("AuraDB is unavailable. Check URI, instance status, and network access.") from exc
    finally:
        if driver is not None:
            driver.close()

    print("AuraDB connection OK")
    print(f"RETURN 1 AS ok -> {ok}")
    print(f"node_count -> {node_count}")
    print(f"rel_count -> {rel_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

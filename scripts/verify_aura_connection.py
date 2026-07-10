#!/usr/bin/env python3
"""Verify that the project can connect to Neo4j AuraDB."""

from __future__ import annotations

import argparse
import os
from getpass import getpass

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment]

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import AuthError, ServiceUnavailable
except ImportError as exc:
    raise SystemExit("Missing neo4j driver. Run: pip install -r requirements.txt") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=os.getenv("AURA_NEO4J_URI"))
    parser.add_argument("--user", default=os.getenv("AURA_NEO4J_USERNAME", "neo4j"))
    parser.add_argument("--password", default=os.getenv("AURA_NEO4J_PASSWORD"))
    parser.add_argument("--database", default=os.getenv("AURA_NEO4J_DATABASE", "neo4j"))
    parser.add_argument("--prompt-password", action="store_true", help="Prompt for the AuraDB password instead of reading .env")
    return parser.parse_args()


def main() -> int:
    if load_dotenv is not None:
        load_dotenv()
    args = parse_args()
    if not args.uri:
        raise SystemExit("Missing AURA_NEO4J_URI. Add it to .env or pass --uri.")
    if args.prompt_password or not args.password:
        args.password = getpass(f"AuraDB password for {args.user}: ")
    if not args.password:
        raise SystemExit("Missing AuraDB password.")

    try:
        driver = GraphDatabase.driver(args.uri, auth=(args.user, args.password))
        driver.verify_connectivity()
        with driver.session(database=args.database) as session:
            ok = session.run("RETURN 1 AS ok").single()["ok"]
            node_count = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]
            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"]
    except AuthError as exc:
        raise SystemExit("Authentication failed. Check AuraDB username and database password, not your Neo4j website login.") from exc
    except ServiceUnavailable as exc:
        raise SystemExit("AuraDB is unavailable. Check URI, instance status, and network access.") from exc
    finally:
        try:
            driver.close()  # type: ignore[name-defined]
        except Exception:
            pass

    print("AuraDB connection OK")
    print(f"RETURN 1 AS ok -> {ok}")
    print(f"node_count -> {node_count}")
    print(f"rel_count -> {rel_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

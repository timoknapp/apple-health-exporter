from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from .config import DEFAULT_DATABASE_PATH
from .db import backup_database, current_schema_version, integrity_check, migrate_database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="health-coach")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path(os.environ.get("HEALTH_COACH_DB_PATH", DEFAULT_DATABASE_PATH)),
        help="SQLite database path (defaults to HEALTH_COACH_DB_PATH or /data/coach.sqlite3)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="Apply all ordered database migrations")
    backup = subparsers.add_parser("backup", help="Create an online SQLite backup")
    backup.add_argument("destination", type=Path)
    subparsers.add_parser("integrity-check", help="Run PRAGMA integrity_check")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    database = arguments.database.expanduser().absolute()
    if arguments.command == "migrate":
        migrate_database(database)
        print(f"schema version {current_schema_version(database)}")
        return 0
    if arguments.command == "backup":
        backup_database(database, arguments.destination.expanduser().absolute())
        print("backup complete")
        return 0
    if arguments.command == "integrity-check":
        result = integrity_check(database)
        print(result)
        return 0 if result == "ok" else 1
    raise AssertionError("argparse accepted an unknown command")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        version=1,
        name="initial_health_store",
        statements=(
            """
            CREATE TABLE batches (
                batch_id TEXT PRIMARY KEY,
                payload_sha256 TEXT NOT NULL,
                device_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                timezone TEXT NOT NULL,
                mode TEXT NOT NULL CHECK (mode IN ('incremental', 'backfill')),
                received_at TEXT NOT NULL,
                raw_payload_json TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE records (
                device_id TEXT NOT NULL,
                sample_type TEXT NOT NULL,
                sample_id TEXT NOT NULL,
                record_kind TEXT NOT NULL
                    CHECK (record_kind IN ('numeric', 'category', 'workout')),
                start_at TEXT NOT NULL,
                end_at TEXT NOT NULL,
                start_ts REAL NOT NULL,
                end_ts REAL NOT NULL,
                numeric_value REAL,
                category_value TEXT,
                activity_type INTEGER,
                duration_seconds REAL,
                total_energy_kcal REAL,
                total_distance_m REAL,
                unit TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_bundle_id TEXT NOT NULL,
                source_version TEXT,
                source_product_type TEXT,
                device_json TEXT,
                metadata_json TEXT NOT NULL,
                raw_record_json TEXT NOT NULL,
                source_batch_id TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                deleted_at TEXT,
                PRIMARY KEY (device_id, sample_type, sample_id),
                FOREIGN KEY (source_batch_id) REFERENCES batches(batch_id)
            )
            """,
            """
            CREATE TABLE tombstones (
                device_id TEXT NOT NULL,
                sample_type TEXT NOT NULL,
                sample_id TEXT NOT NULL,
                deleted_at TEXT NOT NULL,
                source_batch_id TEXT NOT NULL,
                raw_deletion_json TEXT NOT NULL,
                PRIMARY KEY (device_id, sample_type, sample_id),
                FOREIGN KEY (source_batch_id) REFERENCES batches(batch_id)
            )
            """,
        ),
    ),
    Migration(
        version=2,
        name="query_indexes",
        statements=(
            """
            CREATE INDEX idx_records_type_start_active
            ON records(sample_type, start_ts)
            WHERE deleted_at IS NULL
            """,
            """
            CREATE INDEX idx_records_workout_start_active
            ON records(start_ts)
            WHERE sample_type = 'HKWorkoutTypeIdentifier' AND deleted_at IS NULL
            """,
            """
            CREATE INDEX idx_records_end_active
            ON records(end_ts)
            WHERE deleted_at IS NULL
            """,
            "CREATE INDEX idx_batches_received_at ON batches(received_at)",
        ),
    ),
)

CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version


def migrate_database(
    path: Path,
    *,
    busy_timeout_ms: int = 5_000,
    target_version: int | None = None,
) -> None:
    destination_version = target_version or CURRENT_SCHEMA_VERSION
    if destination_version < 1 or destination_version > CURRENT_SCHEMA_VERSION:
        raise ValueError("target_version is outside the supported migration range")
    path.parent.mkdir(parents=True, exist_ok=True)
    with write_connection(path, busy_timeout_ms=busy_timeout_ms) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
        applied = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        }
        unknown = {version for version in applied if version > CURRENT_SCHEMA_VERSION}
        if unknown:
            raise RuntimeError("The database schema is newer than this application.")

        for migration in MIGRATIONS:
            if migration.version > destination_version or migration.version in applied:
                continue
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in migration.statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, _now_text()),
                )
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()


@contextmanager
def write_connection(
    path: Path,
    *,
    busy_timeout_ms: int = 5_000,
) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(
        path,
        timeout=busy_timeout_ms / 1_000,
        isolation_level=None,
        check_same_thread=False,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms:d}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        yield connection
    finally:
        connection.close()


@contextmanager
def read_connection(
    path: Path,
    *,
    busy_timeout_ms: int = 5_000,
) -> Iterator[sqlite3.Connection]:
    uri = f"{path.absolute().as_uri()}?mode=ro"
    connection = sqlite3.connect(
        uri,
        uri=True,
        timeout=busy_timeout_ms / 1_000,
        isolation_level=None,
        check_same_thread=False,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={busy_timeout_ms:d}")
        connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        connection.close()


@contextmanager
def immediate_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def current_schema_version(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with read_connection(path) as connection:
            row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0) if row is not None else 0


def is_ready(path: Path) -> bool:
    if current_schema_version(path) != CURRENT_SCHEMA_VERSION:
        return False
    try:
        with read_connection(path) as connection:
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            ]
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        return False
    return versions == list(range(1, CURRENT_SCHEMA_VERSION + 1))


def integrity_check(path: Path) -> str:
    with read_connection(path) as connection:
        rows = connection.execute("PRAGMA integrity_check").fetchall()
    return "\n".join(str(row[0]) for row in rows)


def backup_database(source: Path, destination: Path) -> None:
    if source.absolute() == destination.absolute():
        raise ValueError("Backup destination must differ from the source database.")
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with read_connection(source) as source_connection:
            destination_connection = sqlite3.connect(destination)
            try:
                source_connection.backup(destination_connection)
            finally:
                destination_connection.close()
        if integrity_check(destination) != "ok":
            raise RuntimeError("The backup failed its SQLite integrity check.")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

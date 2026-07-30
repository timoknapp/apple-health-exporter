from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from health_coach.cli import main as cli_main
from health_coach.config import ConfigurationError, Settings
from health_coach.db import (
    CURRENT_SCHEMA_VERSION,
    backup_database,
    current_schema_version,
    integrity_check,
    migrate_database,
    read_connection,
    write_connection,
)
from health_coach.ingestion import IngestionService
from health_coach.models import IngestBatch

from .conftest import make_batch, make_record


def test_configuration_accepts_environment_and_secret_files(tmp_path: Path) -> None:
    ingest_file = tmp_path / "ingest-token"
    mcp_file = tmp_path / "mcp-token"
    ingest_file.write_text("i" * 40 + "\n", encoding="utf-8")
    mcp_file.write_text("m" * 40 + "\n", encoding="utf-8")

    settings = Settings.from_mapping(
        {
            "HEALTH_COACH_DB_PATH": str(tmp_path / "db.sqlite3"),
            "HEALTH_COACH_TIMEZONE": "Europe/Berlin",
            "HEALTH_COACH_INGEST_TOKEN_FILE": str(ingest_file),
            "HEALTH_COACH_MCP_TOKEN_FILE": str(mcp_file),
        }
    )

    assert settings.ingest_token == "i" * 40
    assert settings.mcp_token == "m" * 40
    assert settings.max_body_bytes == 20 * 1024 * 1024


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {
            "HEALTH_COACH_INGEST_TOKEN": "same-" + ("x" * 40),
            "HEALTH_COACH_MCP_TOKEN": "same-" + ("x" * 40),
        },
        {
            "HEALTH_COACH_INGEST_TOKEN": "short",
            "HEALTH_COACH_MCP_TOKEN": "m" * 40,
        },
        {
            "HEALTH_COACH_INGEST_TOKEN": "i" * 40,
            "HEALTH_COACH_MCP_TOKEN": "m" * 40,
            "HEALTH_COACH_TIMEZONE": "Not/AZone",
        },
    ],
)
def test_configuration_rejects_unsafe_values(tmp_path: Path, overrides: dict[str, str]) -> None:
    values = {
        "HEALTH_COACH_DB_PATH": str(tmp_path / "db.sqlite3"),
        "HEALTH_COACH_TIMEZONE": "Europe/Berlin",
        **overrides,
    }
    with pytest.raises(ConfigurationError):
        Settings.from_mapping(values)


def test_clean_initialization_and_repeated_migration_are_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "coach.sqlite3"

    migrate_database(database)
    migrate_database(database)

    assert current_schema_version(database) == CURRENT_SCHEMA_VERSION
    assert integrity_check(database) == "ok"
    with write_connection(database) as connection:
        modes = connection.execute("PRAGMA journal_mode").fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        index_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
    assert modes[0].lower() == "wal"
    assert foreign_keys[0] == 1
    assert "idx_records_type_start_active" in index_names
    assert "idx_records_workout_start_active" in index_names
    assert "idx_batches_received_at" in index_names


def test_older_database_upgrades_without_losing_rows(tmp_path: Path) -> None:
    database = tmp_path / "older.sqlite3"
    migrate_database(database, target_version=1)
    with write_connection(database) as connection:
        connection.execute(
            """
            INSERT INTO batches (
                batch_id, payload_sha256, device_id, created_at, timezone, mode,
                received_at, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "11111111-1111-1111-1111-111111111111",
                "a" * 64,
                "22222222-2222-2222-2222-222222222222",
                "2026-01-01T00:00:00.000000Z",
                "Europe/Berlin",
                "incremental",
                "2026-01-01T00:00:01.000000Z",
                "{}",
            ),
        )
        connection.commit()

    migrate_database(database)

    with read_connection(database) as connection:
        row = connection.execute("SELECT batch_id FROM batches").fetchone()
    assert row is not None
    assert row["batch_id"] == "11111111-1111-1111-1111-111111111111"
    assert current_schema_version(database) == CURRENT_SCHEMA_VERSION
    assert integrity_check(database) == "ok"


def test_mcp_read_connection_rejects_writes(tmp_path: Path) -> None:
    database = tmp_path / "coach.sqlite3"
    migrate_database(database)

    with read_connection(database) as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (99, 'bad', 'x')"
            )


def test_online_backup_restores_synthetic_database(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backups" / "backup.sqlite3"
    migrate_database(source)
    with write_connection(source) as connection:
        connection.execute(
            """
            INSERT INTO batches (
                batch_id, payload_sha256, device_id, created_at, timezone, mode,
                received_at, raw_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "11111111-1111-1111-1111-111111111111",
                "b" * 64,
                "22222222-2222-2222-2222-222222222222",
                "2026-01-01T00:00:00.000000Z",
                "Europe/Berlin",
                "incremental",
                "2026-01-01T00:00:01.000000Z",
                "{}",
            ),
        )
        connection.commit()

    backup_database(source, backup)

    assert integrity_check(backup) == "ok"
    with read_connection(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 1


def test_core_metric_query_uses_partial_time_index(tmp_path: Path) -> None:
    database = tmp_path / "coach.sqlite3"
    migrate_database(database)
    records = [
        make_record(
            sample_id=str(UUID(int=index + 1)),
            start=f"2026-07-{(index % 28) + 1:02d}T04:30:00.000Z",
        )
        for index in range(2_000)
    ]
    payload = make_batch(records=records)
    IngestionService(database).ingest(IngestBatch.model_validate(payload), payload)

    with write_connection(database) as connection:
        connection.execute("ANALYZE")
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT numeric_value, start_ts, source_name, source_bundle_id
            FROM records
            WHERE sample_type = ?
              AND record_kind = 'numeric'
              AND deleted_at IS NULL
              AND start_ts >= ?
              AND start_ts < ?
            ORDER BY start_ts, sample_id
            """,
            (
                "HKQuantityTypeIdentifierRestingHeartRate",
                1_783_000_000.0,
                1_786_000_000.0,
            ),
        ).fetchall()

    details = "\n".join(str(row["detail"]) for row in plan)
    assert "USING INDEX idx_records_type_start_active" in details


def test_operator_cli_migrates_checks_and_backs_up(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "coach.sqlite3"
    backup = tmp_path / "backup.sqlite3"

    assert cli_main(["--database", str(database), "migrate"]) == 0
    assert capsys.readouterr().out == f"schema version {CURRENT_SCHEMA_VERSION}\n"
    assert cli_main(["--database", str(database), "integrity-check"]) == 0
    assert capsys.readouterr().out == "ok\n"
    assert cli_main(["--database", str(database), "backup", str(backup)]) == 0
    assert capsys.readouterr().out == "backup complete\n"
    assert integrity_check(backup) == "ok"

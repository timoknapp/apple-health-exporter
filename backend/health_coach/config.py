from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_DATABASE_PATH = Path("/data/coach.sqlite3")
DEFAULT_TIMEZONE = "Europe/Berlin"
DEFAULT_MAX_BODY_BYTES = 20 * 1024 * 1024
DEFAULT_MCP_MAX_BODY_BYTES = 1024 * 1024
MINIMUM_TOKEN_LENGTH = 32


class ConfigurationError(ValueError):
    """Raised when required runtime configuration is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: Path
    timezone: str
    ingest_token: str
    mcp_token: str
    allowed_hosts: tuple[str, ...]
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    mcp_max_body_bytes: int = DEFAULT_MCP_MAX_BODY_BYTES
    sqlite_busy_timeout_ms: int = 5_000

    @classmethod
    def from_env(cls) -> Settings:
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> Settings:
        database_value = values.get("HEALTH_COACH_DB_PATH", str(DEFAULT_DATABASE_PATH)).strip()
        if not database_value:
            raise ConfigurationError("HEALTH_COACH_DB_PATH must not be empty.")
        database_path = Path(database_value).expanduser().absolute()

        timezone = values.get("HEALTH_COACH_TIMEZONE", DEFAULT_TIMEZONE).strip()
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as error:
            raise ConfigurationError("HEALTH_COACH_TIMEZONE must be an IANA timezone.") from error

        ingest_token = _load_secret(
            values,
            value_name="HEALTH_COACH_INGEST_TOKEN",
            file_name="HEALTH_COACH_INGEST_TOKEN_FILE",
        )
        mcp_token = _load_secret(
            values,
            value_name="HEALTH_COACH_MCP_TOKEN",
            file_name="HEALTH_COACH_MCP_TOKEN_FILE",
        )
        if hmac.compare_digest(ingest_token, mcp_token):
            raise ConfigurationError("The ingestion and MCP tokens must be distinct.")

        allowed_hosts_value = values.get("HEALTH_COACH_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
        allowed_hosts = tuple(
            host.strip() for host in allowed_hosts_value.split(",") if host.strip()
        )
        if not allowed_hosts:
            raise ConfigurationError("HEALTH_COACH_ALLOWED_HOSTS must contain at least one host.")

        return cls(
            db_path=database_path,
            timezone=timezone,
            ingest_token=ingest_token,
            mcp_token=mcp_token,
            allowed_hosts=allowed_hosts,
        )


def _load_secret(
    values: Mapping[str, str],
    *,
    value_name: str,
    file_name: str,
) -> str:
    direct_value = values.get(value_name)
    file_value = values.get(file_name)
    if direct_value is not None and file_value is not None:
        raise ConfigurationError(f"Set only one of {value_name} and {file_name}.")
    if file_value is not None:
        path = Path(file_value).expanduser()
        try:
            secret = path.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise ConfigurationError(
                f"Could not read the secret file configured by {file_name}."
            ) from error
    elif direct_value is not None:
        secret = direct_value.strip()
    else:
        raise ConfigurationError(f"Set {value_name} or {file_name}.")

    if len(secret) < MINIMUM_TOKEN_LENGTH:
        raise ConfigurationError(f"{value_name} must contain at least 32 characters.")
    if any(character.isspace() for character in secret):
        raise ConfigurationError(f"{value_name} must not contain whitespace.")
    return secret

# Private Health Coach Backend

This directory contains the production service for the Apple Health exporter. It
is a single-user FastAPI/Starlette application with:

- authenticated REST ingestion at `POST /v1/healthkit/ingest`;
- a persistent migration-backed SQLite database;
- authenticated, read-only MCP Streamable HTTP at exactly `/mcp`; and
- bounded, deterministic fitness analytics.

`../app/Tools/mock_ingestion_server.py` is a separate test fixture. It does not
persist health data and must not be used as the production service.

This is fitness-coaching infrastructure, not a medical device. It does not
diagnose, treat, monitor emergencies, or provide medical advice.

## Architecture and trust boundaries

```text
iPhone exporter                     Trusted MCP client
      |                                     |
      | HTTPS + ingestion token             | HTTPS + distinct MCP token
      v                                     v
reverse proxy/private ingress (TLS, body and timeout policy)
      |
      v
single Uvicorn worker
  +-- FastAPI REST routes --------------------+
  |   /healthz, /readyz                       |
  |   /v1/healthkit/ingest                    |
  +-------------------------------------------+
  +-- official MCP Python SDK ----------------+
      stateless Streamable HTTP /mcp
      seven allowlisted read-only tools
      two small read-only resources
                    |
                    v
        /data/coach.sqlite3 (SQLite/WAL)
```

Trust boundaries:

1. TLS terminates in a trusted reverse proxy or private-network ingress.
2. The ingestion token authorizes writes through one fixed REST route.
3. The distinct MCP token authorizes only the bounded MCP projection.
4. MCP opens fresh SQLite connections with `mode=ro` and
   `PRAGMA query_only=ON`.
5. Neither interface logs request bodies, authorization headers, health values,
   identifiers, raw metadata, query strings, or MCP results.

The service intentionally runs one process and one Uvicorn worker. SQLite uses
WAL mode, foreign keys, a five-second busy timeout, explicit write
transactions, and independent parallel read connections. Stateless MCP calls
can run in parallel. Do not increase the worker count without separately
proving migration startup, SQLite writes, and the chosen MCP session model.

## Dependencies and runtime

- Python `3.14.x`
- FastAPI `0.139.2`
- official `mcp` Python SDK `1.28.1` (stable v1 line, not the v2 prerelease)
- Pydantic `2.13.4`
- Uvicorn `0.51.0`

`requirements.lock` and `requirements-dev.lock` pin every resolved package and
include hashes. The Docker image pins Python `3.14.6-slim-bookworm` by manifest
digest.

## Configuration reference

| Variable | Required | Default | Purpose |
|---|---:|---|---|
| `HEALTH_COACH_DB_PATH` | no | `/data/coach.sqlite3` | Persistent SQLite file. |
| `HEALTH_COACH_TIMEZONE` | no | `Europe/Berlin` | IANA timezone used for local-day analytics. |
| `HEALTH_COACH_ALLOWED_HOSTS` | no | `localhost,127.0.0.1,[::1]` | Comma-separated HTTP Host allowlist. Add the trusted proxy hostname in deployment. |
| `HEALTH_COACH_INGEST_TOKEN` | one form | none | Ingestion bearer token. |
| `HEALTH_COACH_INGEST_TOKEN_FILE` | one form | none | File containing the ingestion token. |
| `HEALTH_COACH_MCP_TOKEN` | one form | none | MCP bearer token. |
| `HEALTH_COACH_MCP_TOKEN_FILE` | one form | none | File containing the MCP token. |

For each token, set either the value or the file variable, never both. Tokens
must be distinct, contain at least 32 non-whitespace characters, and should be
generated from at least 32 random bytes. Docker secrets are preferred. From the
repository root:

```sh
cd backend
umask 077
mkdir -p secrets
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > secrets/ingest_token
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > secrets/mcp_token
chmod 0700 secrets
chmod 0444 secrets/ingest_token secrets/mcp_token
```

The commands do not place token values in shell history. Compose bind-mounts
file-backed secrets without remapping their owner, so the files must be readable
by fixed container UID `10001`. The containing `0700` directory prevents other
host users from traversing to those `0444` files. Keep `secrets/` outside
backups that are not encrypted and never commit it.

`HEALTH_COACH_BIND_ADDRESS` and `HEALTH_COACH_PORT` are Compose-only settings
from `.env.example`; they control host publication, not the application.

## Local development without Docker

```sh
cd backend
python3.14 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m pip install --no-deps -e .

export HEALTH_COACH_DB_PATH="$PWD/.local/coach.sqlite3"
export HEALTH_COACH_TIMEZONE=Europe/Berlin
export HEALTH_COACH_INGEST_TOKEN_FILE="$PWD/secrets/ingest_token"
export HEALTH_COACH_MCP_TOKEN_FILE="$PWD/secrets/mcp_token"
export HEALTH_COACH_ALLOWED_HOSTS=127.0.0.1,localhost

.venv/bin/python -m health_coach migrate
.venv/bin/python -m uvicorn health_coach.app:create_app \
  --factory --host 127.0.0.1 --port 8080 --workers 1 --no-access-log
```

Plain HTTP is suitable only on loopback or a trusted private development
network. Release iPhone uploads require HTTPS with normal certificate
validation.

## Docker and Compose

From the repository root, enter the backend directory. Copy `.env.example` to
`.env` only if host defaults need changing. The file must not contain tokens.
All later `docker compose` examples assume this directory.

```sh
cd backend
docker compose config
docker compose build --pull
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8080/readyz
```

Compose publishes loopback only by default, mounts named `/data` and `/backups`
volumes, uses Docker secrets, drops all Linux capabilities, enables
`no-new-privileges`, uses a read-only root filesystem and `/tmp` tmpfs, starts
an init process, and runs as fixed UID/GID `10001`.

Do not change `HEALTH_COACH_BIND_ADDRESS` to a public interface. Put a trusted
TLS proxy or private ingress in front of the loopback listener.

## HTTP endpoints

### `GET /healthz`

Unauthenticated liveness only:

```json
{"status":"ok"}
```

### `GET /readyz`

Unauthenticated readiness. It verifies that SQLite opens and every required
migration is present. It reveals no dates, counts, metric names, or health data:

```json
{"status":"ready"}
```

### `GET /v1/healthkit/status`

Authenticated, privacy-safe database status for the iPhone app and operator:

```text
Authorization: Bearer <ingestion-token>
```

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-07-21T12:45:00.000000Z",
  "state": "current",
  "latestAcceptedBatchAt": "2026-07-21T12:40:00.000000Z",
  "latestRecordAt": "2026-07-21T11:30:00.000000Z",
  "activeRecordCount": 152340,
  "domains": {
    "recovery": {
      "state": "current",
      "latestRecordAt": "2026-07-21T11:30:00.000000Z",
      "recordCount": 8320
    },
    "sleep": {
      "state": "current",
      "latestRecordAt": "2026-07-21T06:45:00.000000Z",
      "recordCount": 2450
    },
    "activity": {
      "state": "current",
      "latestRecordAt": "2026-07-21T11:25:00.000000Z",
      "recordCount": 140000
    },
    "workouts": {
      "state": "current",
      "latestRecordAt": "2026-07-20T18:00:00.000000Z",
      "recordCount": 1570
    }
  },
  "databaseSchemaVersion": 2,
  "warnings": []
}
```

States are `current`, `stale`, or `empty`; data becomes stale when its latest
active record is more than three days old. Counts exclude deleted rows. The
response contains no sample, batch, device, source, metadata, raw payload, or
health measurement values. It uses the ingestion token so the app needs no
second credential.

### `POST /v1/healthkit/ingest`

Required headers:

```text
Authorization: Bearer <INGEST_TOKEN>
Content-Type: application/json
Idempotency-Key: <batchId>
```

The body is the unchanged schema-v1 contract from
[`../app/JSON-SCHEMA.md`](../app/JSON-SCHEMA.md). The
backend reads the request incrementally and rejects content beyond exactly
20 MiB even when `Content-Length` is absent.

| Status | Meaning |
|---:|---|
| `201` | New batch committed atomically. |
| `200` | Same batch ID and canonically equivalent accepted content. |
| `400` | Malformed JSON or invalid `Content-Length`. |
| `401` | Missing or invalid ingestion token. |
| `409` | Batch ID reused with different normalized content. |
| `413` | Body exceeds 20 MiB. |
| `415` | Media type is not `application/json`. |
| `422` | Missing/mismatched idempotency key, invalid schema, or unsupported schema version. |
| `499` | Client disconnected before the upload completed. |
| `500` | Sanitized unexpected server failure. |

Successful bodies are exactly:

```json
{"batchId":"11111111-1111-1111-1111-111111111111"}
```

Unknown object fields are ignored for schema-v1 compatibility. The first
accepted raw JSON objects are retained for future reprocessing, while indexed
normalized columns support queries. A tombstone received before or after its
upsert remains authoritative. Every accepted batch, upsert, and deletion is one
SQLite transaction.

Synthetic example:

```sh
curl --fail-with-body \
  -H 'Authorization: Bearer <INGEST_TOKEN>' \
  -H 'Idempotency-Key: 11111111-1111-1111-1111-111111111111' \
  -H 'Content-Type: application/json' \
  --data-binary '{
    "schemaVersion":1,
    "batchId":"11111111-1111-1111-1111-111111111111",
    "deviceId":"22222222-2222-2222-2222-222222222222",
    "createdAt":"2026-07-16T14:45:00.000Z",
    "timezone":"Europe/Berlin",
    "mode":"incremental",
    "records":[],
    "deletedRecords":[]
  }' \
  https://<private-health-host>/v1/healthkit/ingest
```

Use placeholders only in copied commands. Do not paste a real token into shell
history.

## MCP

- URL: `https://<private-health-host>/mcp`
- transport: `streamable-http`
- authentication: `Authorization: Bearer <MCP_TOKEN>`
- implementation: official Python SDK `FastMCP`
- mode: stateless HTTP with JSON responses
- parallel calls: supported
- database access: read-only URI plus `PRAGMA query_only=ON`
- prompts: none

All MCP requests require the MCP token, including initialization and
list/read/call requests. Browser-origin requests are rejected; CORS is not
enabled. `/mcp/mcp` is not a valid endpoint.

### Tools

| Tool | Limits and behavior |
|---|---|
| `health_get_data_freshness` | Broad recovery/sleep/activity/workout freshness and counts only; no identifiers. |
| `health_get_daily_summary` | One local date; optional IANA timezone. Missing fields are `null` and listed. |
| `health_get_metric_series` | Allowlisted metric; optional end date/timezone; aggregate range <=366 days; raw range <=31 days and <=1,000 points. |
| `health_get_sleep_summary` | Optional end date/timezone; <=180 days; merges overlapping stages and selects one source per night. |
| `health_get_workouts` | Optional end date/timezone and allowlisted sport; <=366 days; limit 1-200; overlap deduplication. |
| `health_get_training_load` | Optional end date/timezone; 7-365 days; count/duration/distributions only, without TRIMP/TSS/zones. |
| `health_get_recovery_summary` | Optional end date/timezone; recent 1-14 days; baseline 14-90 days and longer than recent; no readiness score. |

Metric names:

```text
hrv_sdnn, resting_heart_rate, respiratory_rate,
sleeping_wrist_temperature, heart_rate_recovery_one_minute, vo2_max,
oxygen_saturation, body_mass, body_fat_percentage, lean_body_mass,
active_energy, exercise_time, stand_time, step_count, physical_effort,
workout_heart_rate, cycling_power, cycling_cadence, cycling_speed
```

Aggregations are metric-specific and drawn from `daily_median`, `daily_mean`,
`daily_sum`, `latest`, and `raw`. The metric catalog resource is authoritative.
Additive metrics use one deterministic preferred source per local day rather
than summing overlapping sources.

Sports include every named `HKWorkoutActivityType` exposed by HealthKit, using
snake-case names such as `running`, `cycling`, `tennis`, `table_tennis`,
`functional_strength_training`, and `swim_bike_run`. The original `strength`
and `skiing` names remain available as aggregate filters. Responses also
include the raw numeric `activityType`; unknown future values use `other`
without losing that raw value.

### Resources

- `healthcoach://catalog/metrics`
- `healthcoach://about/data-policy`

Neither resource exposes database files or records.

### Example result

Synthetic `health_get_metric_series` result:

```json
{
  "schemaVersion": 1,
  "metric": "resting_heart_rate",
  "unit": "count/min",
  "aggregation": "daily_median",
  "timezone": "Europe/Berlin",
  "range": {"startDate": "2026-07-16", "endDate": "2026-07-16"},
  "points": [{"date": "2026-07-16", "value": 49.0}],
  "sourcePolicy": "preferred-source-per-local-day",
  "dataCoverage": {
    "records": 1,
    "daysWithData": 1,
    "rangeDays": 1,
    "missingDays": 0
  },
  "warnings": []
}
```

Actual responses also include `generatedAt` and aggregation semantics.

## OpenClaw registration

This configuration was verified against the official OpenClaw MCP documentation
on 2026-07-19. OpenClaw stores remote servers under `mcp.servers` in
`~/.openclaw/openclaw.json`:

```json5
{
  mcp: {
    servers: {
      "health-coach": {
        url: "https://<private-health-host>/mcp",
        transport: "streamable-http",
        headers: {
          Authorization: "Bearer <MCP_TOKEN>",
        },
        sslVerify: true,
        requestTimeoutMs: 30000,
        connectionTimeoutMs: 10000,
        supportsParallelToolCalls: true,
        toolFilter: {
          include: ["health_*"],
        },
      },
    },
  },
}
```

As of the verified OpenClaw documentation, static MCP header values are local
configuration values rather than an MCP-header SecretRef surface. Edit the
config with a trusted local editor, set its permissions to `0600`, keep it out
of version control and backups that are not encrypted, and do not place the
real token in a command argument or shell history. Re-check
`openclaw config schema` after upgrading OpenClaw in case a safer secret
reference becomes available. Do not set `sslVerify: false`.

Verification:

```sh
openclaw mcp status --verbose
openclaw mcp doctor health-coach
openclaw mcp probe health-coach --json
openclaw agent \
  --message 'Call health_get_data_freshness and return its complete JSON result.' \
  --json
```

`doctor` warns about literal sensitive header values; that warning is expected
for this static-token setup and reinforces the local-file protection
requirement. `probe` must list the `health_*` tools. There is no dedicated
`openclaw mcp invoke` command; an actual call runs through `openclaw agent`.
No real OpenClaw configuration is changed by this repository.

Official references:

- <https://docs.openclaw.ai/cli/mcp>
- <https://docs.openclaw.ai/gateway/configuration-reference#mcp>

## HTTPS reverse proxy

The Python process intentionally does not terminate TLS. A private Nginx
example is below; supply a certificate trusted by the iPhone and MCP client:

```nginx
server {
    listen 443 ssl;
    server_name <private-health-host>;

    ssl_certificate     /etc/nginx/tls/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/private-key.pem;

    # 21 MiB permits the 20 MiB contract plus framing; the app enforces 20 MiB.
    client_max_body_size 21m;
    access_log off;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_read_timeout 10m;
        proxy_send_timeout 10m;

        proxy_pass_request_headers off;
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Content-Type $http_content_type;
        proxy_set_header Content-Length $http_content_length;
        proxy_set_header Accept $http_accept;
        proxy_set_header Idempotency-Key $http_idempotency_key;
        proxy_set_header MCP-Protocol-Version $http_mcp_protocol_version;

        add_header Cache-Control "no-store" always;
    }
}
```

Set `HEALTH_COACH_ALLOWED_HOSTS=<private-health-host>,localhost,127.0.0.1`.
Keep direct container HTTP loopback-only. Do not log authorization headers or
bodies, do not cache responses, and do not disable certificate validation.
Private CA roots must be installed normally on both clients. Tailscale HTTPS or
another trusted private ingress is also suitable if it preserves Streamable
HTTP, the body limit, headers, and long request timeouts.

## Database initialization and migration

Startup automatically applies ordered, idempotent migrations. Explicit command:

```sh
docker compose run --rm --no-deps health-coach \
  python -m health_coach --database /data/coach.sqlite3 migrate
```

Migrations are additive and do not rebuild the records table. The initial
schema stores batches, normalized records, tombstones, and raw accepted JSON.
The second migration adds partial sample/workout/time indexes and a batch
receipt index. Tests cover clean initialization, repeated migration, upgrade
from schema v1, row preservation, and integrity.

### Move an existing database into the volume

Only databases produced by this backend's schema are supported.

1. Stop writes to the old backend.
2. Use this backend's `backup` command (SQLite backup API) to produce a
   consistent offline `coach.sqlite3`; never copy a live database file alone
   while its WAL may contain committed pages.
3. Stop the Compose service.
4. Copy the offline backup into `/data/coach.sqlite3` through a one-shot
   Compose container with the data volume mounted.
5. Start the service; automatic migrations run.
6. Run the integrity check and `/readyz`.

For a stopped service and a backup already in `/backups`:

```sh
docker compose stop health-coach
docker compose run --rm --no-deps health-coach sh -eu -c '
  test -f /backups/coach.sqlite3
  test ! -e /data/coach.sqlite3
  cp /backups/coach.sqlite3 /data/coach.sqlite3
'
docker compose up -d
docker compose exec health-coach \
  python -m health_coach --database /data/coach.sqlite3 integrity-check
```

If `/data/coach.sqlite3` already exists, archive it safely first rather than
overwriting it.

## Backup, restore, and integrity

Online backup uses `sqlite3.Connection.backup`, includes committed WAL content,
refuses to overwrite an existing destination, and verifies the result:

```sh
docker compose exec health-coach \
  python -m health_coach --database /data/coach.sqlite3 \
  backup /backups/coach-2026-07-19.sqlite3
```

Copy completed backups to encrypted operator-controlled storage. Test restores
regularly.

Offline restore:

1. Stop the service.
2. Preserve the current database with the backup API before maintenance.
3. Remove/rename the stopped database and any matching `-wal`/`-shm` files.
4. Copy the selected verified backup to `/data/coach.sqlite3`.
5. Start the service and allow migrations to run.
6. Run:

```sh
docker compose exec health-coach \
  python -m health_coach --database /data/coach.sqlite3 integrity-check
curl --fail https://<private-health-host>/readyz
```

Do not restore over a running database.

## Token rotation

1. Generate two new distinct token files with `umask 077` and
   `secrets.token_urlsafe(32)`.
2. Replace the Docker secret source files atomically.
3. Recreate the container: `docker compose up -d --force-recreate`.
4. Update the iPhone ingestion token and choose **Retry Queue**.
5. Update the protected OpenClaw MCP header locally and run `doctor --probe`.
6. Delete obsolete token copies from operator-controlled secret storage.

Rotation is immediate; there is no dual-token grace period.

## Logging and privacy

Application logs contain only:

- generated request ID;
- HTTP method;
- normalized route (`/mcp`, not a tool or query string);
- status; and
- duration.

Uvicorn access logging is disabled. The service has no analytics, telemetry,
CORS, browser UI, arbitrary SQL, file, import, migration, write, or admin MCP
tool. MCP output omits batch IDs, device IDs, sample IDs, source UUIDs, raw
JSON, metadata JSON, full device descriptions, tokens, and file paths.

The SQLite database and its backups contain sensitive health information,
provenance, and raw accepted records. Protect the host, volume, backups,
operator account, proxy, and OpenClaw config accordingly. Docker volume
encryption is an operator/host responsibility.

## Verification

```sh
cd backend
.venv/bin/ruff format --check health_coach tests
.venv/bin/ruff check health_coach tests
.venv/bin/mypy health_coach
.venv/bin/pytest
cd ../app
swift test
cd ../backend
docker compose config --quiet

# Requires a running Docker daemon:
HEALTH_COACH_RUN_DOCKER_TESTS=1 .venv/bin/pytest tests/test_container.py
```

The Docker smoke builds the image, starts it with temporary secrets and a
persistent volume, checks readiness, ingests a synthetic batch, performs an
official MCP client call, restarts the container, proves duplicate persistence,
checks UID `10001`, and scans `/app` for forbidden data/secret artifacts.

## Troubleshooting

- **Permission denied for `/data`:** ensure the volume is writable by UID/GID
  `10001`; do not run the service as root to hide ownership errors.
- **Database is locked:** keep one worker, identify another writer or long
  maintenance transaction, and do not delete WAL files while running.
- **401 ingestion/MCP:** verify the correct distinct token and token file, then
  recreate the container after secret changes.
- **403 on MCP:** browser `Origin` requests are intentionally rejected. Use a
  trusted non-browser MCP client.
- **400 invalid Host:** add only the actual trusted proxy hostname to
  `HEALTH_COACH_ALLOWED_HOSTS`.
- **`/mcp/mcp` fails:** correct the client URL to the exact `/mcp`.
- **Proxy MCP failures/timeouts:** disable response/request buffering, preserve
  required MCP headers, and use long read/send timeouts.
- **413 uploads:** the JSON body exceeds the fixed 20 MiB backend limit or the
  proxy is configured below it.
- **Stale data warnings:** check iPhone queue/status, ingestion authentication,
  proxy reachability, and `/readyz`; missing HealthKit permission and no data
  remain intentionally indistinguishable on iOS.
- **Readiness failure:** run the migration and integrity commands and inspect
  only privacy-safe operational logs.

## Known limitations and security assumptions

- One trusted user and one trusted operator; no accounts or multi-tenancy.
- TLS, host storage encryption, firewalling, backup encryption, and retention
  are external operator responsibilities.
- Static bearer tokens are the private-v1 authentication mechanism; there is no
  OAuth authorization server.
- Source preference is deterministic but cannot reconstruct HealthKit's private
  source-correlation algorithms. Responses warn when overlapping sources were
  reduced.
- Unknown future HealthKit workout activity types use the `other` sport label,
  while responses preserve their raw numeric `activityType`.
- No GPS, ECG, medication, clinical, symptom, reproductive, cycle, route,
  Strava ingestion, dashboard, scheduler, or HealthKit write capability.
- Real-device HealthKit behavior, external TLS reachability, and actual
  OpenClaw connectivity must be verified by the operator in those environments.

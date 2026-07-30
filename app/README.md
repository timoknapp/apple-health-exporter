# HealthKit Exporter

A focused, read-only iPhone bridge that exports a deliberately limited set of
Apple Health samples to one user-controlled HTTPS fitness-coaching backend. It
preserves raw samples, queues every payload durably, and uses stable batch IDs
so retries are idempotent.

This is a personal fitness data tool, not a medical app, dashboard, workout
recorder, or general-purpose HealthKit dump. It does not write to HealthKit and
does not include GPS routes, ECGs, clinical data, medications, symptoms,
reproductive data, or cycle data.

## Requirements

- Xcode with the iOS 17 SDK or later
- An Apple Developer team and an iPhone capable of HealthKit
- A private HTTPS server implementing `POST /v1/healthkit/ingest`

The production server is maintained separately under
[`../backend/`](../backend/README.md). It provides authenticated REST ingestion,
persistent SQLite storage, and a separate authenticated read-only MCP endpoint
for a trusted fitness coach.

The app uses only Apple frameworks: SwiftUI, HealthKit, Foundation/URLSession,
Security/Keychain, BackgroundTasks, CryptoKit, and OSLog. There are no package
dependencies, accounts, analytics, advertising, telemetry, cloud SDKs, or
iCloud capability. `App/PrivacyInfo.xcprivacy` declares Health/Fitness and the
pseudonymous device identifier for app functionality, plus the approved
first-party `UserDefaults` required-reason API use.

## Xcode setup and signing

1. Open `HealthKitExporter.xcodeproj`.
2. Select the `HealthKitExporter` target, then **Signing & Capabilities**.
3. Select your Apple Developer team and change the bundle identifier if needed.
4. Confirm the **HealthKit** capability is present and that background delivery
   is enabled. The checked-in entitlements contain:
   `com.apple.developer.healthkit` and
   `com.apple.developer.healthkit.background-delivery`.
5. If the bundle identifier changes, update
   `BackgroundSyncRegistrar.refreshIdentifier`,
   `BGTaskSchedulerPermittedIdentifiers` in `App/Info.plist`, and the Keychain
   service string together.
6. Build and run on an iPhone. HealthKit background delivery cannot be tested
   in Simulator.

The deployment target is iOS 17 because `physicalEffort`, cycling power,
cycling cadence, and cycling speed are part of the selected version-one
contract.

## App setup

1. Enter an HTTPS base URL such as `https://fitness.example`. The app also
   accepts the exact URL `https://fitness.example/v1/healthkit/ingest`; other
   paths, URL credentials, queries, fragments, and HTTP are rejected.
2. Enter the API token. The client sends it as
   `Authorization: Bearer <token>`.
3. Enable any optional oxygen/body-composition metrics and save.
4. Use **Test Connection**. It sends a schema-valid batch with empty
   `records`/`deletedRecords` and no health values. This explicit test posts
   directly to the ingestion endpoint.
5. Review the destination host and complete **I Consent to Upload**. Consent is
   invalidated logically whenever the host or selected metrics change.
6. Tap **Request Read Authorization**. The request is read-only
   (`toShare: []`). HealthKit intentionally does not disclose whether an
   individual read permission was denied, so the app never labels a read type
   as granted or denied.
7. Use **Sync Now**, or choose a bounded date range under **Backfill**.
8. Open **Status** to see active backend record counts and freshness for
   recovery, sleep, activity, and workouts.

The required server response for every successful 2xx request is:

```json
{"batchId":"the-uploaded-batch-uuid"}
```

The server must use `Idempotency-Key` to return the same acceptance for a
duplicate batch. HTTP 408, 429, and 5xx responses are retried with bounded
exponential backoff and jitter. `Retry-After` is honored for 429 and 503.
Other 4xx responses are retained as permanent/configuration failures until the
user explicitly chooses **Retry Queue** after fixing the configuration.

Before a queued payload upload run, the app sends one unauthenticated
`GET /readyz` request to the configured backend origin with a five-second
timeout. Only a successful 2xx response permits payload uploads. An unavailable
backend leaves queued batches and their retry metadata untouched for a later
opportunity. The app does not inspect the current SSID or request Wi-Fi/location
access.

The Status screen also sends authenticated `GET /v1/healthkit/status` requests
when the app becomes active, after connection/sync/retry/backfill actions, and
on pull-to-refresh. The last successful response is stored with iOS file
protection and remains visible offline only for the same configured backend
origin. The app does not poll continuously.

See [JSON-SCHEMA.md](JSON-SCHEMA.md) for the complete contract.

Debug builds additionally accept `http://` only for loopback, `.local`,
link-local, and RFC 1918 private hosts. This exists solely for local feature
testing: the token and health payload are unencrypted. Release builds always
reject HTTP, and the project does not enable global arbitrary ATS loads.

## Reliability model

- Every sample type owns an independent archived `HKQueryAnchor`.
- A type with no anchor records `predicateStart = now`, runs an empty anchored
  query with that fixed lower bound, drains pre-bootstrap deletion tombstones
  without exporting them, and durably queues the empty bootstrap result before
  atomically storing the anchor and lower bound. Every later query reuses the
  same predicate and exports new tombstones. Historical samples are exported
  through Backfill.
- Predicate boundaries are normalized before the first query and stored beside
  the anchor so later queries reconstruct the exact same HealthKit predicate.
- A query result is encoded and atomically written to the local outbox before
  its anchor is committed.
- A crash after enqueue but before anchor commit reuses a deterministic batch
  ID and retains the first exact encoded payload.
- Reset and queue-clear operations wait for in-flight per-type queries; anchor
  reset pauses and safely re-registers observers so an old query cannot restore
  a stale anchor afterward.
- Deleted HealthKit UUIDs are exported.
- One type failing does not stop other types.
- Incremental catch-up is capped at one 100-change page per type per run.
  Additional changes remain behind the saved anchor and continue on later
  manual, activation, observer, or background opportunities.
- Network work is capped at ten outbox uploads per opportunity, so a large
  recovered queue cannot monopolize one foreground or background run.
- Each upload run probes the same-origin `/readyz` endpoint once. If the backend
  is unavailable, all payload uploads are postponed without incrementing batch
  retry counters; the Status screen records the reachability result.
- Workout upserts trigger bounded heart-rate/cycling queries for their exact
  windows before the workout anchor commits, covering late workout imports.
- Outbox files and backfill checkpoints use iOS file protection and are
  excluded from device backup.
- Backfill uses one-month date chunks and 100-change query pages. Its cursor is
  separate from incremental anchors and is saved only after any resulting
  payload is queued. Each durable non-empty page also triggers a bounded upload
  attempt before the next HealthKit page is queried, preventing a healthy
  connection from accumulating the full backfill locally.
- Pending selection caches only lightweight outbox metadata and loads at most
  ten full payloads per upload run. Upload runs are serialized across foreground,
  background, and observer triggers so a large recovered queue stays
  memory-bounded and a batch is not uploaded concurrently more than once.
- Observer callbacks complete as soon as the corresponding anchored result is
  safely queued; they do not wait for network upload.
- Anchored catch-up and pending uploads are attempted after app launch,
  activation, protected-data availability, manual sync/retry, and best-effort
  `BGAppRefreshTask` opportunities. Observer events query their specific type,
  complete after durable queueing, and then attempt uploads.

Delivery is eventually consistent, not real-time. iOS decides when background
delivery and app refresh run; they may be delayed while locked, in Low Power
Mode, after force-quit, or for other system policy reasons. HealthKit data can
be inaccessible while the device is locked. Such query failures leave anchors
unchanged and are retried later.

HealthKit does not expose a current unfiltered anchor without traversing the
existing database. To avoid an uncontrolled multi-year foreground scan,
continuous sync begins at its persisted `predicateStart`. Rerun Backfill to
reconcile upserts before that date. HealthKit cannot surface deletions of
pre-bootstrap samples through this bounded arrangement; that remains a known
platform/architecture limitation for version 1.

Apple references:

- [Authorizing access to health data](https://developer.apple.com/documentation/healthkit/authorizing-access-to-health-data)
- [HKObserverQuery](https://developer.apple.com/documentation/healthkit/hkobserverquery)
- [enableBackgroundDelivery](https://developer.apple.com/documentation/healthkit/hkhealthstore/1614175-enablebackgrounddelivery)
- [Using background tasks](https://developer.apple.com/documentation/backgroundtasks/using-background-tasks-to-update-your-app)

## Production backend

The production service is intentionally outside this app tree. From the
repository root:

```sh
cd backend
docker compose config
docker compose build
docker compose up -d
```

See [`../backend/README.md`](../backend/README.md) for architecture, environment and
Docker-secret configuration, HTTPS proxy requirements, the complete ingestion
contract, MCP tools/resources and OpenClaw registration, migration and
backup/restore procedures, token rotation, privacy guarantees, tests, and
troubleshooting.

The production backend preserves this exporter’s schema-v1 request contract.
It does not change HealthKit selection, synchronization, anchors, retry
behavior, or the Test Connection flow.

## Local mock ingestion server (test-only)

`Tools/mock_ingestion_server.py` uses only Python's standard library and never
logs request bodies. It is a deterministic iOS retry/contract fixture, not a
database or production backend. Bind it to `0.0.0.0` only on a trusted LAN:

```sh
python3 Tools/mock_ingestion_server.py \
  --host 0.0.0.0 --mode success --port 8787
curl -i \
  -H 'Authorization: Bearer test-token' \
  -H 'Idempotency-Key: 11111111-1111-1111-1111-111111111111' \
  -H 'Content-Type: application/json' \
  --data '{"batchId":"11111111-1111-1111-1111-111111111111"}' \
  http://127.0.0.1:8787/v1/healthkit/ingest
```

Modes are `success`, `401`, `403`, `408`, `429`, `500`, `503`, `timeout`, and
`connection-loss`. The success mode remembers idempotency keys and marks
duplicates in its response. Pass `--cert` and `--key` to serve TLS. A Debug
device build may instead use the restricted local HTTP exception above.

## Build and test

The deterministic contract/coordinator suite can also run directly on macOS:

```sh
swift test
```

The checked-in Xcode test target runs the same tests for iOS:

```sh
xcodebuild \
  -project HealthKitExporter.xcodeproj \
  -scheme HealthKitExporter \
  -destination 'platform=iOS Simulator,name=iPhone 16 Pro' \
  test
```

Choose any installed iOS simulator if that named runtime is unavailable.
Simulator tests cover the contract and coordination logic, but they do not
validate HealthKit background delivery. Complete
[REAL-DEVICE-TEST-PLAN.md](REAL-DEVICE-TEST-PLAN.md) before making any
background-delivery claim.

## Privacy and operations

The API secret is stored only in Keychain. Endpoint settings and consent contain
no token. Health payloads are local only until sent to the configured private
host, are excluded from backup, and are removed only after a matching 2xx batch
acceptance. Operational status and exported diagnostics never include health
values, payload bodies, API tokens, sample UUIDs, or the pseudonymous device ID.

See [PRIVACY.md](PRIVACY.md) for the full data-handling statement and
[REAL-DEVICE-TEST-PLAN.md](REAL-DEVICE-TEST-PLAN.md) for destructive and
recovery scenarios.
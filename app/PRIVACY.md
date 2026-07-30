# Privacy

HealthKit Exporter is a private fitness-data bridge for one user and one
user-configured server. It is not a medical application and does not provide
diagnosis, treatment, emergency monitoring, or medical advice.

## Data read

The app requests read access only to the selected recovery, activity, workout,
and workout-window measurement types documented in `JSON-SCHEMA.md`. Optional
oxygen saturation and body-composition types default off. It does not request
write access and never creates, changes, or deletes HealthKit objects.

Version 1 excludes routes/location, ECG, medication, symptom, reproductive,
cycle, and clinical data. Apple Watch samples already synchronized to the
iPhone are read through the iPhone HealthKit store; there is no watchOS app.

HealthKit protects read authorization privacy. A successful authorization
request does not prove that the user granted a given read type, and denied
access is intentionally indistinguishable from no data. The UI reflects this
and does not infer permission decisions.

## Transmission and consent

Before the first health upload, the app displays the selected HealthKit types
and the exact destination host. Consent is bound to that host and metric list.
Changing either requires renewed consent.

Release uploads go only to the configured HTTPS host at
`/v1/healthkit/ingest`, using App Transport Security. HTTP, embedded URL
credentials, query parameters, and fragments are rejected. Debug builds allow
HTTP only for local/private test hosts; this sends the token and test health
payload unencrypted and must be used only on a trusted LAN. The API token is
sent in the `Authorization` header and is not part of the JSON payload.

Before queued payload uploads, the app makes one unauthenticated request to the
same configured origin at `/readyz`. This request contains no token or health
payload. If the backend is unavailable, queued health data remains on-device.
The app does not read or store the current Wi-Fi SSID and does not request
location or Wi-Fi-information access for network gating.

The operator of the configured server controls server-side retention, access,
backup, and deletion. Users should configure only a server they trust and
protect it with TLS, authentication, authorization, encrypted storage, and
appropriate retention controls.

The optional production service in `../backend/` stores accepted raw records,
normalized query fields, source/device provenance, deletion tombstones, and
batch metadata in the operator-controlled SQLite volume. Its MCP interface is a
separately authenticated, bounded, read-only projection that omits raw
identifiers, metadata, device descriptions, tokens, and file paths. The service
logs only request ID, method, normalized route, status, and duration. Database
volume encryption, encrypted backups, retention, TLS, host access, and deletion
remain operator responsibilities. See [`../backend/README.md`](../backend/README.md).

## Local storage

- The API token and pseudonymous installation ID are stored in the iOS
  Keychain with `AfterFirstUnlockThisDeviceOnly` accessibility.
- The API token is never stored in `UserDefaults`, source, payloads, status,
  logs, or diagnostics.
- Endpoint/metric settings and the consent receipt are stored in
  `UserDefaults`; they contain no token or health values.
- Pending JSON payloads, per-type HealthKit anchors, operational status, and
  backfill checkpoints are stored in Application Support with
  `completeUntilFirstUserAuthentication` file protection.
- The entire app data directory is explicitly excluded from backup, and the app
  has no iCloud entitlement.
- A queued payload remains local until the server returns a 2xx body confirming
  the matching `batchId`. The payload is then deleted.

**Clear Local Queue** permanently removes pending payloads. **Reset Anchors**
removes incremental cursors but leaves the queue intact and can cause samples
to be queued again. Deleting the app removes its local container; Keychain
retention after app deletion follows iOS behavior and can be cleared by
reinstalling and rotating the server token.

## Logging and diagnostics

The app logs only failures to persist or schedule operational background state.
It does not log health values, payload bodies, sample UUIDs, device IDs, or API
tokens.

Exported diagnostics contain type names, timestamps, queue counts, the
destination host, backend reachability state, background registration state,
and a redacted error. They explicitly omit the API token, endpoint
path/credentials, health values, payload bodies, HealthKit sample IDs, and the
pseudonymous device ID. Review a diagnostic file before sharing it because the
destination host and selected metric names may still be sensitive.

## Third parties

There are no analytics, crash reporting, advertising, telemetry, account,
social, or cloud SDKs. The app communicates only with the private endpoint the
user enters. It does not integrate with Strava; a backend may ingest Strava
independently.

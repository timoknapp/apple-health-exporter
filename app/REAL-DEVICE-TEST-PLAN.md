# Real-device test plan

HealthKit background delivery is not supported by Simulator and iOS does not
guarantee immediate execution. Do not state that background delivery works
until this plan has been completed on an actual iPhone with signed result
notes.

## Record for each run

- App commit and version
- iPhone model and iOS version
- Apple Watch model/watchOS version, if used
- Backend version and test account
- Start/end time and device lock/Low Power Mode state
- Expected and observed batch IDs, HTTP statuses, queue counts, and timestamps
- Pass/fail plus a redacted diagnostic export

Never attach raw payloads or API tokens to a bug report.

## Prerequisites

1. Use a private TLS endpoint with a certificate trusted by the iPhone.
2. Configure the backend to retain received batch IDs and record only
   operational request metadata for this test.
3. Build with the intended bundle identifier, team, HealthKit entitlement,
   HealthKit background-delivery entitlement, and permitted BG task identifier.
4. Seed distinguishable, non-medical test samples in Health where practical.
5. Confirm that no route, ECG, clinical, reproductive, medication, or symptom
   fields appear server-side.

## Scenarios

### 1. First authorization

1. Install cleanly, configure endpoint/token, test the empty connection batch,
   and review consent.
2. Tap **Request Read Authorization** and allow all requested read types.
3. Tap **Sync Now**.

Expected: the app requests no sharing permission, does not show a write usage
prompt, queues per-type anchored results, and uploads accepted batches. The
server sees schema version 1 and the status screen updates without exposing
health values.

### 2. Partial authorization

1. Reinstall or change Health permissions so several read types are withheld.
2. Request authorization and sync.

Expected: supported/authorized types continue. The app never labels a withheld
type as denied or granted and explains that no data and denied read access are
indistinguishable. One empty/failing type does not block another.

### 3. Locked, then unlocked

1. Create new Health/Watch data, lock the iPhone, and wait for an observer
   opportunity.
2. Confirm the backend does not receive an authoritative empty replacement.
3. Unlock and later open/activate the app.

Expected: a protected HealthKit failure leaves its anchor unchanged. After
unlock, a later observer/manual/activation opportunity queues and uploads the
missed changes.

### 4. HealthKit background delivery

1. Leave the app suspended (do not force-quit), generate each practical metric,
   and wait under normal power/network conditions.
2. Repeat for a workout and workout-window heart rate/cycling measurements.

Expected: observer time advances; the callback queues data before network work;
the backend eventually receives it. Record actual latency without claiming an
exact schedule.

### 5. Force-quit and relaunch

1. Queue data, force-quit before or during a network outage, then generate more
   Health data.
2. Relaunch and tap **Sync Now**.

Expected: the durable queue survives. iOS may suppress background launch after
force-quit; relaunch catches up from anchors without logical loss.

### 6. Phone restart

1. Leave at least one retryable batch queued, restart the phone, unlock once,
   and launch the app.

Expected: anchors, outbox payloads, retry metadata, consent, and the backfill
checkpoint survive. The pending batch keeps its original batch ID.

### 7. Network loss and recovery

1. While away from the backend's local network, generate data and run
   **Sync Now**.
2. Confirm Status shows the backend as unavailable and the batch retry count is
   unchanged.
3. Rejoin the backend's network and retry or wait for another sync opportunity.

Expected: one unauthenticated `/readyz` probe occurs, no health payload is sent,
and no batch is deleted or marked as attempted while the backend is
unavailable. A later opportunity sends the same payload and idempotency key,
then removes it only after matching 2xx acceptance.

### 8. Server downtime and retry classes

Exercise 408, 429 with `Retry-After`, 500, 503 with `Retry-After`, timeout, and
connection loss. Separately exercise 401 and 403.

Expected: temporary responses use bounded backoff/jitter and honor
`Retry-After`; 401/403 remain visible permanent/configuration failures without
a tight loop. After fixing server/auth, **Retry Queue** can recover them.

### 9. Low Power Mode

1. Enable Low Power Mode, suspend the app, and generate data.
2. Disable Low Power Mode and activate the app.

Expected: background work may be delayed, but the anchor/outbox catch-up path
eventually exports the records.

### 10. Duplicate uploads

1. Have the backend accept a batch but interrupt the client before it observes
   the response, or return a retryable response after storing the key.
2. Allow retry.

Expected: the same `Idempotency-Key` is reused. The backend returns the matching
accepted `batchId` and creates no duplicate logical sample.

### 11. Deleted HealthKit samples

1. Export a known sample, delete it in Health, and trigger sync.

Expected: `deletedRecords` contains the same `sampleType` and sample UUID. A
duplicate deletion is harmless and no unrelated type is blocked.

### 12. Long historical backfill

1. Choose a multi-year interval and start backfill.
2. Observe memory use and queue growth, pause during a query/page boundary,
   terminate once, relaunch, and resume.
3. Run incremental sync during or after the test.

Expected: processing remains bounded to monthly/100-change pages, progress is
saved, each durable page gets a bounded upload opportunity before the next
query, resume does not skip a page, duplicate pages retain stable IDs, and no
incremental anchor is changed by backfill. Relaunching with a large recovered
outbox must remain memory-bounded and must not issue concurrent uploads for the
same batch.

### 13. Token rotation

1. Revoke the old server token and observe a 401.
2. enter/save a new token and choose **Retry Queue**.

Expected: old/new tokens never appear in diagnostics, outbox files, or logs.
The retained batch succeeds unchanged with the new Authorization header.

### 14. Changing the endpoint

1. Change from server A to server B while a batch is pending.
2. Confirm that upload is blocked until consent is reviewed for B.
3. Consent, test B, and explicitly retry.

Expected: the UI identifies B's host, no upload occurs under stale consent, and
the token remains Keychain-only. Decide operationally whether existing queued
data should go to B; use **Clear Local Queue** first if it should not.

## Completion criteria

All scenarios must pass without lost logical records, premature anchor
advancement, leaked tokens/health values, or out-of-scope data. Any observed
iOS scheduling delay is recorded as a platform limitation rather than treated
as an exact-timing failure. Archive signed notes separately from the app
repository.

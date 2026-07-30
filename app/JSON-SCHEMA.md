# JSON schema version 1

All uploads use `Content-Type: application/json`,
`Idempotency-Key: <batchId>`, and a schema version that changes only when a
backward-incompatible contract is introduced. Timestamps are ISO-8601 UTC with
fractional seconds. The batch also records the phone's current IANA timezone so
the backend can reconstruct local-day features without treating local time as
an absolute timestamp.

## Backend data status

The companion backend exposes `GET /v1/healthkit/status` with the same bearer
token used for ingestion. Its schema-v1 response contains:

| Field | Type | Definition |
|---|---|---|
| `schemaVersion` | integer | Always `1` for this response contract. |
| `generatedAt` | UTC timestamp | Time the backend generated the snapshot. |
| `state` | string | Overall `current`, `stale`, or `empty` state. |
| `latestAcceptedBatchAt` | UTC timestamp or null | Latest successful ingestion receipt. |
| `latestRecordAt` | UTC timestamp or null | Latest active record end time. |
| `activeRecordCount` | integer | Number of non-deleted records. |
| `domains` | object | `recovery`, `sleep`, `activity`, and `workouts` status objects. |
| `databaseSchemaVersion` | integer | Applied backend database schema version. |
| `warnings` | string array | Privacy-safe absent/stale domain warnings. |

Each domain contains `state`, `recordCount`, and nullable `latestRecordAt`.
Status responses contain no identifiers, sources, metadata, raw payloads, or
health measurement values.

## Batch

| Field | Type | Required | Definition |
|---|---|---:|---|
| `schemaVersion` | integer | yes | Always `1` for this contract. |
| `batchId` | UUID string | yes | Stable idempotency key for this logical query page. |
| `deviceId` | UUID string | yes | Locally generated pseudonymous installation ID. It is not an Apple hardware identifier. |
| `createdAt` | UTC timestamp | yes | Time the exact persisted payload was first encoded. |
| `timezone` | string | yes | Current IANA timezone, for example `Europe/Berlin`. |
| `mode` | string | yes | `incremental` or `backfill`. |
| `records` | array | yes | Upsert records; may be empty. |
| `deletedRecords` | array | yes | Deleted sample identities; may be empty. |

## Upsert record

| Field | Type | Required | Definition |
|---|---|---:|---|
| `operation` | string | yes | Always `upsert`. |
| `sampleType` | string | yes | Apple's stable HealthKit sample-type identifier. |
| `sampleId` | UUID string | yes | `HKSample.uuid`. Backends should upsert on `(deviceId, sampleType, sampleId)`. |
| `start` | UTC timestamp | yes | Sample interval start. |
| `end` | UTC timestamp | yes | Sample interval end. |
| `value` | object | yes | Tagged numeric, category, or workout representation below. |
| `unit` | string | yes | Explicit canonical unit from the unit table. |
| `source` | object | yes | HealthKit source revision. |
| `device` | object | no | HealthKit device fields when supplied by the sample. |
| `metadata` | string map | yes | Only allowlisted keys; never arbitrary HealthKit metadata. |

`value.kind` selects one representation:

- `{"kind":"numeric","numericValue":42.5}`
- `{"kind":"category","categoryValue":1}`
- `{"kind":"workout","workout":{...}}`

Workout value fields:

| Field | Type | Required | Definition |
|---|---|---:|---|
| `activityType` | unsigned integer | yes | Raw `HKWorkoutActivityType` value. Unknown future values remain representable. |
| `durationSeconds` | number | yes | HealthKit workout duration. |
| `totalEnergyKilocalories` | number | no | Workout total in `kcal`, when HealthKit supplies it. |
| `totalDistanceMeters` | number | no | Workout total in `m`, when HealthKit supplies it. |

Source fields are `name`, `bundleIdentifier`, optional `version`, and optional
`productType`. This preserves enough provenance to distinguish Apple Watch,
iPhone, Strava-imported data, and other apps. Device fields are optional
`name`, `manufacturer`, `model`, `hardwareVersion`, `firmwareVersion`,
`softwareVersion`, `localIdentifier`, and `udiDeviceIdentifier`.

Heart rate, cycling power, cycling cadence, and cycling speed are separate
timestamped records. Version 1 exports those measurements only when their
sample interval falls inside a HealthKit workout interval. The backend can
associate them with a workout by time window and source. Measurement types have
independent incremental anchors, and every workout upsert also triggers bounded
queries over that workout window before the workout anchor commits. This
recovers measurements when a workout is imported after those measurement
anchors have already advanced. GPS routes are not exported.

## Deletion

```json
{
  "sampleType": "HKQuantityTypeIdentifierRestingHeartRate",
  "sampleId": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
}
```

Deleting a key unknown to the backend is a safe no-op. Upserts and deletions
may be delivered more than once because crash recovery prioritizes no data loss;
the batch id and sample key make both operations idempotent.

## Canonical units

| Metric | Unit string | Numeric convention |
|---|---|---|
| Heart-rate variability SDNN | `ms` | milliseconds |
| Resting/workout/recovery heart rate | `count/min` | beats per minute |
| Sleep analysis | `category-code` | raw `HKCategoryValueSleepAnalysis` integer |
| Respiratory rate | `count/min` | breaths per minute |
| Sleeping wrist temperature | `degC` | degrees Celsius |
| VO2 max | `mL/(kg*min)` | milliliters per kilogram per minute |
| Oxygen saturation | `%` | percentage points (`0.42` in HealthKit becomes `42`) |
| Body fat percentage | `%` | percentage points (`0.20` becomes `20`) |
| Body/lean mass | `kg` | kilograms |
| Active energy | `kcal` | kilocalories |
| Exercise/stand time | `min` | minutes |
| Step count | `count` | count |
| Physical effort | `MET` | `kcal/(kg*hr)`, numerically equal to MET |
| Workout | `workout` | tagged workout object |
| Cycling power | `W` | watts |
| Cycling cadence | `count/min` | pedal revolutions per minute |
| Cycling speed | `m/s` | meters per second |

Raw samples are preserved. The client does not aggregate daily values or
derive coaching features.

## Metadata allowlist

The only metadata keys serialized in version 1 are:

- `HKMetadataKeyExternalUUID`
- `HKMetadataKeySyncIdentifier`
- `HKMetadataKeySyncVersion`
- `HKMetadataKeyWasUserEntered`
- `HKMetadataKeyIndoorWorkout`
- `HKMetadataKeyElevationAscended`
- `HKMetadataKeyAverageSpeed`
- `HKMetadataKeyMaximumSpeed`
- `HKMetadataKeyWeatherTemperature`
- `HKMetadataKeyWeatherHumidity`
- `HKMetadataKeyTimeZone`
- `HKMetadataKeyPhysicalEffortEstimationType`

The JSON keys use the actual HealthKit string constants. String values are
limited to 512 characters. Dates use UTC ISO-8601. Supported quantity metadata
includes its canonical unit in the string, for example `12.3 m`. Unsupported
value classes are omitted rather than serialized by description.

## Example

```json
{
  "schemaVersion": 1,
  "batchId": "11111111-1111-1111-1111-111111111111",
  "deviceId": "22222222-2222-2222-2222-222222222222",
  "createdAt": "2026-07-16T14:45:00.000Z",
  "timezone": "Europe/Berlin",
  "mode": "incremental",
  "records": [
    {
      "operation": "upsert",
      "sampleType": "HKQuantityTypeIdentifierRestingHeartRate",
      "sampleId": "33333333-3333-3333-3333-333333333333",
      "start": "2026-07-16T04:30:00.000Z",
      "end": "2026-07-16T04:30:00.000Z",
      "value": {"kind": "numeric", "numericValue": 49},
      "unit": "count/min",
      "source": {
        "name": "Apple Watch",
        "bundleIdentifier": "com.apple.health",
        "version": "26.0",
        "productType": "Watch"
      },
      "metadata": {}
    }
  ],
  "deletedRecords": []
}
```

## Compatibility rules

1. Consumers must ignore unknown object fields.
2. New optional fields and new HealthKit `sampleType` values may be added
   without changing `schemaVersion`.
3. Existing field meaning, unit convention, requiredness, or tagged-value
   shape must not change within version 1.
4. A breaking change requires a new integer schema version and a transition
   period in which the server explicitly accepts both versions.
5. The server must reject unsupported versions without acknowledging the batch
   ID; the client then retains the batch as a permanent/configuration failure.

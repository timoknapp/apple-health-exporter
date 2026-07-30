# Apple Health Exporter and Health Coach

This repository has two deliberately separate components:

```text
.
|-- app/       iPhone HealthKit exporter and its test-only mock server
`-- backend/   production REST ingestion and read-only MCP service
```

## App

[`app/`](app/README.md) contains the SwiftUI iPhone app, Xcode project, Swift
package tests, HealthKit v1 JSON contract, privacy documentation, real-device
test plan, and deterministic local mock ingestion server.

```sh
cd app
swift test
```

## Backend

[`backend/`](backend/README.md) contains the production Python service,
SQLite migrations and analytics, MCP tools, backend tests, Dockerfile, Compose
configuration, and deployment documentation.

```sh
cd backend
docker compose config
```

The app uploads only through the authenticated REST endpoint. MCP is a separate,
authenticated, read-only query interface. The wire contract in
[`app/JSON-SCHEMA.md`](app/JSON-SCHEMA.md) remains authoritative.

## Security

Please report vulnerabilities privately as described in
[`SECURITY.md`](SECURITY.md). Do not open a public issue, and never include
real health data or API tokens in a report.

## License

Released under the [MIT License](LICENSE).

This is a personal fitness data tool, not a medical device. It does not
diagnose, treat, monitor emergencies, or provide medical advice.

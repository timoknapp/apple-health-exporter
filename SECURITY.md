# Security Policy

This project moves personal Apple Health data between an iPhone and a
self-hosted backend. Security reports are welcome and taken seriously.

It is fitness-coaching infrastructure, not a medical device. It does not
diagnose, treat, monitor emergencies, or provide medical advice.

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report privately through
[GitHub Security Advisories](https://github.com/timoknapp/apple-health-exporter/security/advisories/new),
which keeps the report confidential until a fix is available.

Please include:

- affected component (`app/` or `backend/`) and version or commit;
- a description of the issue and its impact;
- reproduction steps or a proof of concept; and
- any suggested remediation.

**Never include real health data, API tokens, or unredacted diagnostics in a
report.** Use synthetic samples. `app/PRIVACY.md` describes what an exported
diagnostic contains and why it should be reviewed before sharing.

Expect an initial response within 7 days. Because this is a personal project
maintained in spare time, fix timelines depend on severity and complexity. You
will be credited in the advisory unless you ask otherwise.

## Supported versions

Only the latest commit on the default branch receives security fixes. There are
no long-term support branches or backports.

## Security model

Understanding the intended trust boundaries helps determine whether a finding
is a vulnerability or expected behavior. `backend/README.md` documents the
architecture in full; the essentials are:

- **Single user, single operator.** No accounts, roles, or multi-tenancy.
- **TLS terminates in an operator-run reverse proxy or private ingress.** The
  application itself listens on loopback and does not implement TLS.
- **Two distinct static bearer tokens.** One authorizes REST ingestion writes;
  the other authorizes the bounded read-only MCP projection. The service
  refuses to start if they are equal or shorter than the minimum length.
- **MCP is read-only.** It opens SQLite connections with `mode=ro` and
  `PRAGMA query_only=ON`, and exposes only allowlisted tools and resources.
- **Secrets are never committed.** Tokens are supplied through Docker secrets or
  token files, and on iOS are stored only in the Keychain — never in
  `UserDefaults`, source, payloads, status, or logs.
- **Neither interface logs** request bodies, authorization headers, health
  values, identifiers, raw metadata, query strings, or MCP results.

## In scope

- Authentication or authorization bypass on REST ingestion or MCP.
- Any MCP path that permits writes, arbitrary SQL, or file or network access.
- Leakage of health data, tokens, or identifiers into logs, errors, or
  diagnostics.
- SQL injection, path traversal, deserialization, or resource-exhaustion flaws.
- Token handling weaknesses, including insecure storage or comparison.
- Container escape or privilege escalation from the shipped Compose setup.
- Transport weaknesses in the iOS client, such as endpoint validation bypass or
  App Transport Security downgrade in release builds.
- Vulnerable pinned dependencies with a practical exploit path.

## Out of scope

The following are documented design decisions, not vulnerabilities:

- **Plaintext HTTP to private hosts in DEBUG builds.** Release builds require
  HTTPS. The debug allowance is limited to loopback, `.local`, and private
  address ranges, and is documented as trusted-LAN-only.
- **Static bearer tokens instead of OAuth.** Intentional for a single-user
  private deployment. Rotation is documented in `backend/README.md`.
- **Absence of TLS in the application.** Delegated to the operator's proxy.
- **Operator responsibilities**: host storage encryption, firewalling, backup
  encryption, and retention.
- Findings that require a compromised host, a malicious operator, or physical
  access to an unlocked device.
- Automated scanner output without a demonstrated exploit path.
- Missing hardening headers on an API with no browser UI or CORS.

If you believe an out-of-scope item is exploitable in a way the documentation
does not anticipate, report it anyway and explain the attack path.

## Deployment guidance

This project ships no hosted service — every deployment is operator-run. Before
exposing a backend beyond loopback:

1. Terminate TLS in a trusted reverse proxy; never expose the container port
   directly to an untrusted network.
2. Generate two distinct high-entropy tokens with `secrets.token_urlsafe(32)`
   under `umask 077`, and keep `secrets/` outside the build context.
3. Set `HEALTH_COACH_ALLOWED_HOSTS` to the trusted proxy hostname.
4. Keep the container's `read_only`, `cap_drop: ALL`, and
   `no-new-privileges` settings as shipped.
5. Rotate tokens on any suspected exposure. Rotation is immediate and has no
   grace period.

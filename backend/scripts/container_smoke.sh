#!/bin/sh
set -eu

backend_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
image_name="${HEALTH_COACH_SMOKE_IMAGE:-health-coach-backend:smoke}"
container_name="health-coach-smoke-$$"
volume_name="health-coach-smoke-data-$$"
temporary_dir=$(mktemp -d)
python_bin="${HEALTH_COACH_SMOKE_PYTHON:-$backend_dir/.venv/bin/python}"

cleanup() {
    docker rm -f "$container_name" >/dev/null 2>&1 || true
    docker volume rm "$volume_name" >/dev/null 2>&1 || true
    rm -rf "$temporary_dir"
}
trap cleanup EXIT INT TERM

if ! docker info >/dev/null 2>&1; then
    echo "Docker daemon is unavailable." >&2
    exit 2
fi
if [ ! -x "$python_bin" ]; then
    echo "Python with the locked backend dependencies is required at $python_bin." >&2
    exit 2
fi

ingest_token=$("$python_bin" -c "import secrets; print(secrets.token_urlsafe(32))")
mcp_token=$("$python_bin" -c "import secrets; print(secrets.token_urlsafe(32))")
printf '%s\n' "$ingest_token" >"$temporary_dir/ingest_token"
printf '%s\n' "$mcp_token" >"$temporary_dir/mcp_token"
chmod 0444 "$temporary_dir/ingest_token" "$temporary_dir/mcp_token"

docker build -f "$backend_dir/Dockerfile" -t "$image_name" "$backend_dir"
docker volume create "$volume_name" >/dev/null
docker run -d \
    --name "$container_name" \
    --init \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --tmpfs /tmp:rw,noexec,nosuid,size=16m \
    -p 127.0.0.1::8080 \
    -e HEALTH_COACH_DB_PATH=/data/coach.sqlite3 \
    -e HEALTH_COACH_TIMEZONE=Europe/Berlin \
    -e HEALTH_COACH_ALLOWED_HOSTS=127.0.0.1,localhost \
    -e HEALTH_COACH_INGEST_TOKEN_FILE=/run/secrets/ingest_token \
    -e HEALTH_COACH_MCP_TOKEN_FILE=/run/secrets/mcp_token \
    --mount "type=volume,source=$volume_name,target=/data" \
    --mount "type=bind,source=$temporary_dir/ingest_token,target=/run/secrets/ingest_token,readonly" \
    --mount "type=bind,source=$temporary_dir/mcp_token,target=/run/secrets/mcp_token,readonly" \
    "$image_name" >/dev/null

published=$(docker port "$container_name" 8080/tcp)
base_url="http://$published"
attempt=0
until curl --fail --silent --show-error "$base_url/readyz" >/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        docker logs "$container_name" >&2
        exit 1
    fi
    sleep 1
done

cat >"$temporary_dir/batch.json" <<'JSON'
{"schemaVersion":1,"batchId":"11111111-1111-1111-1111-111111111111","deviceId":"22222222-2222-2222-2222-222222222222","createdAt":"2026-07-16T14:45:00.000Z","timezone":"Europe/Berlin","mode":"incremental","records":[],"deletedRecords":[]}
JSON

status=$(curl --silent --output "$temporary_dir/response.json" --write-out '%{http_code}' \
    -H "Authorization: Bearer $ingest_token" \
    -H "Idempotency-Key: 11111111-1111-1111-1111-111111111111" \
    -H "Content-Type: application/json" \
    --data-binary "@$temporary_dir/batch.json" \
    "$base_url/v1/healthkit/ingest")
[ "$status" = "201" ]
[ "$(cat "$temporary_dir/response.json")" = '{"batchId":"11111111-1111-1111-1111-111111111111"}' ]

HEALTH_COACH_SMOKE_MCP_URL="$base_url/mcp" \
HEALTH_COACH_SMOKE_MCP_TOKEN="$mcp_token" \
"$python_bin" "$backend_dir/scripts/mcp_smoke_client.py"

docker restart "$container_name" >/dev/null
published=$(docker port "$container_name" 8080/tcp)
base_url="http://$published"
attempt=0
until curl --fail --silent --show-error "$base_url/readyz" >/dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then
        docker logs "$container_name" >&2
        exit 1
    fi
    sleep 1
done

status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    -H "Authorization: Bearer $ingest_token" \
    -H "Idempotency-Key: 11111111-1111-1111-1111-111111111111" \
    -H "Content-Type: application/json" \
    --data-binary "@$temporary_dir/batch.json" \
    "$base_url/v1/healthkit/ingest")
[ "$status" = "200" ]
[ "$(docker exec "$container_name" id -u)" = "10001" ]

docker run --rm --entrypoint sh "$image_name" -c \
    'test -z "$(find /app -type f \( -name ".env" -o -name "*.sqlite*" -o -name "*-wal" -o -name "*-shm" -o -name "export.xml" -o -name "*.zip" -o -path "*/secrets/*" \) -print -quit)"'

echo "Container smoke test passed."

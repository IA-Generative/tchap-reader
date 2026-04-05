#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE="${TCHAP_IMAGE:-tchapreader:local}"
CONTAINER="${TCHAP_CONTAINER:-tchapreader}"
PORT="${TCHAP_PORT:-8087}"
NETWORK="${DOCKER_NETWORK:-grafrag-experimentation_default}"

if [[ ! -f "${PROJECT_ROOT}/.env" ]]; then
  cp "${PROJECT_ROOT}/.env.example" "${PROJECT_ROOT}/.env"
  echo "Created .env from .env.example — edit it with your Tchap credentials."
  exit 1
fi

echo "=== Building ${IMAGE} ==="
docker build -f "${PROJECT_ROOT}/Dockerfile" -t "${IMAGE}" "${PROJECT_ROOT}"

docker rm -f "${CONTAINER}" 2>/dev/null || true

NETWORK_FLAG=""
if docker network inspect "${NETWORK}" >/dev/null 2>&1; then
  NETWORK_FLAG="--network ${NETWORK}"
fi

echo "=== Starting ${CONTAINER} on port ${PORT} ==="
docker run -d \
  --name "${CONTAINER}" \
  ${NETWORK_FLAG} \
  --publish "${PORT}:8087" \
  --env-file "${PROJECT_ROOT}/.env" \
  -v tchapreader-data:/app/data \
  --restart unless-stopped \
  "${IMAGE}"

printf 'Waiting...'
for i in $(seq 1 20); do
  curl -sf "http://localhost:${PORT}/healthz" >/dev/null 2>&1 && printf ' OK\n' && break
  printf '.'; sleep 1
done

curl -s "http://localhost:${PORT}/healthz" | python3 -m json.tool

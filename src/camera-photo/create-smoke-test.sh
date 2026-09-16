#!/usr/bin/env bash
set -euo pipefail

N8N_CONTAINER="${SECOND_BRAIN_N8N_CONTAINER:-n8n}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="/Capture/Documents/Camera Inbox/${STAMP}_camera-photo-smoke-test.jpg"
SOURCE="/scripts/Camera Photo/tests/camera-photo-smoke.jpg"

if ! docker inspect "${N8N_CONTAINER}" >/dev/null 2>&1; then
  echo "STOP: n8n container was not found: ${N8N_CONTAINER}"
  exit 1
fi

docker exec "${N8N_CONTAINER}" cp "${SOURCE}" "${TARGET}"

docker exec "${N8N_CONTAINER}" touch -d '2 minutes ago' "${TARGET}"

echo "CREATED: ${TARGET}"
echo "NEXT: Manually run [DOC-01B] Camera Inbox → Photo Capture Review | OCR + Vision"

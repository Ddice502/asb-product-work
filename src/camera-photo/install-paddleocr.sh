#!/usr/bin/env bash
set -euo pipefail

COMPOSE_DIRECTORY="${1:-/AI/Apps/n8n}"
OVERLAY="/AI/Apps/camera-photo-ocr/camera-ocr.compose.yml"

if [[ ! -d "${COMPOSE_DIRECTORY}" ]]; then
  echo "STOP: Compose directory does not exist: ${COMPOSE_DIRECTORY}"
  exit 1
fi

if [[ ! -f "${OVERLAY}" ]]; then
  echo "STOP: Camera OCR Compose overlay is missing: ${OVERLAY}"
  exit 1
fi

COMPOSE_FILE=""
for candidate in \
  "${COMPOSE_DIRECTORY}/compose.yaml" \
  "${COMPOSE_DIRECTORY}/compose.yml" \
  "${COMPOSE_DIRECTORY}/docker-compose.yaml" \
  "${COMPOSE_DIRECTORY}/docker-compose.yml"; do
  if [[ -f "${candidate}" ]]; then
    if [[ -n "${COMPOSE_FILE}" ]]; then
      echo "STOP: More than one Compose file was found. Pass a directory containing one canonical file."
      exit 1
    fi
    COMPOSE_FILE="${candidate}"
  fi
done

if [[ -z "${COMPOSE_FILE}" ]]; then
  echo "STOP: No Compose file was found under ${COMPOSE_DIRECTORY}"
  exit 1
fi

docker compose \
  -f "${COMPOSE_FILE}" \
  -f "${OVERLAY}" \
  config >/dev/null

echo "COMPOSE_VALID"

docker compose \
  -f "${COMPOSE_FILE}" \
  -f "${OVERLAY}" \
  up -d --build camera-ocr

echo "CAMERA_OCR_CONTAINER_STARTED"
echo "The first model download and initialization can take several minutes."
echo "Check with: sudo docker inspect camera-ocr --format '{{json .State.Health}}'"

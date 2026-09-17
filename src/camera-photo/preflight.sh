#!/usr/bin/env bash
set -euo pipefail

N8N_CONTAINER="${SECOND_BRAIN_N8N_CONTAINER:-n8n}"
CONFIG_PATH="/Config/Second Brain/Camera Photo/camera-photo-config.json"

if ! docker inspect "${N8N_CONTAINER}" >/dev/null 2>&1; then
  echo "STOP: n8n container was not found: ${N8N_CONTAINER}"
  exit 1
fi

for target in \
  "/scripts/Camera Photo/camera-photo-worker.js" \
  "/scripts/Camera Photo/camera-photo-review.js" \
  "${CONFIG_PATH}" \
  "/Config/Second Brain/Camera Photo/photo-extraction.schema.json" \
  "/Config/Second Brain/Camera Photo/photo-extraction-prompt.txt"; do
  if ! docker exec "${N8N_CONTAINER}" test -r "${target}"; then
    echo "STOP: n8n cannot read ${target}"
    exit 1
  fi
done

for target in \
  "/Capture/Documents/Camera Inbox" \
  "/Capture/Documents/Camera Processing" \
  "/Capture/Documents/Archive" \
  "/Capture/Documents/Failed/Camera Photos" \
  "/Capture/Documents/Duplicates/Camera Photos" \
  "/Capture/Documents/Temp/Camera Photos" \
  "/Capture/Documents/Extracted/Camera Photos" \
  "/Capture/Documents/Logs" \
  "/Obsidian/00 Inbox/Documents/Photo Capture Review/Pending" \
  "/Obsidian/00 Inbox/Documents/Needs Filing Review/Photo Transcriptions" \
  "/Obsidian/99 Archive/Source Attachments/Photo Captures" \
  "/Obsidian/90 System/Queues"; do
  if ! docker exec "${N8N_CONTAINER}" test -w "${target}"; then
    echo "STOP: n8n cannot write ${target}"
    exit 1
  fi
done

if ! docker exec "${N8N_CONTAINER}" sh -lc \
  'command -v node >/dev/null && command -v tesseract >/dev/null'; then
  echo "STOP: n8n is missing Node.js or Tesseract."
  exit 1
fi

NODES_EXCLUDE_VALUE="$(
  docker inspect "${N8N_CONTAINER}" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sed -n 's/^NODES_EXCLUDE=//p' \
    | tail -n 1
)"

if [[ "${NODES_EXCLUDE_VALUE}" != "[]" ]]; then
  echo "STOP: Expected NODES_EXCLUDE=[] so Execute Command is available."
  echo "Found: ${NODES_EXCLUDE_VALUE:-not set}"
  exit 1
fi

PREFLIGHT_OUTPUT="$(
  docker exec "${N8N_CONTAINER}" \
    node '/scripts/Camera Photo/camera-photo-worker.js' \
      preflight --config "${CONFIG_PATH}"
)"

echo "${PREFLIGHT_OUTPUT}"

PREFLIGHT_RESULT="$(
  printf '%s' "${PREFLIGHT_OUTPUT}" \
    | sed -n 's/.*"result":"\([^"]*\)".*/\1/p'
)"

if [[ "${PREFLIGHT_RESULT}" != "preflight_passed" ]]; then
  echo "STOP: Camera-photo preflight did not pass."
  exit 1
fi

echo "CAMERA_PHOTO_PREFLIGHT_PASS"

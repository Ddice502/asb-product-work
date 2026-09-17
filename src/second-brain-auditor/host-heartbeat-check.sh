#!/usr/bin/env bash
set -euo pipefail

export TZ='America/Kentucky/Louisville'

N8N_CONTAINER='n8n'
HEARTBEAT_FILE='/AI/State/Second Brain/Auditor/production-heartbeat.json'
MONITOR_ROOT='/AI/State/Second Brain/Auditor/External Monitors'
MONITOR_FILE="$MONITOR_ROOT/host-monitor.json"
ALERT_STATE_FILE="$MONITOR_ROOT/host-alert-state.txt"
NTFY_URL='https://ntfy.sh/jayaroh-voice'

mkdir -p "$MONITOR_ROOT"

NOW_ISO="$(date --iso-8601=seconds)"
NOW_EPOCH="$(date +%s)"
TODAY_SCHEDULE_EPOCH="$(date -d 'today 04:00' +%s)"
TODAY_DEADLINE_EPOCH="$((TODAY_SCHEDULE_EPOCH + 5400))"
ISSUES=()

if [[ "$(docker inspect --format '{{.State.Running}}' "$N8N_CONTAINER" 2>/dev/null || true)" != 'true' ]]; then
  ISSUES+=('The n8n container is not running.')
fi

if [[ ! -r "$HEARTBEAT_FILE" ]]; then
  ISSUES+=('The production auditor heartbeat is missing or unreadable.')
else
  CURRENT_PHASE="$(jq -r '.current_phase // "missing"' "$HEARTBEAT_FILE" 2>/dev/null || echo invalid)"
  LAST_SUCCESS="$(jq -r '.last_success // empty' "$HEARTBEAT_FILE" 2>/dev/null || true)"
  if [[ "$CURRENT_PHASE" != 'succeeded' ]]; then
    ISSUES+=("The production auditor heartbeat phase is $CURRENT_PHASE.")
  fi
  if [[ "$NOW_EPOCH" -ge "$TODAY_DEADLINE_EPOCH" ]]; then
    LAST_SUCCESS_EPOCH="$(date -d "$LAST_SUCCESS" +%s 2>/dev/null || echo 0)"
    if [[ "$LAST_SUCCESS_EPOCH" -lt "$TODAY_SCHEDULE_EPOCH" ]]; then
      ISSUES+=('The daily 4:00 a.m. audit did not succeed by 5:30 a.m.')
    fi
  fi
fi

if [[ "${#ISSUES[@]}" -eq 0 ]]; then
  STATUS='verified'
  MESSAGE='Host monitor verified n8n and the production auditor heartbeat.'
else
  STATUS='failed'
  MESSAGE="$(printf '%s ' "${ISSUES[@]}")"
fi

ISSUE_HASH="$(printf '%s' "$STATUS|$MESSAGE" | sha256sum | awk '{print $1}')"
PREVIOUS_HASH="$(cat "$ALERT_STATE_FILE" 2>/dev/null || true)"

if [[ "$ISSUE_HASH" != "$PREVIOUS_HASH" ]]; then
  if [[ "$STATUS" == 'failed' ]]; then
    curl --fail --silent --show-error \
      -H 'Title: Second-Brain host monitor alert' \
      -H 'Priority: urgent' \
      -H 'Tags: rotating_light' \
      --data-binary "$MESSAGE" \
      "$NTFY_URL" >/dev/null
  elif [[ -n "$PREVIOUS_HASH" ]]; then
    curl --fail --silent --show-error \
      -H 'Title: Second-Brain host monitor recovered' \
      -H 'Priority: default' \
      -H 'Tags: white_check_mark' \
      --data-binary "$MESSAGE" \
      "$NTFY_URL" >/dev/null
  fi
  printf '%s\n' "$ISSUE_HASH" > "$ALERT_STATE_FILE"
fi

TMP_FILE="$MONITOR_FILE.$$.tmp"
jq -n \
  --arg status "$STATUS" \
  --arg checked_at "$NOW_ISO" \
  --arg message "$MESSAGE" \
  '{schema_version:"1.0.0",monitor:"server_host",status:$status,checked_at:$checked_at,message:$message}' \
  > "$TMP_FILE"
chmod 0640 "$TMP_FILE"
mv -f "$TMP_FILE" "$MONITOR_FILE"

[[ "$STATUS" == 'verified' ]]

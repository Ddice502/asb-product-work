#!/usr/bin/env bash

set -o pipefail

STATE='/AI/State/Second Brain/Step 53'
OBS="$STATE/Observation"
DAILY="$OBS/Daily"

DAY="$(date -u +%Y-%m-%d)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TX="$DAILY/$DAY"

mkdir -p "$TX" || exit 1

# ------------------------------------------------------------
# Helper: run probe without killing the collector
# ------------------------------------------------------------

probe() {
    local name="$1"
    shift

    "$@" \
        > "$TX/${name}.stdout" \
        2> "$TX/${name}.stderr"

    local rc=$?

    printf '%s\n' "$rc" \
        > "$TX/${name}.rc"

    return 0
}

# ============================================================
# HOST
# ============================================================

probe \
    boot-id \
    cat \
    /proc/sys/kernel/random/boot_id

probe \
    uptime \
    uptime

# ============================================================
# CONTAINERS
# ============================================================

probe \
    docker-ps \
    docker ps \
    --format '{{.Names}}\t{{.Status}}'

CONTAINER_IDS="$(
    docker ps -q 2>/dev/null
)"

if [ -n "$CONTAINER_IDS" ]; then
    docker inspect \
        $CONTAINER_IDS \
        --format \
        '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}|{{.RestartCount}}' \
        > "$TX/container-health.stdout" \
        2> "$TX/container-health.stderr"

    printf '%s\n' "$?" \
        > "$TX/container-health.rc"
else
    : > "$TX/container-health.stdout"

    echo 'No running container IDs returned.' \
        > "$TX/container-health.stderr"

    echo '1' \
        > "$TX/container-health.rc"
fi

# ============================================================
# N8N DISCOVERY
# ============================================================

N8N="$(
    docker ps \
        --format '{{.Names}}' \
        2>/dev/null |
    grep -E '^n8n$|n8n' |
    head -n1
)"

if [ -n "$N8N" ]; then
    echo "$N8N" \
        > "$TX/n8n-container.txt"

    TMP="/tmp/step53-workflows-$STAMP.json"

    docker exec -u node "$N8N" \
        n8n export:workflow \
        --all \
        --output="$TMP" \
        > "$TX/workflow-export.stdout" \
        2> "$TX/workflow-export.stderr"

    WF_RC=$?

    echo "$WF_RC" \
        > "$TX/workflow-export.rc"

    if [ "$WF_RC" -eq 0 ]; then
        docker cp \
            "$N8N:$TMP" \
            "$TX/workflows.json" \
            > "$TX/workflow-copy.stdout" \
            2> "$TX/workflow-copy.stderr"

        echo "$?" \
            > "$TX/workflow-copy.rc"

        docker exec -u 0 "$N8N" \
            rm -f "$TMP" \
            >/dev/null 2>&1 || true
    else
        echo '1' \
            > "$TX/workflow-copy.rc"
    fi

    docker exec "$N8N" \
        node \
        '/scripts/Second Brain Backups/second-brain-backup.mjs' \
        preflight \
        > "$TX/backup-preflight.json" \
        2> "$TX/backup-preflight.stderr"

    echo "$?" \
        > "$TX/backup-preflight.rc"
else
    echo 'n8n container not discovered.' \
        > "$TX/n8n-container-error.txt"

    echo '1' > "$TX/workflow-export.rc"
    echo '1' > "$TX/workflow-copy.rc"
    echo '1' > "$TX/backup-preflight.rc"
fi

# ============================================================
# OLLAMA
# ============================================================

ss -lntp \
    > "$TX/listeners.txt" \
    2> "$TX/listeners.stderr"

echo "$?" \
    > "$TX/listeners.rc"

curl -fsS \
    --max-time 10 \
    http://127.0.0.1:11435/api/tags \
    > "$TX/ollama-tags.json" \
    2> "$TX/ollama.stderr"

echo "$?" \
    > "$TX/ollama.rc"

# ============================================================
# SECURITY DRIFT
# ============================================================

find /AI \
    -xdev \
    \( -type f -o -type d \) \
    -perm -0002 \
    -printf '%m %u:%g %p\n' \
    2> "$TX/world-writable.stderr" \
    | sort \
    > "$TX/world-writable.txt"

echo "${PIPESTATUS[0]}" \
    > "$TX/world-writable.rc"

if [ -n "$CONTAINER_IDS" ]; then
    docker inspect \
        $CONTAINER_IDS \
        --format \
        '{{.Name}} {{.HostConfig.Privileged}}' \
        2> "$TX/privileged-containers.stderr" \
        | awk '$2=="true"{print}' \
        > "$TX/privileged-containers.txt"

    echo "${PIPESTATUS[0]}" \
        > "$TX/privileged-containers.rc"
else
    : > "$TX/privileged-containers.txt"

    echo '1' \
        > "$TX/privileged-containers.rc"
fi

# ============================================================
# RECENT REVIEW / FAILURE FILE INDEX
# ============================================================

find /AI/State/Second Brain \
    -type f \
    -mtime -1 \
    \( \
        -iname '*fail*' \
        -o -iname '*error*' \
        -o -iname '*health*' \
        -o -iname '*review*' \
    \) \
    -printf '%T@\t%p\n' \
    2> "$TX/recent-evidence.stderr" \
    | sort -nr \
    > "$TX/recent-evidence.tsv"

echo "${PIPESTATUS[0]}" \
    > "$TX/recent-evidence.rc"

# ============================================================
# BUILD OBSERVATION
# ============================================================

python3 - "$TX" "$DAY" "$STAMP" <<'PY'
import json
import re
import sys
from pathlib import Path

tx = Path(sys.argv[1])
day = sys.argv[2]
stamp = sys.argv[3]


def rc(name):
    path = tx / f"{name}.rc"

    try:
        return int(
            path.read_text().strip()
        )
    except Exception:
        return None


def text(name):
    path = tx / name

    try:
        return path.read_text(
            errors="ignore"
        )
    except Exception:
        return ""


checks = {}
collection = {}

# ------------------------------------------------------------
# Container state
# ------------------------------------------------------------

container_rc = rc(
    "container-health"
)

container_rows = []

for line in text(
    "container-health.stdout"
).splitlines():

    parts = line.split("|")

    if len(parts) < 4:
        continue

    container_rows.append({
        "name":
            parts[0].lstrip("/"),

        "state":
            parts[1],

        "health":
            parts[2],

        "restart_count":
            parts[3],
    })

collection[
    "container_probe_rc"
] = container_rc

checks[
    "containers_present"
] = (
    container_rc == 0
    and len(container_rows) > 0
)

# A restart count >0 is not by itself a loop.
# What matters here is current running/healthy state.
checks[
    "containers_currently_healthy"
] = (
    len(container_rows) > 0
    and all(
        row["state"] == "running"
        and row["health"] in {
            "healthy",
            "no-healthcheck",
        }
        for row in container_rows
    )
)

# ------------------------------------------------------------
# Workflow state
# ------------------------------------------------------------

workflow_export_rc = rc(
    "workflow-export"
)

workflow_copy_rc = rc(
    "workflow-copy"
)

collection[
    "workflow_export_rc"
] = workflow_export_rc

collection[
    "workflow_copy_rc"
] = workflow_copy_rc

active_count = None
cloud_hits = []

workflow_file = tx / "workflows.json"

if (
    workflow_export_rc == 0
    and workflow_copy_rc == 0
    and workflow_file.exists()
):
    try:
        raw = json.loads(
            workflow_file.read_text()
        )

        if not isinstance(raw, list):
            raw = [raw]

        active = [
            workflow
            for workflow in raw
            if workflow.get("active") is True
        ]

        active_count = len(active)

        cloud = re.compile(
            r"(?i)"
            r"(api\.openai\.com|"
            r"anthropic\.com|"
            r"generativelanguage\.googleapis\.com|"
            r"api\.mistral\.ai|"
            r"api\.groq\.com|"
            r"openrouter\.ai|"
            r"cohere\.ai|"
            r"replicate\.com|"
            r"huggingface\.co)"
        )

        for workflow in active:
            payload = json.dumps(
                workflow,
                ensure_ascii=False
            )

            domains = sorted({
                match.group(0)
                for match
                in cloud.finditer(payload)
            })

            if domains:
                cloud_hits.append({
                    "id":
                        workflow.get("id"),

                    "name":
                        workflow.get("name"),

                    "domains":
                        domains,
                })

    except Exception as exc:
        collection[
            "workflow_parse_error"
        ] = str(exc)

checks[
    "active_workflow_count_38"
] = (
    active_count == 38
)

checks[
    "no_cloud_ai_workflows"
] = (
    active_count is not None
    and len(cloud_hits) == 0
)

# ------------------------------------------------------------
# Backup
# ------------------------------------------------------------

backup_rc = rc(
    "backup-preflight"
)

collection[
    "backup_preflight_rc"
] = backup_rc

backup = None

try:
    backup = json.loads(
        (
            tx
            / "backup-preflight.json"
        ).read_text()
    )
except Exception:
    pass

checks[
    "backup_preflight_pass"
] = (
    backup_rc == 0
    and isinstance(backup, dict)
    and backup.get("status")
        == "completed"
)

# ------------------------------------------------------------
# Ollama
# ------------------------------------------------------------

listener_rc = rc(
    "listeners"
)

ollama_rc = rc(
    "ollama"
)

collection[
    "listener_probe_rc"
] = listener_rc

collection[
    "ollama_probe_rc"
] = ollama_rc

listeners = text(
    "listeners.txt"
)

checks[
    "ollama_loopback_only"
] = (
    listener_rc == 0
    and "127.0.0.1:11435"
        in listeners
    and "0.0.0.0:11435"
        not in listeners
    and "[::]:11435"
        not in listeners
)

ollama = None

try:
    ollama = json.loads(
        (
            tx
            / "ollama-tags.json"
        ).read_text()
    )
except Exception:
    pass

models = []

if isinstance(ollama, dict):
    for item in ollama.get(
        "models",
        []
    ):
        if isinstance(item, dict):
            name = item.get("name")

            if name:
                models.append(name)

checks[
    "qwen3_4b_available"
] = (
    ollama_rc == 0
    and any(
        name == "qwen3:4b"
        or name.startswith(
            "qwen3:4b"
        )
        for name in models
    )
)

# ------------------------------------------------------------
# Security
# ------------------------------------------------------------

world_rc = rc(
    "world-writable"
)

privileged_rc = rc(
    "privileged-containers"
)

collection[
    "world_writable_probe_rc"
] = world_rc

collection[
    "privileged_probe_rc"
] = privileged_rc

checks[
    "world_writable_ai_paths_zero"
] = (
    world_rc == 0
    and text(
        "world-writable.txt"
    ).strip() == ""
)

checks[
    "privileged_containers_zero"
] = (
    privileged_rc == 0
    and text(
        "privileged-containers.txt"
    ).strip() == ""
)

technical_pass = all(
    checks.values()
)

result = {
    "status":
        (
            "STEP_53_DAILY_TECHNICAL_PASS"
            if technical_pass
            else
            "STEP_53_DAILY_REVIEW_REQUIRED"
        ),

    "day":
        day,

    "observed_at_utc":
        stamp,

    "checks":
        checks,

    "collection_status":
        collection,

    "active_workflow_count":
        active_count,

    "cloud_ai_hits":
        cloud_hits,

    "container_count":
        len(container_rows),

    "technical_checks_pass":
        technical_pass,

    "important_boundary": {
        "daily_record_does_not_complete_step53":
            True,

        "elapsed_ten_day_requirement_still_applies":
            True,
    },
}

path = (
    tx
    / "daily-observation.json"
)

path.write_text(
    json.dumps(
        result,
        indent=2
    ) + "\n"
)

print(
    json.dumps(
        result,
        indent=2
    )
)
PY

PY_RC=$?

if [ "$PY_RC" -ne 0 ]; then
    exit "$PY_RC"
fi

sync

# Successful collection is successful service execution,
# even if the resulting observation says REVIEW_REQUIRED.
exit 0

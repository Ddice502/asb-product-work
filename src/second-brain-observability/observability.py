#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/AI/Scripts/Second Brain Diagnostics")
from diagnostics import diagnose_event

SCHEMA_VERSION = "1.1.0"
WORKER_VERSION = "1.1.0"
RETENTION_DAYS = 183

AI_ROOT = Path("/AI")
SOURCE_LOG_ROOT = AI_ROOT / "Logs"
OBS_LOG_ROOT = SOURCE_LOG_ROOT / "Second Brain Observability"
EVENT_DB = OBS_LOG_ROOT / "events.sqlite"
STATE_ROOT = AI_ROOT / "State/Second Brain/Observability"
CURRENT_STATE = STATE_ROOT / "current.json"
CURSOR_STATE = STATE_ROOT / "cursors.json"
PROCESSING_STATUS = AI_ROOT / "State/Second Brain/Processing Status/current.json"
N8N_DB = AI_ROOT / "Config/n8n/database.sqlite"
VOICE_RUN_ROOT = AI_ROOT / "State/Transcription Consensus/Production Runs"
SYSTEM_ROOT = AI_ROOT / "Knowledge/Second Brain/90 System"
NOTE_PATH = SYSTEM_ROOT / "Observability.md"

SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|encryption[_-]?key|private[_-]?key|credential|authorization)",
    re.I,
)

NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")
CUTOFF = NOW - timedelta(days=RETENTION_DAYS)

def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def parse_time(value, fallback: datetime | None = None) -> datetime:
    if value is None or value == "":
        return fallback or NOW
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1] + "+00:00").astimezone(timezone.utc)
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return fallback or NOW

def atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)

def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        return default

def safe_value(value, depth=0):
    if depth > 4:
        return "<truncated>"
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:40]:
            if SENSITIVE_KEY.search(str(key)):
                out[str(key)] = "<REDACTED>"
            else:
                out[str(key)] = safe_value(item, depth + 1)
        return out
    if isinstance(value, list):
        return [safe_value(x, depth + 1) for x in value[:20]]
    if isinstance(value, str):
        return value[:1000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:1000]

def severity_for(event_type: str, payload: dict) -> str:
    text = " ".join(
        str(x)
        for x in [
            event_type,
            payload.get("status", ""),
            payload.get("result", ""),
            payload.get("overall_status", ""),
            payload.get("error", ""),
            payload.get("message", ""),
            payload.get("summary", ""),
        ]
    ).lower()
    if "critical" in text:
        return "critical"
    if any(x in text for x in ["failed", "failure", "error", "crashed", "fatal"]):
        return "error"
    if any(x in text for x in ["warning", "degraded", "canceled", "cancelled", "stale"]):
        return "warning"
    return "info"

def event_time(payload: dict, fallback: datetime) -> datetime:
    for key in ("at", "timestamp", "completed_at", "checked_at", "processed_at", "generated_at", "started_at", "startedAt"):
        if payload.get(key):
            return parse_time(payload.get(key), fallback)
    return fallback

def source_component(path: Path) -> str:
    try:
        rel = path.relative_to(SOURCE_LOG_ROOT)
        parts = rel.parts
        if len(parts) >= 2 and parts[0] == "Pipelines":
            return parts[1].lower().replace(" ", "_")
        if parts:
            return parts[0].lower().replace(" ", "_")
    except Exception:
        pass
    return path.parent.name.lower().replace(" ", "_") or "unknown"

def make_event(source_kind: str, source_ref: str, payload: dict, fallback: datetime, identity: str | None = None) -> dict:
    etype = str(
        payload.get("event")
        or payload.get("event_type")
        or payload.get("status")
        or payload.get("type")
        or "source_record"
    )
    occurred = event_time(payload, fallback)
    component = str(payload.get("component") or source_component(Path(source_ref)))
    message = payload.get("message") or payload.get("summary")
    if not message and isinstance(payload.get("error"), dict):
        message = payload["error"].get("message")
    if not message and payload.get("error"):
        message = str(payload.get("error"))
    if not message:
        message = etype.replace("_", " ")

    safe = safe_value(payload)
    raw_id = identity or json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    event_id = hashlib.sha256(f"{source_kind}|{source_ref}|{raw_id}".encode("utf-8")).hexdigest()

    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event_id,
        "occurred_at": iso(occurred),
        "observed_at": NOW_ISO,
        "severity": severity_for(etype, payload),
        "event_type": etype,
        "component": component,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "workflow_id": payload.get("workflow_id") or payload.get("workflowId"),
        "workflow_name": payload.get("workflow_name") or payload.get("workflowName"),
        "execution_id": payload.get("execution_id") or payload.get("executionId"),
        "run_id": payload.get("run_id") or payload.get("runId"),
        "record_id": payload.get("record_id") or payload.get("recordId"),
        "source_sha256": payload.get("source_sha256") or payload.get("sourceSha256") or payload.get("sha256"),
        "message": str(message)[:1000],
        "metadata": safe,
    }
    return event

def connect_events() -> sqlite3.Connection:
    OBS_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(EVENT_DB)
    c.execute("PRAGMA journal_mode=DELETE")
    c.execute("PRAGMA synchronous=FULL")
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            occurred_at TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            severity TEXT NOT NULL,
            event_type TEXT NOT NULL,
            component TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            workflow_id TEXT,
            workflow_name TEXT,
            execution_id TEXT,
            run_id TEXT,
            record_id TEXT,
            source_sha256 TEXT,
            message TEXT NOT NULL,
            metadata_json TEXT NOT NULL
        )
        """
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_severity ON events(severity, occurred_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_component ON events(component, occurred_at)")
    return c

def insert_event(c: sqlite3.Connection, event: dict) -> bool:
    occurred = parse_time(event["occurred_at"])
    if occurred < CUTOFF:
        return False
    cur = c.execute(
        """
        INSERT OR IGNORE INTO events (
            event_id, occurred_at, observed_at, severity, event_type, component,
            source_kind, source_ref, workflow_id, workflow_name, execution_id,
            run_id, record_id, source_sha256, message, metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event["event_id"], event["occurred_at"], event["observed_at"], event["severity"],
            event["event_type"], event["component"], event["source_kind"], event["source_ref"],
            event.get("workflow_id"), event.get("workflow_name"), event.get("execution_id"),
            event.get("run_id"), event.get("record_id"), event.get("source_sha256"),
            event["message"], json.dumps(event.get("metadata", {}), sort_keys=True, ensure_ascii=False),
        ),
    )
    return cur.rowcount == 1

def scan_jsonl(c, cursors, source_issues):
    added = 0
    jsonl_state = cursors.setdefault("jsonl", {})
    paths = sorted(SOURCE_LOG_ROOT.rglob("*.jsonl"))
    for path in paths:
        if OBS_LOG_ROOT == path.parent or OBS_LOG_ROOT in path.parents:
            continue
        try:
            st = path.stat()
            key = str(path)
            old = jsonl_state.get(key, {})
            offset = int(old.get("offset", 0))
            if int(old.get("inode", -1)) != st.st_ino or offset > st.st_size:
                offset = 0
            with path.open("rb") as f:
                f.seek(offset)
                while True:
                    start = f.tell()
                    line = f.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        f.seek(start)
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    if not text:
                        continue
                    try:
                        payload = json.loads(text)
                        if not isinstance(payload, dict):
                            payload = {"event": "jsonl_record", "value": payload}
                        event = make_event(
                            "jsonl",
                            key,
                            payload,
                            datetime.fromtimestamp(st.st_mtime, timezone.utc),
                            hashlib.sha256(line).hexdigest(),
                        )
                        if insert_event(c, event):
                            added += 1
                    except Exception as exc:
                        source_issues.append(f"{key}: JSONL parse error at byte {start}: {exc}")
                jsonl_state[key] = {"inode": st.st_ino, "offset": f.tell(), "size": st.st_size}
        except Exception as exc:
            source_issues.append(f"{path}: {exc}")
    return added

def scan_json_files(c, cursors, source_issues):
    added = 0
    seen = cursors.setdefault("json_files", {})
    roots = [SOURCE_LOG_ROOT, VOICE_RUN_ROOT]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            if OBS_LOG_ROOT == path.parent or OBS_LOG_ROOT in path.parents:
                continue
            try:
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                key = str(path)
                if seen.get(key) == digest:
                    continue
                payload = json.loads(data.decode("utf-8", errors="replace"))
                if not isinstance(payload, dict):
                    payload = {"event": "json_record", "value": payload}
                fallback = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                event = make_event("json_file", key, payload, fallback, digest)
                if insert_event(c, event):
                    added += 1
                seen[key] = digest
            except Exception as exc:
                source_issues.append(f"{path}: {exc}")
    return added

def parse_frontmatter(path: Path) -> dict:
    payload = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        first = f.readline().strip()
        if first != "---":
            return payload
        for _ in range(100):
            line = f.readline()
            if not line:
                break
            line = line.rstrip("\n")
            if line.strip() == "---":
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                payload[key] = value
    return payload

def scan_system_markdown_logs(c, cursors, source_issues):
    added = 0
    seen = cursors.setdefault("markdown_logs", {})
    if not SYSTEM_ROOT.exists():
        return 0
    for path in sorted(SYSTEM_ROOT.rglob("*.md")):
        if "logs" not in [p.lower() for p in path.parts]:
            continue
        try:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            key = str(path)
            if seen.get(key) == digest:
                continue
            payload = parse_frontmatter(path)
            if not payload:
                seen[key] = digest
                continue
            payload.setdefault("event", payload.get("type") or "system_log")
            payload.setdefault("component", path.parent.parent.name.lower().replace(" ", "_"))
            fallback = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            event = make_event("system_markdown_log", key, payload, fallback, digest)
            if insert_event(c, event):
                added += 1
            seen[key] = digest
        except Exception as exc:
            source_issues.append(f"{path}: {exc}")
    return added

def n8n_observe(c, cursors, source_issues):
    added = 0
    coverage = {
        "active_workflows": 0,
        "active_production_workflows": 0,
        "active_error_handlers": 0,
        "production_workflows_with_error_handler": 0,
        "production_workflows_without_error_handler": [],
    }
    if not N8N_DB.exists():
        source_issues.append("n8n database is missing")
        return added, coverage
    try:
        db = sqlite3.connect("file:" + str(N8N_DB) + "?mode=ro", uri=True)
        last_id = int(cursors.get("last_n8n_execution_id", 0))
        rows = db.execute(
            """
            SELECT e.id,e.status,e.startedAt,e.stoppedAt,e.workflowId,w.name
            FROM execution_entity e
            LEFT JOIN workflow_entity w ON w.id=e.workflowId
            WHERE e.id > ?
            ORDER BY e.id
            """,
            (last_id,),
        ).fetchall()
        max_id = last_id
        for exec_id, status, started, stopped, workflow_id, workflow_name in rows:
            max_id = max(max_id, int(exec_id))
            if status not in {"error", "crashed", "canceled"}:
                continue
            payload = {
                "event": "n8n_execution_" + status,
                "status": status,
                "startedAt": started,
                "stopped_at": stopped,
                "workflow_id": workflow_id,
                "workflow_name": workflow_name,
                "execution_id": str(exec_id),
                "component": "n8n",
                "message": f"{workflow_name or workflow_id or 'n8n workflow'} execution {status}",
            }
            event = make_event("n8n_execution_metadata", f"execution:{exec_id}", payload, NOW, str(exec_id))
            if insert_event(c, event):
                added += 1
        cursors["last_n8n_execution_id"] = max_id

        wf_rows = db.execute(
            "SELECT id,name,settings,nodes FROM workflow_entity WHERE active=1 ORDER BY name"
        ).fetchall()
        coverage["active_workflows"] = len(wf_rows)
        for workflow_id, name, settings_text, nodes_text in wf_rows:
            try:
                nodes = json.loads(nodes_text or "[]")
            except Exception:
                nodes = []
            is_error = (
                re.search(r"(?:error handler|^\[err-)", name or "", re.I) is not None
                or any((node or {}).get("type") == "n8n-nodes-base.errorTrigger" for node in nodes)
            )
            if is_error:
                coverage["active_error_handlers"] += 1
                continue
            coverage["active_production_workflows"] += 1
            try:
                settings = json.loads(settings_text or "{}")
            except Exception:
                settings = {}
            error_workflow = (
                settings.get("errorWorkflow")
                or settings.get("errorWorkflowId")
                or settings.get("error_workflow_id")
            )
            if error_workflow:
                coverage["production_workflows_with_error_handler"] += 1
            else:
                coverage["production_workflows_without_error_handler"].append(
                    {"id": workflow_id, "name": name}
                )
        db.close()
    except Exception as exc:
        source_issues.append(f"n8n metadata: {exc}")
    return added, coverage

def observe_processing_status(c, cursors, source_issues):
    if not PROCESSING_STATUS.exists():
        source_issues.append("canonical processing status is missing")
        return 0, {}
    try:
        status = json.loads(PROCESSING_STATUS.read_text(encoding="utf-8"))
        fingerprint_obj = {
            "overall_status": status.get("overall_status"),
            "lifecycle": status.get("lifecycle"),
            "automation_backlog": status.get("automation_backlog"),
            "system": [
                {"id": x.get("id"), "status": x.get("status")}
                for x in status.get("system", [])
            ],
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if cursors.get("processing_status_fingerprint") != fingerprint:
            payload = {
                "event": "operational_status_changed",
                "generated_at": status.get("generated_at"),
                "status": status.get("overall_status"),
                "component": "processing_status",
                "lifecycle": status.get("lifecycle", {}),
                "automation_backlog": status.get("automation_backlog", {}),
                "system": fingerprint_obj["system"],
                "message": f"Second Brain operational status is {status.get('overall_status','unknown')}",
            }
            event = make_event("canonical_status", str(PROCESSING_STATUS), payload, NOW, fingerprint)
            inserted = 1 if insert_event(c, event) else 0
            cursors["processing_status_fingerprint"] = fingerprint
            return inserted, status
        return 0, status
    except Exception as exc:
        source_issues.append(f"processing status: {exc}")
        return 0, {}

def systemd_failures(c, cursors, source_issues):
    added = 0
    try:
        p = subprocess.run(
            ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        units = []
        if p.returncode in (0, 1):
            for line in (p.stdout or "").splitlines():
                line = line.strip()
                if line:
                    units.append(line.split()[0])
        previous = set(cursors.get("failed_systemd_units", []))
        current = set(units)
        for unit in sorted(current - previous):
            payload = {
                "event": "systemd_unit_failed",
                "component": "systemd",
                "unit": unit,
                "message": f"systemd unit entered failed state: {unit}",
            }
            if insert_event(c, make_event("systemd_state", f"unit:{unit}", payload, NOW, f"{unit}|failed|{NOW_ISO}")):
                added += 1
        for unit in sorted(previous - current):
            payload = {
                "event": "systemd_unit_recovered",
                "component": "systemd",
                "unit": unit,
                "message": f"systemd unit left failed state: {unit}",
            }
            if insert_event(c, make_event("systemd_state", f"unit:{unit}", payload, NOW, f"{unit}|recovered|{NOW_ISO}")):
                added += 1
        cursors["failed_systemd_units"] = sorted(current)
        return added, sorted(current)
    except Exception as exc:
        source_issues.append(f"systemd failed-unit query: {exc}")
        return 0, []

def rows_as_dicts(cursor):
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]

def summarize(c, coverage, processing_status, failed_units, source_issues, added):
    cutoff24 = iso(NOW - timedelta(hours=24))
    cutoff7 = iso(NOW - timedelta(days=7))
    counts24 = {x: 0 for x in ("info", "warning", "error", "critical")}
    counts7 = dict(counts24)
    for sev, count in c.execute(
        "SELECT severity,count(*) FROM events WHERE occurred_at >= ? GROUP BY severity", (cutoff24,)
    ):
        counts24[sev] = int(count)
    for sev, count in c.execute(
        "SELECT severity,count(*) FROM events WHERE occurred_at >= ? GROUP BY severity", (cutoff7,)
    ):
        counts7[sev] = int(count)

    recent = rows_as_dicts(
        c.execute(
            """
            SELECT occurred_at,severity,event_type,component,workflow_name,execution_id,run_id,message,source_kind,source_ref
            FROM events
            WHERE severity IN ('warning','error','critical')
            ORDER BY occurred_at DESC
            LIMIT 20
            """
        )
    )
    for item in recent:
        item["suggested_diagnostic"] = diagnose_event(item)
    total = c.execute("SELECT count(*) FROM events").fetchone()[0]
    oldest = c.execute("SELECT min(occurred_at) FROM events").fetchone()[0]
    newest = c.execute("SELECT max(occurred_at) FROM events").fetchone()[0]
    db_size = EVENT_DB.stat().st_size if EVENT_DB.exists() else 0

    current = {
        "schema_version": SCHEMA_VERSION,
        "worker_version": WORKER_VERSION,
        "generated_at": NOW_ISO,
        "retention": {
            "canonical_event_days": RETENTION_DAYS,
            "policy": "Canonical normalized observability events are retained for 183 days. Raw source logs and raw n8n execution payloads are supplemental and non-authoritative.",
        },
        "central_event_store": {
            "path": str(EVENT_DB),
            "event_count": int(total),
            "oldest_event_at": oldest,
            "newest_event_at": newest,
            "size_bytes": db_size,
            "new_events_this_refresh": int(added),
        },
        "events_24h": counts24,
        "events_7d": counts7,
        "recent_warning_error_critical": recent,
        "coverage": coverage,
        "processing_status": {
            "path": str(PROCESSING_STATUS),
            "overall_status": processing_status.get("overall_status"),
            "lifecycle": processing_status.get("lifecycle", {}),
            "automation_backlog": processing_status.get("automation_backlog", {}),
        },
        "systemd_failed_units": failed_units,
        "source_issues": source_issues,
        "n8n_execution_history": {
            "canonical": False,
            "note": "Raw n8n execution history is intentionally not the long-term log. Failure metadata is copied into the canonical event store before n8n pruning removes it.",
        },
        "raw_runtime_logs": {
            "canonical": False,
            "note": "Docker json-file logs and journald remain supplemental runtime diagnostics. Their native retention does not define the Second Brain six-month observability contract.",
        },
    }
    atomic_json(CURRENT_STATE, current)
    return current

def render_note(current):
    coverage = current["coverage"]
    counts = current["events_24h"]
    p = current["processing_status"]
    backlog = p.get("automation_backlog", {}) or {}
    lines = [
        "---",
        "type: system-observability",
        "content_origin: system_generated",
        f"schema_version: {SCHEMA_VERSION}",
        f"worker_version: {WORKER_VERSION}",
        f"generated_at: {current['generated_at']}",
        f"retention_days: {RETENTION_DAYS}",
        "---",
        "",
        "# Second Brain Observability",
        "",
        "This is a system-generated mirror of the canonical observability state.",
        "",
        "## Current",
        "",
        f"- Processing status: **{p.get('overall_status') or 'unknown'}**",
        f"- Awaiting owner review: **{(p.get('lifecycle') or {}).get('awaiting_review', 0)}**",
        f"- Active processing: **{(p.get('lifecycle') or {}).get('processing', 0)}**",
        f"- Automation backlog actions: **{backlog.get('action_count', 0)}**",
        f"- Unique backlog artifacts: **{backlog.get('unique_artifact_count', 0)}**",
        f"- Failed systemd units: **{len(current.get('systemd_failed_units', []))}**",
        "",
        "## Events — Last 24 Hours",
        "",
        "| Severity | Count |",
        "|---|---:|",
        f"| Critical | {counts.get('critical', 0)} |",
        f"| Error | {counts.get('error', 0)} |",
        f"| Warning | {counts.get('warning', 0)} |",
        f"| Info | {counts.get('info', 0)} |",
        "",
        "## Workflow Failure Coverage",
        "",
        f"- Active workflows: **{coverage.get('active_workflows', 0)}**",
        f"- Active production workflows: **{coverage.get('active_production_workflows', 0)}**",
        f"- Production workflows with assigned error handler: **{coverage.get('production_workflows_with_error_handler', 0)}**",
        f"- Production workflows missing an error handler: **{len(coverage.get('production_workflows_without_error_handler', []))}**",
        "",
        "## Recent Warnings / Errors",
        "",
    ]
    recent = current.get("recent_warning_error_critical", [])
    if not recent:
        lines.append("- None in the retained event window.")
    else:
        for item in recent[:12]:
            wf = f" — {item.get('workflow_name')}" if item.get("workflow_name") else ""
            lines.append(
                f"- {item.get('occurred_at')} — **{str(item.get('severity','')).upper()}** — "
                f"{item.get('component')} — {item.get('message')}{wf}"
            )
            diag = item.get("suggested_diagnostic") or {}
            if diag:
                lines.append(
                    f"  - Suggested diagnostic ({diag.get('confidence','unknown')} confidence): "
                    f"{diag.get('diagnostic','')}"
                )
                lines.append(f"  - First check: {diag.get('first_check','')}")
    lines += [
        "",
        "## Logging Contract",
        "",
        f"- Canonical normalized event store: `{EVENT_DB}`",
        f"- Canonical observability state: `{CURRENT_STATE}`",
        f"- Retention: **{RETENTION_DAYS} days**.",
        "- Raw n8n execution history is not long-term evidence; failure metadata is persisted here before n8n pruning.",
        "- Raw Docker and journald output are supplemental runtime diagnostics, not the Second Brain canonical event history.",
        "- Source pipeline logs remain detailed evidence and are not rewritten by this service.",
        "- Suggested diagnostics are deterministic triage hints, not root-cause proof; inspect the first failing node/event before changing state.",
        "",
    ]
    if current.get("source_issues"):
        lines += ["## Source Warnings", ""]
        lines += [f"- {x}" for x in current["source_issues"][:20]]
        lines.append("")
    atomic_text(NOTE_PATH, "\n".join(lines))

def set_modes():
    for path in (OBS_LOG_ROOT, STATE_ROOT):
        try:
            os.chmod(path, 0o750)
        except Exception:
            pass
    for path in (EVENT_DB, CURRENT_STATE, CURSOR_STATE):
        if path.exists():
            try:
                os.chmod(path, 0o640)
            except Exception:
                pass
    if NOTE_PATH.exists():
        try:
            os.chmod(NOTE_PATH, 0o644)
        except Exception:
            pass

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()

    OBS_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    cursors = load_json(
        CURSOR_STATE,
        {
            "schema_version": SCHEMA_VERSION,
            "jsonl": {},
            "json_files": {},
            "markdown_logs": {},
            "last_n8n_execution_id": 0,
            "processing_status_fingerprint": None,
            "failed_systemd_units": [],
        },
    )
    source_issues = []
    c = connect_events()
    added = 0

    added += scan_jsonl(c, cursors, source_issues)
    added += scan_json_files(c, cursors, source_issues)
    added += scan_system_markdown_logs(c, cursors, source_issues)
    n_added, coverage = n8n_observe(c, cursors, source_issues)
    added += n_added
    s_added, processing_status = observe_processing_status(c, cursors, source_issues)
    added += s_added
    f_added, failed_units = systemd_failures(c, cursors, source_issues)
    added += f_added

    c.execute("DELETE FROM events WHERE occurred_at < ?", (iso(CUTOFF),))
    c.commit()

    current = summarize(c, coverage, processing_status, failed_units, source_issues, added)
    render_note(current)
    cursors["schema_version"] = SCHEMA_VERSION
    cursors["updated_at"] = NOW_ISO
    atomic_json(CURSOR_STATE, cursors)
    c.close()
    set_modes()

    result = {
        "status": "completed",
        "generated_at": NOW_ISO,
        "new_events": added,
        "retained_events": current["central_event_store"]["event_count"],
        "events_24h": current["events_24h"],
        "coverage": current["coverage"],
        "source_issue_count": len(source_issues),
        "state_path": str(CURRENT_STATE),
        "event_store": str(EVENT_DB),
        "note_path": str(NOTE_PATH),
    }
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()

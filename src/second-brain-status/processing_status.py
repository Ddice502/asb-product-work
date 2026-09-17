#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.5.0"
WORKER_VERSION = "1.5.0"
AI = Path("/AI")
VAULT = AI / "Knowledge/Second Brain"
STATE_ROOT = AI / "State"
OUT_DIR = STATE_ROOT / "Second Brain/Processing Status"
STATE_PATH = OUT_DIR / "current.json"
NOTE_PATH = VAULT / "90 System/Processing Status.md"
NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.isoformat().replace("+00:00", "Z")
SOURCE_ISSUES: list[dict] = []


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            Path(tmp_name).unlink()
        except FileNotFoundError:
            pass


def read_json(path: Path, label: str, required: bool = False):
    if not path.exists():
        if required:
            SOURCE_ISSUES.append({"source": label, "status": "missing", "path": str(path)})
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        SOURCE_ISSUES.append({"source": label, "status": "invalid", "path": str(path), "detail": str(e)})
        return None


def direct_files(directory: Path, predicate=lambda name: True):
    if not directory.exists():
        return []
    items = []
    for p in directory.iterdir():
        if not p.is_file() or p.name.startswith(".") or not predicate(p.name):
            continue
        st = p.stat()
        items.append({
            "name": p.name,
            "path": str(p),
            "size": st.st_size,
            "mtime_ms": st.st_mtime_ns // 1_000_000,
            "modified_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
        })
    return sorted(items, key=lambda x: x["mtime_ms"])


def markdown(directory: Path, predicate=lambda name: True):
    return direct_files(directory, lambda name: name.lower().endswith(".md") and predicate(name))


def count_tsv(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip() and not line.lstrip().startswith("#"))


def dict_count(value) -> int:
    return len(value) if isinstance(value, dict) else 0


def count_pending(files, signatures, signature_builder):
    done = signatures if isinstance(signatures, dict) else {}
    return [f for f in files if done.get(f["name"]) != signature_builder(f)]


def stage(stage_id, label, lifecycle, items, source, detail=""):
    items = list(items)
    return {
        "id": stage_id,
        "label": label,
        "class": "lifecycle",
        "lifecycle": lifecycle,
        "count": len(items),
        "source": source,
        "detail": detail,
        "oldest_at": items[0].get("modified_at") if items else None,
        "examples": [x.get("name", str(x)) for x in items[:5]],
    }


def scalar_stage(stage_id, label, lifecycle, count, source, detail="", examples=None):
    return {
        "id": stage_id,
        "label": label,
        "class": "lifecycle",
        "lifecycle": lifecycle,
        "count": int(count or 0),
        "source": source,
        "detail": detail,
        "oldest_at": None,
        "examples": list(examples or [])[:5],
    }


def backlog_stage(stage_id, label, items, source, detail=""):
    items = list(items)
    return {
        "id": stage_id,
        "label": label,
        "class": "automation_backlog",
        "lifecycle": None,
        "count": len(items),
        "source": source,
        "detail": detail,
        "oldest_at": items[0].get("modified_at") if items else None,
        "examples": [x.get("name", str(x)) for x in items[:5]],
    }


def backlog_scalar_stage(stage_id, label, count, source, detail="", examples=None):
    return {
        "id": stage_id,
        "label": label,
        "class": "automation_backlog",
        "lifecycle": None,
        "count": int(count or 0),
        "source": source,
        "detail": detail,
        "oldest_at": None,
        "examples": list(examples or [])[:5],
    }


def state_failures(state, key):
    records = (state or {}).get(key, {}) if isinstance(state, dict) else {}
    if not isinstance(records, dict):
        return {"retryable": 0, "permanent": 0, "examples": []}
    retryable = permanent = 0
    examples = []
    for record in records.values():
        if not isinstance(record, dict):
            continue
        status = str(record.get("status", "")).lower()
        if "fail" not in status or "completed" in status:
            continue
        name = record.get("source_name") or record.get("sourceName") or record.get("sourcePath") or record.get("notePath") or record.get("runId") or "record"
        examples.append(str(name))
        if "retry" in status or record.get("retry_after") or record.get("retryAfter"):
            retryable += 1
        else:
            permanent += 1
    return {"retryable": retryable, "permanent": permanent, "examples": examples[:5]}


def decision_status(directory: Path, marker: str):
    counts = Counter()
    examples = []
    pattern = re.compile(r"<!--\s*" + re.escape(marker) + r"\s*([\s\S]*?)\s*" + re.escape(marker) + r"\s*-->")
    for file in markdown(directory):
        try:
            text = Path(file["path"]).read_text(encoding="utf-8")
            m = pattern.search(text)
            if not m:
                continue
            record = json.loads(m.group(1))
            status = str(record.get("status", "other")).lower()
            counts[status] += 1
            if status in {"pending", "failed"} and len(examples) < 5:
                examples.append(file["name"])
        except Exception as e:
            SOURCE_ISSUES.append({"source": str(directory), "status": "invalid_decision_file", "path": file["path"], "detail": str(e)})
    return {"pending": counts["pending"], "failed": counts["failed"], "completed": counts["completed"], "other": sum(v for k, v in counts.items() if k not in {"pending", "failed", "completed"}), "examples": examples}


def age_hours(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return round((NOW - dt.astimezone(timezone.utc)).total_seconds() / 3600, 2)
    except Exception:
        return None


def system_check(check_id, label, status, checked_at, detail):
    return {"id": check_id, "label": label, "status": status, "checked_at": checked_at, "age_hours": age_hours(checked_at), "detail": detail}


def docker_n8n_status():
    try:
        p = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", "n8n"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        return "running" if p.returncode == 0 and p.stdout.strip() == "true" else "failed"
    except Exception:
        return "failed"


voice_inbox = direct_files(AI / "Capture/Voice/Inbox")
voice_processing = direct_files(AI / "Capture/Voice/Processing")
doc_inbox = direct_files(AI / "Capture/Documents/Inbox")
doc_processing = direct_files(AI / "Capture/Documents/Processing")
photo_inbox = direct_files(AI / "Capture/Documents/Camera Inbox")
photo_processing = direct_files(AI / "Capture/Documents/Camera Processing")
transcription_review = markdown(VAULT / "90 System/Transcription Review/Pending")
document_filing_review = markdown(VAULT / "00 Inbox/Documents/Needs Filing Review")
photo_review = markdown(VAULT / "00 Inbox/Documents/Photo Capture Review/Pending")

voice_stability = read_json(STATE_ROOT / "Voice/.voice-inbox-stability.json", "Voice stability")
doc_stability = read_json(STATE_ROOT / "Documents/document-ingest-stability-state.json", "Document stability")
ingest_state = read_json(STATE_ROOT / "Documents/document-ingest-state.json", "Document ingest state", True)
ai_review_state = read_json(STATE_ROOT / "Documents/document-ai-review-state.json", "Document AI review state", True)
filing_state_doc = read_json(STATE_ROOT / "Documents/document-filing-state.json", "Document filing state", True)
manual_filing_state = read_json(STATE_ROOT / "Documents/document-manual-filing-state.json", "Document manual filing state")
camera_state = read_json(STATE_ROOT / "Documents/camera-photo-state.json", "Camera photo state", True)
failure_control = read_json(STATE_ROOT / "Second Brain/Failure Control/current.json", "Failure Control state", True)
webpage_state = read_json(STATE_ROOT / "Second Brain/Webpage Import/current.json", "Webpage Import state")

def webpage_request_items(statuses):
    records = (webpage_state or {}).get("requests", {}) if isinstance(webpage_state, dict) else {}
    items = []
    if not isinstance(records, dict):
        return items
    for request_id, rec in records.items():
        if not isinstance(rec, dict) or rec.get("status") not in statuses:
            continue
        timestamp = rec.get("submitted_at") or rec.get("processing_started_at") or rec.get("updated_at")
        dt = None
        if timestamp:
            try:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                dt = None
        items.append({
            "name": str(rec.get("normalized_url") or request_id),
            "path": str(rec.get("final_note_path") or ""),
            "size": 0,
            "mtime_ms": int(dt.timestamp() * 1000) if dt else 0,
            "modified_at": dt.isoformat().replace("+00:00", "Z") if dt else None,
        })
    return sorted(items, key=lambda x: x["mtime_ms"])

webpage_received = webpage_request_items({"queued"})
webpage_processing = webpage_request_items({"processing"})

website_state = read_json(STATE_ROOT / "Second Brain/Website Scraper/current.json", "Website Scraper state")

def website_crawl_items(statuses):
    records = (website_state or {}).get("crawls", {}) if isinstance(website_state, dict) else {}
    items = []
    if not isinstance(records, dict):
        return items
    for crawl_id, rec in records.items():
        if not isinstance(rec, dict) or rec.get("status") not in statuses:
            continue
        timestamp = rec.get("submitted_at") or rec.get("updated_at")
        dt = None
        if timestamp:
            try:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                dt = None
        items.append({
            "name": str(rec.get("normalized_root_url") or crawl_id),
            "path": str(rec.get("manifest_note_path") or ""),
            "size": 0,
            "mtime_ms": int(dt.timestamp() * 1000) if dt else 0,
            "modified_at": dt.isoformat().replace("+00:00", "Z") if dt else None,
        })
    return sorted(items, key=lambda x: x["mtime_ms"])

website_received = website_crawl_items({"queued"})
website_processing = website_crawl_items({"processing"})

personal_email_state = read_json(STATE_ROOT / "Second Brain/Personal Email/current.json", "Personal Email state")
personal_email_queue_all = direct_files(
    STATE_ROOT / "Second Brain/Personal Email/Queue",
    lambda name: name.lower().endswith(".json"),
)

def personal_email_record_items(statuses):
    records = (personal_email_state or {}).get("records", {}) if isinstance(personal_email_state, dict) else {}
    items = []
    if not isinstance(records, dict):
        return items
    for gmail_id, rec in records.items():
        if not isinstance(rec, dict) or rec.get("status") not in statuses:
            continue
        timestamp = rec.get("updated_at") or rec.get("first_seen_at") or rec.get("completed_at")
        dt = None
        if timestamp:
            try:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                dt = None
        items.append({
            "name": str(rec.get("subject") or gmail_id),
            "path": str(rec.get("note_path") or rec.get("archive_dir") or ""),
            "size": 0,
            "mtime_ms": int(dt.timestamp() * 1000) if dt else 0,
            "modified_at": dt.isoformat().replace("+00:00", "Z") if dt else None,
        })
    return sorted(items, key=lambda x: x["mtime_ms"])

personal_email_records = (personal_email_state or {}).get("records", {}) if isinstance(personal_email_state, dict) else {}
def personal_email_queue_pending():
    result = []
    for item in personal_email_queue_all:
        gmail_id = Path(item["path"]).stem
        record = personal_email_records.get(gmail_id, {}) if isinstance(personal_email_records, dict) else {}
        status = str(record.get("status") or "queued") if isinstance(record, dict) else "queued"
        if status in {"failed_retryable", "failed_permanent", "completed"}:
            continue
        result.append(item)
    return result

personal_email_received = personal_email_queue_pending()
personal_email_processing = personal_email_record_items({"processing"})

curator_state = read_json(STATE_ROOT / "Second Brain/nightly-curator-state.json", "Nightly Curator state", True)
curator_filing_state = read_json(STATE_ROOT / "Second Brain/curator-filing-state.json", "Curator filing state", True)
entity_review_state = read_json(STATE_ROOT / "Second Brain/entity-action-review-state.json", "Entity Action Review state", True)
entity_creation_state = read_json(STATE_ROOT / "Second Brain/controlled-entity-creation-state.json", "Controlled Entity Creation state", True)
task_queue_state = read_json(STATE_ROOT / "Second Brain/controlled-task-queue-state.json", "Controlled Task Queue state", True)

root_inbox = markdown(VAULT / "00 Inbox")
curator_digests = [f for f in root_inbox if re.search(r"_nightly-curator-review\.md$", f["name"], re.I)]
source_notes = [f for f in root_inbox if not re.search(r"_nightly-curator-review\.md$", f["name"], re.I)]
entity_reviews = markdown(VAULT / "80 Tasks/Entity Action Reviews", lambda n: bool(re.search(r"_entity-action-review\.md$", n, re.I)))

curator_rules = str((curator_state or {}).get("rules_version", "1.6.0"))
entity_rules = str((entity_review_state or {}).get("rules_version", "1.1.0"))
creation_rules = str((entity_creation_state or {}).get("rules_version", "1.0.0"))
task_rules = str((task_queue_state or {}).get("rules_version", "1.0.0"))
curator_pending = count_pending(source_notes, (curator_state or {}).get("reviewed"), lambda f: f"{curator_rules}:{f['mtime_ms']}:{f['size']}")
curator_filing_pending = count_pending(curator_digests, (curator_filing_state or {}).get("processed_digests"), lambda f: f"{f['mtime_ms']}:{f['size']}")
entity_review_pending = count_pending(curator_digests, (entity_review_state or {}).get("processed_digests"), lambda f: f"{entity_rules}:{f['mtime_ms']}:{f['size']}")
entity_creation_pending = count_pending(entity_reviews, (entity_creation_state or {}).get("processed_reviews"), lambda f: f"{creation_rules}:{f['mtime_ms']}:{f['size']}")
task_queue_pending = count_pending(entity_reviews, (task_queue_state or {}).get("processed_reviews"), lambda f: f"{task_rules}:{f['mtime_ms']}:{f['size']}")

project_decisions = decision_status(VAULT / "90 System/Project Review Control/decisions", "PROJECT_REVIEW_DECISION_JSON")
task_decisions = decision_status(VAULT / "90 System/Task Review Control/decisions", "TASK_REVIEW_DECISION_JSON")

stages = [
    stage("voice_received", "Voice capture inbox", "received", voice_inbox, "/AI/Capture/Voice/Inbox"),
    scalar_stage("voice_stabilizing", "Voice stabilization", "stabilizing", dict_count((voice_stability or {}).get("files")), "/AI/State/Voice/.voice-inbox-stability.json"),
    stage("voice_processing", "Voice processing", "processing", voice_processing, "/AI/Capture/Voice/Processing"),
    stage("transcription_review", "Transcription owner review", "awaiting_review", transcription_review, "90 System/Transcription Review/Pending"),
    stage("document_received", "Document capture inbox", "received", doc_inbox, "/AI/Capture/Documents/Inbox"),
    scalar_stage("document_stabilizing", "Document stabilization", "stabilizing", dict_count((doc_stability or {}).get("files")), "/AI/State/Documents/document-ingest-stability-state.json"),
    stage("document_processing", "Document processing", "processing", doc_processing, "/AI/Capture/Documents/Processing"),
    stage("document_filing_review", "Document filing review", "awaiting_review", document_filing_review, "00 Inbox/Documents/Needs Filing Review"),
    stage("photo_received", "Photo capture inbox", "received", photo_inbox, "/AI/Capture/Documents/Camera Inbox"),
    stage("photo_processing", "Photo processing", "processing", photo_processing, "/AI/Capture/Documents/Camera Processing"),
    stage("photo_review", "Photo capture owner review", "awaiting_review", photo_review, "00 Inbox/Documents/Photo Capture Review/Pending"),
    stage("webpage_received", "Webpage import queue", "received", webpage_received, "/AI/State/Second Brain/Webpage Import/current.json"),
    stage("webpage_processing", "Webpage import processing", "processing", webpage_processing, "/AI/State/Second Brain/Webpage Import/current.json"),
    stage("website_scrape_received", "Website scrape queue", "received", website_received, "/AI/State/Second Brain/Website Scraper/current.json"),
    stage("website_scrape_processing", "Website scrape processing", "processing", website_processing, "/AI/State/Second Brain/Website Scraper/current.json"),
    stage("personal_email_received", "Personal Gmail queue", "received", personal_email_received, "/AI/State/Second Brain/Personal Email/Queue"),
    stage("personal_email_processing", "Personal Gmail processing", "processing", personal_email_processing, "/AI/State/Second Brain/Personal Email/current.json"),
    backlog_stage("curator_pending", "Nightly Curator backlog", curator_pending, "/AI/State/Second Brain/nightly-curator-state.json"),
    backlog_stage("voice_filing_pending", "Voice-note filing backlog", curator_filing_pending, "/AI/State/Second Brain/curator-filing-state.json"),
    backlog_stage("entity_review_pending", "Entity Action Review generation backlog", entity_review_pending, "/AI/State/Second Brain/entity-action-review-state.json"),
    backlog_stage("entity_creation_pending", "Controlled entity creation backlog", entity_creation_pending, "/AI/State/Second Brain/controlled-entity-creation-state.json"),
    backlog_stage("task_queue_pending", "Controlled task import backlog", task_queue_pending, "/AI/State/Second Brain/controlled-task-queue-state.json"),
    backlog_scalar_stage("project_decisions_pending", "Project decisions awaiting execution", project_decisions["pending"], "90 System/Project Review Control/decisions", examples=project_decisions["examples"]),
    backlog_scalar_stage("task_decisions_pending", "Task decisions awaiting execution", task_decisions["pending"], "90 System/Task Review Control/decisions", examples=task_decisions["examples"]),
]

held = int(((entity_creation_state or {}).get("last_run") or {}).get("hold_count", 0) or 0)
if held:
    stages.append(scalar_stage("entity_held", "Entity recommendations held for owner review", "held", held, "/AI/State/Second Brain/controlled-entity-creation-state.json"))

# Item-level ingest failures are normalized by Failure Control so retries and dead-letter
# outcomes are not double-counted from both the source ledger and the central registry.
downstream_failures = [
    state_failures(ai_review_state, "notes"),
    state_failures(filing_state_doc, "notes"),
    state_failures(manual_filing_state, "notes"),
]
failure_summary = (failure_control or {}).get("summary", {}) if isinstance(failure_control, dict) else {}
failure_retryable = int(failure_summary.get("failed_retryable", 0) or 0)
failure_permanent = int(failure_summary.get("failed_permanent", 0) or 0)
failure_halting = int(failure_summary.get("halting_permanent", 0) or 0)
downstream_retryable = sum(x["retryable"] for x in downstream_failures)
downstream_permanent = sum(x["permanent"] for x in downstream_failures)
retryable_failures = failure_retryable + downstream_retryable
permanent_failures = failure_permanent + downstream_permanent + project_decisions["failed"] + task_decisions["failed"]
if retryable_failures:
    stages.append(scalar_stage(
        "retryable_failures",
        "Retryable failures",
        "failed_retryable",
        retryable_failures,
        "/AI/State/Second Brain/Failure Control/current.json + downstream pipeline state",
        detail=f"{failure_retryable} centrally controlled; {downstream_retryable} downstream"
    ))
if permanent_failures:
    stages.append(scalar_stage(
        "permanent_failures",
        "Permanent isolated/unresolved failures",
        "failed_permanent",
        permanent_failures,
        "/AI/State/Second Brain/Failure Control/current.json + downstream state/decisions",
        detail=f"{failure_halting} halting; {max(0, failure_permanent - failure_halting)} isolated; {downstream_permanent + project_decisions['failed'] + task_decisions['failed']} downstream"
    ))

lifecycle = {k: 0 for k in ["received", "stabilizing", "processing", "awaiting_review", "completed_with_warning", "held", "failed_retryable", "failed_permanent"]}
for item in stages:
    lifecycle_name = item.get("lifecycle")
    if item.get("class") == "lifecycle" and lifecycle_name in lifecycle:
        lifecycle[lifecycle_name] += item["count"]

backlog_stages = [x for x in stages if x.get("class") == "automation_backlog"]
automation_backlog_action_count = sum(x["count"] for x in backlog_stages)

# Unique backlog artifacts are computed by artifact class rather than stage action.
# The same Curator digest can legitimately require both filing and entity-review work;
# the same entity-review note can require both entity creation and task import.
backlog_artifact_keys = set()
for f in curator_pending:
    backlog_artifact_keys.add(("source_note", f["name"]))
for f in curator_filing_pending:
    backlog_artifact_keys.add(("curator_digest", f["name"]))
for f in entity_review_pending:
    backlog_artifact_keys.add(("curator_digest", f["name"]))
for f in entity_creation_pending:
    backlog_artifact_keys.add(("entity_review", f["name"]))
for f in task_queue_pending:
    backlog_artifact_keys.add(("entity_review", f["name"]))
for i in range(project_decisions["pending"]):
    backlog_artifact_keys.add(("project_decision", str(i)))
for i in range(task_decisions["pending"]):
    backlog_artifact_keys.add(("task_decision", str(i)))
automation_backlog_unique_artifact_count = len(backlog_artifact_keys)

stale_processing = []
stale_backlog = []
for item in stages:
    if not item["count"] or not item["oldest_at"]:
        continue
    try:
        dt = datetime.fromisoformat(item["oldest_at"].replace("Z", "+00:00"))
        if (NOW - dt).total_seconds() <= 1800:
            continue
        if item.get("class") == "automation_backlog":
            stale_backlog.append(item["id"])
        elif item.get("lifecycle") in {"received", "stabilizing", "processing"}:
            stale_processing.append(item["id"])
    except Exception:
        pass

auditor = read_json(STATE_ROOT / "Second Brain/Auditor/production-heartbeat.json", "Production auditor heartbeat", True)
backup_server = read_json(STATE_ROOT / "Second Brain/Auditor/Backup Evidence/server-daily.json", "Server backup evidence", True)
backup_workstation = read_json(STATE_ROOT / "Second Brain/Auditor/Backup Evidence/workstation-daily.json", "Workstation backup evidence", True)
backup_monthly = read_json(STATE_ROOT / "Second Brain/Auditor/Backup Evidence/monthly-external-drive.json", "Monthly backup evidence")
host_monitor = read_json(STATE_ROOT / "Second Brain/Auditor/External Monitors/host-monitor.json", "Host monitor")
workstation_monitor = read_json(STATE_ROOT / "Second Brain/Auditor/External Monitors/workstation-monitor.json", "Workstation monitor")

system = [
    system_check("n8n", "n8n container", docker_n8n_status(), NOW_ISO, "Docker runtime state"),
    system_check("auditor", "Production auditor", "critical" if int((auditor or {}).get("critical_count", 0) or 0) else str((auditor or {}).get("overall_status", "unknown")), (auditor or {}).get("last_success"), f"{int((auditor or {}).get('finding_count', 0) or 0)} findings; {int((auditor or {}).get('critical_count', 0) or 0)} critical"),
    system_check("backup_server", "Server backup", str((backup_server or {}).get("status", "missing")), (backup_server or {}).get("completed_at"), str((backup_server or {}).get("repository_check", ""))),
    system_check("backup_workstation", "Workstation backup", str((backup_workstation or {}).get("status", "missing")), (backup_workstation or {}).get("completed_at"), str((backup_workstation or {}).get("repository_check", ""))),
    system_check("backup_monthly", "Disconnected monthly backup", str((backup_monthly or {}).get("status", "not_yet_recorded")), (backup_monthly or {}).get("completed_at"), str((backup_monthly or {}).get("full_restore_test", ""))),
    system_check("host_monitor", "Host monitor", str((host_monitor or {}).get("status", "missing")), (host_monitor or {}).get("checked_at"), str((host_monitor or {}).get("message", ""))),
    system_check("workstation_monitor", "Workstation monitor", str((workstation_monitor or {}).get("status", "missing")), (workstation_monitor or {}).get("checked_at"), str((workstation_monitor or {}).get("message", ""))),
]

system_critical = any(x["status"] in {"critical", "failed"} for x in system)
system_warning = any(x["status"] in {"warning", "missing", "unknown"} for x in system) or bool(SOURCE_ISSUES) or bool(stale_processing)
if failure_halting or system_critical:
    overall = "critical"
elif lifecycle["awaiting_review"] or lifecycle["held"] or lifecycle["failed_permanent"]:
    overall = "review_needed"
elif system_warning:
    overall = "warning"
elif lifecycle["received"] + lifecycle["stabilizing"] + lifecycle["processing"] + lifecycle["failed_retryable"] or automation_backlog_action_count:
    overall = "working"
else:
    overall = "clear"

camera_records = (camera_state or {}).get("files", {}) if isinstance(camera_state, dict) else {}
camera_pending = sum(1 for x in camera_records.values() if isinstance(x, dict) and x.get("status") == "pending_owner_review")

snapshot = {
    "schema_version": SCHEMA_VERSION,
    "worker_version": WORKER_VERSION,
    "generated_at": NOW_ISO,
    "overall_status": overall,
    "lifecycle": lifecycle,
    "automation_backlog": {
        "action_count": automation_backlog_action_count,
        "unique_artifact_count": automation_backlog_unique_artifact_count,
        "stale_stage_ids": stale_backlog,
        "note": "Backlog actions are open downstream automation work, not items actively executing. One artifact may legitimately have multiple independent downstream actions.",
    },
    "failure_control": {
        "path": "/AI/State/Second Brain/Failure Control/current.json",
        "failed_retryable": failure_retryable,
        "failed_permanent": failure_permanent,
        "halting_permanent": failure_halting,
        "isolated_permanent": max(0, failure_permanent - failure_halting),
        "max_retries": int(((failure_control or {}).get("policy") or {}).get("max_retries", 3) or 3) if isinstance(failure_control, dict) else 3,
        "progressive_backoff_seconds": ((failure_control or {}).get("policy") or {}).get("progressive_backoff_seconds", [60, 300, 900]) if isinstance(failure_control, dict) else [60, 300, 900],
    },
    "stages": stages,
    "system": system,
    "source_issues": SOURCE_ISSUES,
    "stale_processing_stages": stale_processing,
    "stale_backlog_stages": stale_backlog,
    "diagnostics": {
        "voice_review_pending_ledger_count": count_tsv(STATE_ROOT / "Voice/.voice-review-pending-sha256.tsv"),
        "camera_state_pending_owner_review": camera_pending,
        "webpage_import_summary": (webpage_state or {}).get("summary", {}) if isinstance(webpage_state, dict) else {},
        "website_scraper_summary": (website_state or {}).get("summary", {}) if isinstance(website_state, dict) else {},
        "n8n_execution_database_authoritative": False,
        "n8n_execution_database_note": "Raw n8n execution rows are intentionally excluded from canonical processing status because successful executions are not retained consistently and stale running rows can survive restarts.",
    },
}

labels = {"clear": "Clear", "working": "Working", "review_needed": "Review needed", "warning": "Warning", "critical": "Critical"}
lines = [
    "---", "type: processing-status", f'schema_version: "{SCHEMA_VERSION}"', f'generated_at: "{NOW_ISO}"',
    f'overall_status: "{overall}"', f'received_count: {lifecycle["received"]}', f'stabilizing_count: {lifecycle["stabilizing"]}',
    f'processing_count: {lifecycle["processing"]}', f'awaiting_review_count: {lifecycle["awaiting_review"]}', f'held_count: {lifecycle["held"]}',
    f'automation_backlog_action_count: {automation_backlog_action_count}', f'automation_backlog_unique_artifact_count: {automation_backlog_unique_artifact_count}',
    f'failed_retryable_count: {lifecycle["failed_retryable"]}', f'failed_permanent_count: {lifecycle["failed_permanent"]}',
    f'halting_permanent_failure_count: {failure_halting}',
    "content_origin: system_generated", "---", "", "# Processing Status", "",
    f"> **{labels.get(overall, overall)}.** Canonical processing snapshot generated {NOW_ISO}.", "",
    "## Current Load", "", "| Lifecycle | Count |", "|---|---:|",
    f'| Received | {lifecycle["received"]} |', f'| Stabilizing | {lifecycle["stabilizing"]} |', f'| Processing | {lifecycle["processing"]} |',
    f'| Awaiting review | {lifecycle["awaiting_review"]} |', f'| Held | {lifecycle["held"]} |', f'| Retryable failures | {lifecycle["failed_retryable"]} |',
    f'| Permanent isolated/unresolved failures | {lifecycle["failed_permanent"]} |', f'| Halting permanent failures | {failure_halting} |', "",
    "## Failure Control", "",
    "- Retry policy: **1 initial attempt + 3 retries**.",
    f"- Progressive backoff: **{', '.join(str(x) + 's' for x in snapshot['failure_control']['progressive_backoff_seconds'])}**.",
    f"- Retryable now: **{failure_retryable}**.",
    f"- Permanent isolated: **{max(0, failure_permanent - failure_halting)}**.",
    f"- Permanent halting: **{failure_halting}**.",
    "- Isolated item failures do not halt unrelated items; a halting flag is reserved for failure-isolation/retry-control failure.",
    "",
    "## Automation Backlog", "",
    f"- Pending stage actions: **{automation_backlog_action_count}**",
    f"- Unique artifacts represented: **{automation_backlog_unique_artifact_count}**",
    f"- Stale backlog stages (>30 min): **{len(stale_backlog)}**",
    "- Backlog is reported separately from lifecycle `processing`; it means work waiting for downstream automation, not work actively executing.",
    "", "## Pipeline Stages", "", "| Stage | Class | Lifecycle | Count | Oldest |", "|---|---|---|---:|---|",
]
for item in stages:
    lines.append(f'| {item["label"]} | {item.get("class","lifecycle")} | {item.get("lifecycle") or "—"} | {item["count"]} | {item["oldest_at"] or "—"} |')
lines += ["", "## System Checks", "", "| Check | Status | Last evidence | Detail |", "|---|---|---|---|"]
for item in system:
    detail = str(item["detail"] or "").replace("|", "\\|")
    lines.append(f'| {item["label"]} | {item["status"]} | {item["checked_at"] or "—"} | {detail} |')
lines += ["", "## Status Contract", "", "- Operational state JSON: `/AI/State/Second Brain/Processing Status/current.json`", "- This note is a system-generated mirror for Obsidian.", "- Source workflows keep their detailed ledgers; this snapshot normalizes current open work and system evidence.", "- Raw n8n execution-table `running` rows are not treated as processing truth.", ""]
if SOURCE_ISSUES or stale_processing or stale_backlog:
    lines += ["## Status Source Warnings", ""]
    for issue in SOURCE_ISSUES:
        lines.append(f'- {issue["source"]}: {issue["status"]}' + (f' — {issue.get("detail")}' if issue.get("detail") else ""))
    for item in stale_processing:
        lines.append(f"- {item}: active lifecycle item older than 30 minutes.")
    for item in stale_backlog:
        lines.append(f"- {item}: automation backlog stage older than 30 minutes.")
    lines.append("")

atomic_write(STATE_PATH, json.dumps(snapshot, indent=2) + "\n")
atomic_write(NOTE_PATH, "\n".join(lines) + "\n")
print(json.dumps({"status": "completed", "generated_at": NOW_ISO, "overall_status": overall, "state_path": str(STATE_PATH), "note_path": str(NOTE_PATH), "lifecycle": lifecycle, "automation_backlog": snapshot["automation_backlog"], "failure_control": snapshot["failure_control"]}))

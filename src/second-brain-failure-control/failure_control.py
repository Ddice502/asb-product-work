#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
WORKER_VERSION = "1.0.0"
MAX_RETRIES = 3
TOTAL_ATTEMPTS = 4
BACKOFF_SECONDS = [60, 300, 900]
RETENTION_DAYS = 183

IN_CONTAINER = Path("/State/Second Brain").exists() and Path("/Capture").exists()
STATE_ROOT = Path("/State/Second Brain") if IN_CONTAINER else Path("/AI/State/Second Brain")
LOG_ROOT = Path("/Logs") if IN_CONTAINER else Path("/AI/Logs")
CURRENT = STATE_ROOT / "Failure Control/current.json"
LOCK = STATE_ROOT / "Failure Control/.lock"
EVENT_LOG = LOG_ROOT / "Second Brain Failure Control/events.jsonl"

DOC_STATE = Path("/Capture/Documents/document-ingest-state.json") if IN_CONTAINER else Path("/AI/State/Documents/document-ingest-state.json")
CAMERA_STATE = Path("/Capture/Documents/camera-photo-state.json") if IN_CONTAINER else Path("/AI/State/Documents/camera-photo-state.json")

def now_dt():
    return datetime.now(timezone.utc)

def now_iso():
    return now_dt().isoformat().replace("+00:00", "Z")

def parse_time(value):
    if not value:
        return None
    try:
        s = str(value)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return None

def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass

def append_event(value):
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False) + "\n")

def base_state():
    return {
        "schema_version": SCHEMA_VERSION,
        "worker_version": WORKER_VERSION,
        "updated_at": now_iso(),
        "policy": {
            "max_retries": MAX_RETRIES,
            "total_attempts": TOTAL_ATTEMPTS,
            "progressive_backoff_seconds": BACKOFF_SECONDS,
            "permanent_item_failures_are_isolated_not_halting": True,
            "halting_is_reserved_for_failure_isolation_or_retry_control_failure": True,
        },
        "records": {},
        "summary": {},
    }

def record_key(pipeline, source_id):
    return f"{pipeline}|{source_id}"

def sanitize_text(value, limit=2000):
    return str(value or "").replace("\x00", "")[:limit]

def source_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def recompute_summary(state):
    summary = {
        "failed_retryable": 0,
        "retry_queued": 0,
        "failed_permanent": 0,
        "isolated_permanent": 0,
        "halting_permanent": 0,
        "resolved": 0,
    }
    for rec in state.get("records", {}).values():
        disposition = rec.get("disposition")
        if disposition in summary:
            summary[disposition] += 1
        if disposition == "failed_permanent":
            if rec.get("halting") is True:
                summary["halting_permanent"] += 1
            else:
                summary["isolated_permanent"] += 1
    state["summary"] = summary
    state["updated_at"] = now_iso()

def prune_resolved(state):
    cutoff = now_dt() - timedelta(days=RETENTION_DAYS)
    remove = []
    for key, rec in state.get("records", {}).items():
        if rec.get("disposition") != "resolved":
            continue
        d = parse_time(rec.get("resolved_at") or rec.get("updated_at"))
        if d and d < cutoff:
            remove.append(key)
    for key in remove:
        state["records"].pop(key, None)

class StateLock:
    def __enter__(self):
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        self.f = LOCK.open("a+")
        fcntl.flock(self.f.fileno(), fcntl.LOCK_EX)
        return self
    def __exit__(self, exc_type, exc, tb):
        fcntl.flock(self.f.fileno(), fcntl.LOCK_UN)
        self.f.close()

def update_state(mutator):
    with StateLock():
        state = load_json(CURRENT, base_state())
        if not isinstance(state, dict):
            state = base_state()
        state.setdefault("records", {})
        mutator(state)
        prune_resolved(state)
        recompute_summary(state)
        atomic_json(CURRENT, state)
        return state

def register(args):
    if args.disposition not in {"failed_retryable", "failed_permanent"}:
        raise RuntimeError("register disposition must be failed_retryable or failed_permanent")
    if args.attempt < 1:
        raise RuntimeError("attempt must be at least 1")
    if args.disposition == "failed_retryable" and not args.retry_at:
        raise RuntimeError("failed_retryable requires --retry-at")
    key = record_key(args.pipeline, args.source_id)
    at = now_iso()
    def mutate(state):
        old = state["records"].get(key, {})
        history = list(old.get("history", []))[-19:]
        history.append({
            "at": at,
            "event": args.disposition,
            "attempt": args.attempt,
            "error": sanitize_text(args.error, 1000),
            "source_path": args.source_path,
            "retry_at": args.retry_at,
        })
        state["records"][key] = {
            **old,
            "pipeline": args.pipeline,
            "source_id": args.source_id,
            "source_name": sanitize_text(args.source_name, 300),
            "source_sha256": args.source_sha256 or old.get("source_sha256"),
            "disposition": args.disposition,
            "attempt": int(args.attempt),
            "max_retries": MAX_RETRIES,
            "total_attempts": TOTAL_ATTEMPTS,
            "retry_at": args.retry_at if args.disposition == "failed_retryable" else None,
            "retry_source_path": args.source_path or old.get("retry_source_path"),
            "retry_destination": args.retry_destination or old.get("retry_destination"),
            "retry_mode": (
                "central_dispatch"
                if (args.retry_destination or old.get("retry_destination"))
                else "source_managed"
            ) if args.disposition == "failed_retryable" else old.get("retry_mode"),
            "dead_letter_path": args.dead_letter_path or old.get("dead_letter_path"),
            "last_error": sanitize_text(args.error, 2000),
            "halting": bool(args.halting),
            "dispatch_failures": 0 if args.disposition == "failed_retryable" else int(old.get("dispatch_failures", 0) or 0),
            "first_failed_at": old.get("first_failed_at") or at,
            "last_failed_at": at,
            "updated_at": at,
            "resolved_at": None,
            "resolution": None,
            "history": history,
        }
    state = update_state(mutate)
    append_event({
        "schema_version": SCHEMA_VERSION,
        "event": args.disposition,
        "at": at,
        "pipeline": args.pipeline,
        "source_id": args.source_id,
        "attempt": args.attempt,
        "retry_at": args.retry_at,
        "halting": bool(args.halting),
        "error": sanitize_text(args.error, 1000),
    })
    print(json.dumps({"status": "completed", "record": state["records"][key], "summary": state["summary"]}))

def resolve(args):
    key = record_key(args.pipeline, args.source_id)
    at = now_iso()
    found = {"value": False}
    def mutate(state):
        rec = state["records"].get(key)
        if not rec:
            return
        found["value"] = True
        history = list(rec.get("history", []))[-19:]
        history.append({"at": at, "event": "resolved", "message": sanitize_text(args.message, 1000)})
        rec.update({
            "disposition": "resolved",
            "halting": False,
            "retry_at": None,
            "resolved_at": at,
            "resolution": sanitize_text(args.message, 1000),
            "updated_at": at,
            "history": history,
        })
    state = update_state(mutate)
    if found["value"]:
        append_event({
            "schema_version": SCHEMA_VERSION,
            "event": "resolved",
            "at": at,
            "pipeline": args.pipeline,
            "source_id": args.source_id,
            "message": sanitize_text(args.message, 1000),
        })
    print(json.dumps({"status": "completed", "found": found["value"], "summary": state["summary"]}))

def unique_destination(directory, filename, source_id):
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    safe_id = "".join(ch for ch in source_id if ch.isalnum())[:8] or "retry"
    for i in range(1, 1000):
        alt = directory / f"{stem}_retry_{safe_id}_{i}{suffix}"
        if not alt.exists():
            return alt
    raise RuntimeError("Could not allocate a unique retry destination")

def move_verified(source, destination, expected_sha=None):
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        raise RuntimeError(f"Retry source is missing: {source}")
    actual = source_hash(source)
    if expected_sha and actual.lower() != str(expected_sha).lower():
        raise RuntimeError(f"Retry source checksum mismatch: expected {expected_sha}, got {actual}")
    try:
        os.replace(source, destination)
    except OSError as exc:
        if getattr(exc, "errno", None) != 18:
            raise
        temp = destination.with_name("." + destination.name + f".{os.getpid()}.partial")
        if temp.exists():
            temp.unlink()
        shutil.copy2(source, temp)
        copied = source_hash(temp)
        if copied != actual:
            temp.unlink(missing_ok=True)
            raise RuntimeError("Cross-filesystem retry copy checksum mismatch")
        os.replace(temp, destination)
        source.unlink()
    final_hash = source_hash(destination)
    if final_hash != actual:
        raise RuntimeError("Retry destination checksum verification failed")
    return final_hash

def reconcile_pipeline_states(state):
    doc = load_json(DOC_STATE, {})
    camera = load_json(CAMERA_STATE, {})
    now = now_iso()
    changed = []
    for key, rec in state.get("records", {}).items():
        if rec.get("disposition") == "resolved":
            continue
        pipeline = rec.get("pipeline")
        source_id = rec.get("source_id")
        if pipeline == "document_ingest":
            source_rec = ((doc.get("files") or {}).get(source_id) if isinstance(doc, dict) else None)
            if isinstance(source_rec, dict) and source_rec.get("status") == "complete":
                rec.update({
                    "disposition": "resolved",
                    "halting": False,
                    "retry_at": None,
                    "resolved_at": now,
                    "resolution": "Document ingest completed successfully.",
                    "updated_at": now,
                })
                changed.append((pipeline, source_id))
        elif pipeline == "camera_photo":
            source_rec = ((camera.get("files") or {}).get(source_id) if isinstance(camera, dict) else None)
            if isinstance(source_rec, dict) and source_rec.get("status") == "pending_owner_review" and source_rec.get("completed") is True:
                rec.update({
                    "disposition": "resolved",
                    "halting": False,
                    "retry_at": None,
                    "resolved_at": now,
                    "resolution": "Camera photo processing completed and is awaiting owner review.",
                    "updated_at": now,
                })
                changed.append((pipeline, source_id))
    return changed

def process_due(_args):
    if not IN_CONTAINER:
        raise RuntimeError("process-due must run inside the n8n container namespace")
    at = now_dt()
    results = []
    events = []
    def mutate(state):
        reconciled = reconcile_pipeline_states(state)
        for pipeline, source_id in reconciled:
            events.append({
                "schema_version": SCHEMA_VERSION,
                "event": "resolved_by_state_reconciliation",
                "at": now_iso(),
                "pipeline": pipeline,
                "source_id": source_id,
            })
        for key, rec in list(state.get("records", {}).items()):
            if rec.get("disposition") != "failed_retryable":
                continue
            retry_at = parse_time(rec.get("retry_at"))
            if not retry_at or retry_at > at:
                continue
            source = rec.get("retry_source_path")
            destination_dir = rec.get("retry_destination")
            if not destination_dir:
                # Source-managed retry: the owning pipeline already left the source in
                # its retryable location and enforces retry_at itself (camera-photo path).
                rec["retry_mode"] = "source_managed"
                rec["updated_at"] = now_iso()
                continue
            if not source:
                rec["dispatch_failures"] = int(rec.get("dispatch_failures", 0) or 0) + 1
                rec["last_dispatch_error"] = "Retry record is missing its source path."
                rec["updated_at"] = now_iso()
            else:
                try:
                    src = Path(source)
                    dest = unique_destination(Path(destination_dir), src.name, rec.get("source_id", "retry"))
                    move_verified(src, dest, rec.get("source_sha256"))
                    rec.update({
                        "disposition": "retry_queued",
                        "retry_at": None,
                        "retry_source_path": str(dest),
                        "last_dispatched_at": now_iso(),
                        "last_dispatch_error": None,
                        "dispatch_failures": 0,
                        "updated_at": now_iso(),
                    })
                    results.append({"pipeline": rec.get("pipeline"), "source_id": rec.get("source_id"), "destination": str(dest)})
                    events.append({
                        "schema_version": SCHEMA_VERSION,
                        "event": "retry_queued",
                        "at": now_iso(),
                        "pipeline": rec.get("pipeline"),
                        "source_id": rec.get("source_id"),
                        "attempt": rec.get("attempt"),
                        "destination": str(dest),
                    })
                    continue
                except Exception as exc:
                    rec["dispatch_failures"] = int(rec.get("dispatch_failures", 0) or 0) + 1
                    rec["last_dispatch_error"] = sanitize_text(exc, 1000)
                    rec["updated_at"] = now_iso()
            if int(rec.get("dispatch_failures", 0) or 0) >= 3:
                rec.update({
                    "disposition": "failed_permanent",
                    "halting": True,
                    "retry_at": None,
                    "last_error": "Retry dispatcher failed three times: " + sanitize_text(rec.get("last_dispatch_error"), 1000),
                    "updated_at": now_iso(),
                })
                events.append({
                    "schema_version": SCHEMA_VERSION,
                    "event": "retry_dispatch_failed_permanent",
                    "at": now_iso(),
                    "pipeline": rec.get("pipeline"),
                    "source_id": rec.get("source_id"),
                    "halting": True,
                    "error": rec.get("last_error"),
                })
    state = update_state(mutate)
    for event in events:
        append_event(event)
    print(json.dumps({
        "status": "completed",
        "mode": "process_due",
        "queued": results,
        "summary": state["summary"],
    }))

def dispatch_container(_args):
    if IN_CONTAINER:
        raise RuntimeError("dispatch-container is a host-side command")
    p = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", "n8n"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if p.returncode != 0 or p.stdout.strip() != "true":
        print(json.dumps({"status": "completed", "mode": "dispatch_container", "decision": "deferred_n8n_unavailable"}))
        return
    p = subprocess.run(
        ["docker", "exec", "n8n", "python3", "/scripts/Second Brain Failure Control/failure_control.py", "process-due"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if p.stdout:
        print(p.stdout.rstrip())
    if p.returncode != 0:
        raise RuntimeError(f"Container retry dispatcher exited {p.returncode}")

def show_status(_args):
    state = load_json(CURRENT, base_state())
    recompute_summary(state)
    print(json.dumps({
        "status": "completed",
        "state_path": str(CURRENT),
        "summary": state.get("summary", {}),
        "unresolved": [
            {
                "pipeline": r.get("pipeline"),
                "source_id": r.get("source_id"),
                "source_name": r.get("source_name"),
                "disposition": r.get("disposition"),
                "attempt": r.get("attempt"),
                "retry_at": r.get("retry_at"),
                "halting": r.get("halting"),
                "last_error": r.get("last_error"),
            }
            for r in state.get("records", {}).values()
            if r.get("disposition") != "resolved"
        ],
    }, indent=2))

def self_test(_args):
    if TOTAL_ATTEMPTS != MAX_RETRIES + 1:
        raise RuntimeError("Retry policy math is invalid")
    if BACKOFF_SECONDS != sorted(BACKOFF_SECONDS) or len(BACKOFF_SECONDS) != MAX_RETRIES:
        raise RuntimeError("Progressive backoff policy is invalid")
    print(json.dumps({
        "status": "completed",
        "mode": "self_test",
        "policy": {
            "max_retries": MAX_RETRIES,
            "total_attempts": TOTAL_ATTEMPTS,
            "progressive_backoff_seconds": BACKOFF_SECONDS,
            "isolated_permanent_is_halting": False,
            "retry_dispatch_failure_after_three_checks_is_halting": True,
            "source_managed_retries_supported": True,
        },
        "in_container": IN_CONTAINER,
    }))

def build_parser():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("register")
    r.add_argument("--pipeline", required=True)
    r.add_argument("--source-id", required=True)
    r.add_argument("--source-name", default="")
    r.add_argument("--source-sha256", default="")
    r.add_argument("--source-path", default="")
    r.add_argument("--disposition", required=True)
    r.add_argument("--attempt", type=int, required=True)
    r.add_argument("--retry-at", default="")
    r.add_argument("--retry-destination", default="")
    r.add_argument("--dead-letter-path", default="")
    r.add_argument("--error", default="")
    r.add_argument("--halting", action="store_true")
    r.set_defaults(func=register)

    rr = sub.add_parser("resolve")
    rr.add_argument("--pipeline", required=True)
    rr.add_argument("--source-id", required=True)
    rr.add_argument("--message", default="Resolved")
    rr.set_defaults(func=resolve)

    pd = sub.add_parser("process-due")
    pd.set_defaults(func=process_due)

    dc = sub.add_parser("dispatch-container")
    dc.set_defaults(func=dispatch_container)

    st = sub.add_parser("status")
    st.set_defaults(func=show_status)

    st2 = sub.add_parser("list")
    st2.set_defaults(func=show_status)

    t = sub.add_parser("self-test")
    t.set_defaults(func=self_test)
    return p

def main():
    args = build_parser().parse_args()
    args.func(args)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import re

DIAGNOSTIC_SCHEMA_VERSION = "1.0.0"

def _result(diagnostic, first_check, confidence="high"):
    return {
        "diagnostic": diagnostic,
        "first_check": first_check,
        "confidence": confidence,
    }

def diagnose_health(health):
    reasons = list(health.get("halt_reasons") or [])
    docker_ok = bool(health.get("docker_active"))
    n8n_ok = bool(health.get("n8n_healthy"))
    permanent = int(health.get("failed_permanent", 0) or 0)

    if not docker_ok:
        return _result(
            "The Docker service is unavailable, so containerized Second Brain services cannot run.",
            "Run: systemctl status docker --no-pager",
        )
    if docker_ok and not n8n_ok:
        return _result(
            "Docker is running but the n8n health endpoint is not responding; the likely fault is the n8n container, application startup, or its local health endpoint.",
            "Run: docker ps --filter name=n8n",
        )
    if permanent > 0:
        return _result(
            f"The canonical processing state contains {permanent} permanent failure(s); automatic retries have ended or the item requires intervention.",
            "Run: sb-observe errors 20",
        )
    if reasons:
        return _result(
            "A halting condition was detected but it does not match a more specific diagnostic rule.",
            "Run: sb-observe status",
            "medium",
        )
    return _result(
        "No halting condition is currently present.",
        "Run: sb-observe status",
    )

def diagnose_event(event):
    event_type = str(event.get("event_type") or "").lower()
    component = str(event.get("component") or "").lower()
    message = str(event.get("message") or "").lower()
    workflow = str(event.get("workflow_name") or "").lower()
    text = " ".join([event_type, component, message, workflow])

    if "systemd_unit_failed" in event_type:
        unit_match = re.search(r"([a-zA-Z0-9_.@-]+\.service)", str(event.get("message") or ""))
        unit = unit_match.group(1) if unit_match else "<unit>"
        return _result(
            "A host service entered the failed state.",
            f"Run: systemctl status {unit} --no-pager",
        )
    if "backup" in text or re.search(r"\brestic\b", text):
        return _result(
            "A backup or restore-validation operation failed. Treat repository validation and retention as safety-sensitive.",
            "Check the latest backup evidence and run the backup preflight before retrying; do not manually prune snapshots.",
        )
    if any(x in text for x in ["voice", "transcription", "curator", "audio"]):
        return _result(
            "The voice-note path failed or stalled. The common fault domains are queue state, transcription services, or a downstream filing/review stage.",
            "Check Processing Status, then the owning voice workflow execution and local transcription service health.",
        )
    if any(x in text for x in ["document", "camera", "photo", "ocr", "vision"]):
        return _result(
            "The document/photo path failed or stalled. The common fault domains are capture state, OCR/vision processing, or review/finalization.",
            "Check Processing Status, then the owning document/photo workflow execution and its capture/state files.",
        )
    if "auditor" in text:
        return _result(
            "The Second Brain Auditor failed to complete an advisory run.",
            "Check the latest Auditor evidence and execution, then verify required local services are available.",
        )
    if "n8n_execution_crashed" in event_type or "crashed" in text:
        return _result(
            "An n8n execution crashed rather than returning a normal workflow error.",
            "Open the execution in n8n and inspect the failed node; if multiple workflows crash together, check n8n/container health first.",
        )
    if "n8n_execution_error" in event_type or "execution error" in text or "failed" in text or "error" in text:
        return _result(
            "A workflow or operational step returned an error. The event metadata alone does not prove the root cause.",
            "Open the matching n8n execution or source log and inspect the first failing node/event before retrying.",
            "medium",
        )
    if "stale" in text or "backlog" in text:
        return _result(
            "Work remained pending longer than expected; this usually indicates a schedule, dependency, or queue-transition problem rather than active processing.",
            "Check the owning workflow schedule and the canonical Processing Status backlog; do not delete queue/state files manually.",
            "medium",
        )
    return _result(
        "The event is abnormal but does not match a specific deterministic diagnostic rule.",
        "Review the event source and the immediately preceding events in Central Observability before changing state.",
        "low",
    )

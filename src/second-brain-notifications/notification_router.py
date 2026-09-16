#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, "/AI/Scripts/Second Brain Diagnostics")
from diagnostics import diagnose_health

SCHEMA_VERSION = "1.2.0"
WORKER_VERSION = "1.2.0"

CONFIG = Path("/AI/Config/Second Brain/Notifications/notification-config.json")
STATE = Path("/AI/State/Second Brain/Notifications/router-state.json")
LOG = Path("/AI/Logs/Second Brain Notifications/notification-decisions.jsonl")
PROCESSING = Path("/AI/State/Second Brain/Processing Status/current.json")
FAILURE_CONTROL = Path("/AI/State/Second Brain/Failure Control/current.json")

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {} if default is None else default

def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)

def append_log(obj):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def service_active(name):
    p = subprocess.run(
        ["systemctl", "is-active", name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return p.returncode == 0 and p.stdout.strip() == "active"

def n8n_healthy(url):
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=4) as r:
            return 200 <= int(r.status) < 300
    except Exception:
        return False

def current_health(cfg):
    processing = load_json(PROCESSING, {})
    lifecycle = processing.get("lifecycle", {}) or {}
    permanent_total = int(lifecycle.get("failed_permanent", 0) or 0)
    failure_control = load_json(FAILURE_CONTROL, {})
    failure_summary = failure_control.get("summary", {}) if isinstance(failure_control, dict) else {}
    halting_permanent = int(failure_summary.get("halting_permanent", 0) or 0)

    n8n_ok = n8n_healthy(cfg["n8n_health_url"])
    docker_ok = service_active("docker.service")

    halt_reasons = []
    if not docker_ok:
        halt_reasons.append("Docker service is unavailable")
    if not n8n_ok:
        halt_reasons.append("n8n is unavailable")
    if halting_permanent > 0:
        halt_reasons.append(f"{halting_permanent} halting permanent failure(s)")

    return {
        "checked_at": now_iso(),
        "halted": bool(halt_reasons),
        "halt_reasons": halt_reasons,
        "n8n_healthy": n8n_ok,
        "docker_active": docker_ok,
        "failed_permanent": halting_permanent,
        "failed_permanent_total": permanent_total,
        "isolated_permanent": max(0, permanent_total - halting_permanent),
        "processing_status": processing.get("overall_status", "unknown"),
    }

def send_ntfy(cfg, title, body, priority, tags):
    data = body.encode("utf-8")
    req = urllib.request.Request(
        cfg["ntfy_url"],
        data=data,
        method="POST",
        headers={
            "Title": title,
            "Priority": priority,
            "Tags": tags,
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return int(r.status)

def self_test(cfg):
    assert cfg["schema_version"] == "1.0.0"
    assert cfg["critical_confirmation_checks"] >= 2
    assert cfg["ntfy_url"].startswith("https://")
    assert cfg["n8n_health_url"].startswith("http://127.0.0.1:")
    sample = current_health(cfg)
    print(json.dumps({
        "status": "completed",
        "mode": "self_test",
        "policy": {
            "immediate": [
                "n8n unavailable for consecutive checks",
                "Docker unavailable for consecutive checks",
                "halting permanent failure count greater than zero for consecutive checks",
            ],
            "non_halting": "central Observability plus the daily health review; no immediate workflow-error push",
            "raw_exception_text_on_phone": False,
            "suggested_diagnostic_on_immediate_alert": True,
        },
        "diagnostic_self_tests": {
            "docker_down": diagnose_health({"docker_active": False, "n8n_healthy": False, "failed_permanent": 0, "halt_reasons": ["Docker service is unavailable"]}),
            "n8n_down": diagnose_health({"docker_active": True, "n8n_healthy": False, "failed_permanent": 0, "halt_reasons": ["n8n is unavailable"]}),
            "permanent_failure": diagnose_health({"docker_active": True, "n8n_healthy": True, "failed_permanent": 2, "halt_reasons": ["2 halting permanent failure(s)"]}),
        },
        "current_health": sample,
    }))
    return 0

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    cfg = load_json(CONFIG, {})
    required = {"schema_version", "ntfy_url", "n8n_health_url", "critical_confirmation_checks"}
    if not required.issubset(cfg):
        raise RuntimeError("Notification configuration is missing required fields.")

    if args.self_test:
        return self_test(cfg)

    health = current_health(cfg)
    state = load_json(STATE, {
        "schema_version": SCHEMA_VERSION,
        "worker_version": WORKER_VERSION,
        "consecutive_halt_checks": 0,
        "alert_active": False,
        "last_alert_at": None,
        "last_recovery_at": None,
    })

    previous_active = bool(state.get("alert_active", False))
    consecutive = int(state.get("consecutive_halt_checks", 0) or 0)

    if health["halted"]:
        consecutive += 1
    else:
        consecutive = 0

    decision = "none"
    sent = False
    send_error = None
    status_code = None

    if args.initialize:
        decision = "initialized_without_notification"
    elif health["halted"] and consecutive >= int(cfg["critical_confirmation_checks"]) and not previous_active:
        decision = "send_critical"
        diag = diagnose_health(health)
        body = (
            "Second Brain automation is halted or has a permanent failure.\n\n"
            + "Reason(s): " + "; ".join(health["halt_reasons"])
            + "\n\nSuggested diagnostic: " + diag["diagnostic"]
            + "\nFirst check: " + diag["first_check"]
            + "\nConfidence: " + diag["confidence"]
            + "\n\nReview the local Observability dashboard for details. "
              "Raw exception text is intentionally not sent to the phone."
        )
        try:
            status_code = send_ntfy(
                cfg,
                "Second Brain — Immediate Attention",
                body,
                "urgent",
                "rotating_light,brain",
            )
            sent = True
            state["alert_active"] = True
            state["last_alert_at"] = now_iso()
        except Exception as exc:
            send_error = f"{type(exc).__name__}: {exc}"
    elif not health["halted"] and previous_active:
        decision = "send_recovery"
        try:
            status_code = send_ntfy(
                cfg,
                "Second Brain — Recovered",
                "The previously detected halting condition has cleared. Review Observability for the event trail.",
                "default",
                "white_check_mark,brain",
            )
            sent = True
            state["alert_active"] = False
            state["last_recovery_at"] = now_iso()
        except Exception as exc:
            send_error = f"{type(exc).__name__}: {exc}"
    elif health["halted"]:
        decision = "confirming_halt"
    else:
        decision = "healthy_no_notification"
        state["alert_active"] = False

    state.update({
        "schema_version": SCHEMA_VERSION,
        "worker_version": WORKER_VERSION,
        "updated_at": now_iso(),
        "consecutive_halt_checks": consecutive,
        "last_health": health,
        "last_decision": decision,
        "last_send_error": send_error,
    })
    atomic_json(STATE, state)

    append_log({
        "schema_version": SCHEMA_VERSION,
        "worker_version": WORKER_VERSION,
        "at": now_iso(),
        "decision": decision,
        "sent": sent,
        "status_code": status_code,
        "send_error": send_error,
        "health": health,
    })

    print(json.dumps({
        "status": "completed",
        "decision": decision,
        "sent": sent,
        "health": health,
        "state_path": str(STATE),
        "log_path": str(LOG),
    }))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

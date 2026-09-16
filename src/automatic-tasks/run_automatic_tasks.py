#!/usr/bin/env python3

from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys
import traceback


CONFIG = Path(
    "/AI/Config/Second Brain/Automatic Tasks/runner.json"
)

RUNNER_VERSION = "automatic-task-runner-1.3.0"


def stamp():
    return datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")


def allocate_run_dir(root):
    root = Path(root)
    base_run_id = stamp()

    for collision_index in range(100):
        run_id = (
            base_run_id
            if collision_index == 0
            else (
                f"{base_run_id}-"
                f"{collision_index:02d}"
            )
        )

        run_dir = root / run_id

        try:
            run_dir.mkdir(
                parents=True,
                exist_ok=False,
            )

            return run_id, run_dir

        except FileExistsError:
            continue

    raise RuntimeError(
        "Unable to allocate a unique "
        "automatic-task run directory "
        "after 100 attempts."
    )


def sha256_file(path):
    h = hashlib.sha256()

    with Path(path).open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def write_json(path, payload):
    path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )


config = json.loads(
    CONFIG.read_text(
        encoding="utf-8"
    )
)

run_id, run_dir = allocate_run_dir(
    config["run_log_root"]
)

gate_output = (
    run_dir / "gate.json"
)

gate_stderr = (
    run_dir / "gate.stderr"
)

writer_input = (
    run_dir / "writer-input.json"
)

writer_stderr = (
    run_dir / "writer.stderr"
)

result_file = (
    run_dir / "result.json"
)

error_file = (
    run_dir / "error.json"
)


def fail(stage, message, *, detail="", exit_code=1):
    error = {
        "schema_version": "1.0.0",
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "result": "failed",
        "stage": stage,
        "message": message,
        "detail": detail,
        "run_dir": str(run_dir),
    }

    write_json(
        error_file,
        error,
    )

    write_json(
        result_file,
        error,
    )

    print(
        json.dumps(
            error,
            indent=2,
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )

    raise SystemExit(
        exit_code
    )


try:
    gate = subprocess.run(
        [config["production_gate"]],
        text=True,
        capture_output=True,
    )
except Exception as exc:
    detail = "".join(
        traceback.format_exception(
            type(exc),
            exc,
            exc.__traceback__,
        )
    )

    gate_stderr.write_text(
        detail,
        encoding="utf-8",
    )

    fail(
        "gate_spawn",
        "Production gate could not be started.",
        detail=str(exc),
    )


gate_stderr.write_text(
    gate.stderr or "",
    encoding="utf-8",
)

if gate.returncode != 0:
    fail(
        "gate_execution",
        "Production gate returned a nonzero exit code.",
        detail=(
            f"returncode={gate.returncode}"
        ),
    )


gate_output.write_text(
    gate.stdout,
    encoding="utf-8",
)


try:
    data = json.loads(
        gate.stdout
    )
except Exception as exc:
    fail(
        "gate_json",
        "Production gate returned invalid JSON.",
        detail=str(exc),
    )


eligible = [
    item
    for item in data["candidates"]
    if item.get("disposition")
    == "auto_eligible"
]


attestation = dict(
    data.get(
        "attestation",
        {}
    )
)

attestation["writer_sha256"] = (
    sha256_file(
        config["writer"]
    )
)

payload = {
    "schema_version": "1.0.0",
    "policy_version":
        data["policy_version"],
    "mode":
        "automatic-task-runner",
    "attestation":
        attestation,
    "candidates":
        eligible,
}


write_json(
    writer_input,
    payload,
)


if not config["write_enabled"]:
    result = {
        "schema_version": "1.0.0",
        "runner_version":
            RUNNER_VERSION,
        "configured_runner_version":
            config["runner_version"],
        "run_id":
            run_id,
        "result":
            "dry_run",
        "write_enabled":
            False,
        "auto_eligible_count":
            len(eligible),
        "attestation":
            attestation,
        "gate_output":
            str(gate_output),
        "writer_input":
            str(writer_input),
    }

    write_json(
        result_file,
        result,
    )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )

    raise SystemExit(0)


try:
    writer = subprocess.run(
        [
            config["writer"],
            "--input",
            str(writer_input),
            "--vault-root",
            config["vault_root"],
            "--queue-relative",
            config["queue_relative"],
            "--state",
            config["writer_state"],
            "--backup-root",
            config["writer_backup_root"],
            "--lock",
            config["writer_lock"],
        ],
        text=True,
        capture_output=True,
    )
except Exception as exc:
    detail = "".join(
        traceback.format_exception(
            type(exc),
            exc,
            exc.__traceback__,
        )
    )

    writer_stderr.write_text(
        detail,
        encoding="utf-8",
    )

    fail(
        "writer_spawn",
        "Automatic task writer could not be started.",
        detail=str(exc),
    )


writer_stderr.write_text(
    writer.stderr or "",
    encoding="utf-8",
)


if writer.returncode != 0:
    fail(
        "writer_execution",
        "Automatic task writer returned a nonzero exit code.",
        detail=(
            f"returncode={writer.returncode}"
        ),
    )


try:
    writer_result = json.loads(
        writer.stdout
    )
except Exception as exc:
    fail(
        "writer_json",
        "Automatic task writer returned invalid JSON.",
        detail=str(exc),
    )


result = {
    "schema_version": "1.0.0",
    "runner_version":
        RUNNER_VERSION,
    "configured_runner_version":
        config["runner_version"],
    "run_id":
        run_id,
    "result":
        "writer_complete",
    "write_enabled":
        True,
    "auto_eligible_count":
        len(eligible),
    "writer":
        writer_result,
}


write_json(
    result_file,
    result,
)


print(
    json.dumps(
        result,
        indent=2,
        ensure_ascii=False,
    )
)

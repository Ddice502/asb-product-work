#!/usr/bin/env python3

from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata


WRITER_VERSION = "automatic-task-writer-1.3.0"

POLICY_PATH = Path(
    "/AI/Config/Second Brain/"
    "Automatic Tasks/policy.json"
)

BASE_GATE_PATH = Path(
    "/AI/Scripts/Automatic Tasks/"
    "automatic_task_gate.py"
)

PRODUCTION_GATE_PATH = Path(
    "/AI/Scripts/Automatic Tasks/"
    "automatic_task_production_gate.py"
)


def now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


def run_stamp():
    return datetime.now(
        timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")


def normalize(value):
    value = unicodedata.normalize(
        "NFKD",
        str(value or ""),
    ).lower()

    value = re.sub(
        r"<!--.*?-->",
        " ",
        value,
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def stable_task_id(task):
    return hashlib.sha256(
        normalize(task).encode("utf-8")
    ).hexdigest()[:16]


def sha256_file(path):
    if not path.exists():
        return ""

    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def canonical_attestation():
    policy = json.loads(
        POLICY_PATH.read_text(
            encoding="utf-8"
        )
    )

    ledger_path = Path(
        policy[
            "historical_task_observation_ledger"
        ]
    )

    return {
        "policy_sha256":
            sha256_file(POLICY_PATH),
        "base_gate_sha256":
            sha256_file(BASE_GATE_PATH),
        "production_gate_sha256":
            sha256_file(
                PRODUCTION_GATE_PATH
            ),
        "historical_observation_ledger_sha256":
            sha256_file(ledger_path),
        "writer_sha256":
            sha256_file(
                Path(__file__).resolve()
            ),
    }


def fresh_gate():
    raw = subprocess.check_output(
        [str(PRODUCTION_GATE_PATH)],
        text=True,
    )

    data = json.loads(raw)

    if not isinstance(
        data.get("candidates"),
        list,
    ):
        raise RuntimeError(
            "Fresh production gate candidates "
            "are invalid."
        )

    return data


def safe_relative(value):
    text = str(value or "").replace(
        "\\",
        "/",
    ).lstrip("/").strip()

    p = Path(text)

    if (
        not text
        or p.is_absolute()
        or ".." in p.parts
    ):
        raise RuntimeError(
            f"Unsafe relative path: {value}"
        )

    return Path(*p.parts)


def atomic_write(path, text):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_name(
        path.name
        + f".tmp-{os.getpid()}-{DateStamp.value()}"
    )

    temporary.write_text(
        text,
        encoding="utf-8",
    )

    os.replace(
        temporary,
        path,
    )


class DateStamp:
    @staticmethod
    def value():
        return datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%d%H%M%S%f"
        )


def load_state(path):
    if not path.exists():
        return {
            "schema_version": "1.0.0",
            "writer_version": WRITER_VERSION,
            "tasks": {},
            "runs": [],
        }

    data = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    if (
        data.get("schema_version")
        != "1.0.0"
    ):
        raise RuntimeError(
            "Unsupported writer state schema."
        )

    data.setdefault(
        "tasks",
        {},
    )

    data.setdefault(
        "runs",
        [],
    )

    data["writer_version"] = (
        WRITER_VERSION
    )

    return data


def queue_normalized_tasks(text):
    result = set()

    for line in text.splitlines():
        match = re.match(
            r"^-\s*\[[ xX]\]\s+(.+?)\s*$",
            line,
        )

        if not match:
            continue

        task = re.sub(
            r"\s*<!--.*?-->\s*$",
            "",
            match.group(1),
        ).strip()

        if task:
            result.add(
                normalize(task)
            )

    return result


def acquire_lock(lock_path):
    lock_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        fd = os.open(
            lock_path,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY,
            0o644,
        )

        payload = {
            "pid": os.getpid(),
            "started_at": now_iso(),
        }

        os.write(
            fd,
            (
                json.dumps(payload)
                + "\n"
            ).encode("utf-8"),
        )

        os.close(fd)

        return True

    except FileExistsError:

        age = (
            datetime.now(
                timezone.utc
            ).timestamp()
            - lock_path.stat().st_mtime
        )

        if age < 15 * 60:
            return False

        stale = lock_path.with_name(
            lock_path.name
            + ".stale-"
            + run_stamp()
        )

        os.replace(
            lock_path,
            stale,
        )

        return acquire_lock(
            lock_path
        )


def release_lock(lock_path):
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


parser = argparse.ArgumentParser()

parser.add_argument(
    "--input",
    required=True,
)

parser.add_argument(
    "--vault-root",
    required=True,
)

parser.add_argument(
    "--queue-relative",
    required=True,
)

parser.add_argument(
    "--state",
    required=True,
)

parser.add_argument(
    "--backup-root",
    required=True,
)

parser.add_argument(
    "--lock",
    required=True,
)

parser.add_argument(
    "--inject-failure-after-queue",
    action="store_true",
)

args = parser.parse_args()

input_path = Path(
    args.input
).resolve()

vault_root = Path(
    args.vault_root
).resolve()

queue_relative = safe_relative(
    args.queue_relative
)

queue_path = (
    vault_root
    / queue_relative
).resolve()

state_path = Path(
    args.state
).resolve()

backup_root = Path(
    args.backup_root
).resolve()

lock_path = Path(
    args.lock
).resolve()

vault_prefix = (
    str(vault_root)
    + os.sep
)

if not str(queue_path).startswith(
    vault_prefix
):
    raise RuntimeError(
        "Queue path escapes vault."
    )

if not acquire_lock(
    lock_path
):
    print(
        json.dumps(
            {
                "result": "locked",
                "writer_version":
                    WRITER_VERSION,
                "reason":
                    "Another automatic task "
                    "writer is running.",
            },
            indent=2,
        )
    )

    raise SystemExit(0)


try:
    payload = json.loads(
        input_path.read_text(
            encoding="utf-8"
        )
    )

    policy_version = str(
        payload.get(
            "policy_version",
            "",
        )
    ).strip()

    if not policy_version:
        raise RuntimeError(
            "Input policy_version is missing."
        )

    if (
        payload.get("mode")
        != "automatic-task-runner"
    ):
        raise RuntimeError(
            "Writer input mode is not "
            "automatic-task-runner."
        )

    supplied_attestation = payload.get(
        "attestation"
    )

    if not isinstance(
        supplied_attestation,
        dict,
    ):
        raise RuntimeError(
            "Writer attestation is missing."
        )

    expected_attestation = (
        canonical_attestation()
    )

    if supplied_attestation != expected_attestation:
        raise RuntimeError(
            "Writer attestation validation failed."
        )

    current_gate = fresh_gate()

    current_gate_attestation = dict(
        current_gate.get(
            "attestation",
            {}
        )
    )

    expected_gate_attestation = {
        key: value
        for key, value
        in expected_attestation.items()
        if key != "writer_sha256"
    }

    if (
        current_gate_attestation
        != expected_gate_attestation
    ):
        raise RuntimeError(
            "Fresh production gate attestation "
            "does not match canonical artifacts."
        )

    current_auto = {
        str(item.get("task_id", "")):
            item
        for item
        in current_gate["candidates"]
        if (
            item.get("disposition")
            == "auto_eligible"
            and item.get("task_id")
        )
    }

    candidates = payload.get(
        "candidates",
    )

    if not isinstance(
        candidates,
        list,
    ):
        raise RuntimeError(
            "Input candidates must be a list."
        )

    if not queue_path.exists():
        raise RuntimeError(
            f"Queue must already exist: "
            f"{queue_path}"
        )

    queue_before = (
        queue_path.read_text(
            encoding="utf-8"
        )
    )

    queue_before_sha = (
        sha256_file(
            queue_path
        )
    )

    state_existed_before = (
        state_path.exists()
    )

    state_before = (
        state_path.read_text(
            encoding="utf-8"
        )
        if state_existed_before
        else ""
    )

    state = load_state(
        state_path
    )

    existing_normalized = (
        queue_normalized_tasks(
            queue_before
        )
    )

    accepted = []
    skipped = []

    for item in candidates:

        if (
            item.get("disposition")
            != "auto_eligible"
        ):
            raise RuntimeError(
                "Writer received a candidate "
                "that is not auto_eligible."
            )

        claimed_id = str(
            item.get("task_id", "")
        ).strip().lower()

        fresh_item = current_auto.get(
            claimed_id
        )

        if fresh_item is None:
            raise RuntimeError(
                "Submitted task is not currently "
                "auto_eligible in the canonical "
                "production gate."
            )

        if item != fresh_item:
            raise RuntimeError(
                "Submitted task does not exactly "
                "match the current production-gate "
                "candidate."
            )

        task = str(
            item.get(
                "task",
                "",
            )
        ).strip()

        supplied_id = str(
            item.get(
                "task_id",
                "",
            )
        ).strip().lower()

        expected_id = (
            stable_task_id(
                task
            )
        )

        if (
            not task
            or supplied_id
            != expected_id
        ):
            raise RuntimeError(
                "Task ID validation "
                f"failed: {task}"
            )

        if (
            item.get(
                "requires_review"
            )
            is not False
        ):
            raise RuntimeError(
                "Auto task still requires "
                f"review: {task}"
            )

        confidence = item.get(
            "confidence"
        )

        if not isinstance(
            confidence,
            (int, float),
        ):
            raise RuntimeError(
                "Task confidence missing: "
                f"{task}"
            )

        source_note = (
            safe_relative(
                item.get(
                    "source_note",
                    "",
                )
            )
        )

        source_path = (
            vault_root
            / source_note
        ).resolve()

        if not str(
            source_path
        ).startswith(
            vault_prefix
        ):
            raise RuntimeError(
                "Source path escapes vault."
            )

        if not source_path.is_file():
            raise RuntimeError(
                "Source note not found: "
                f"{source_note}"
            )

        excerpt = str(
            item.get(
                "source_excerpt",
                "",
            )
        ).strip()

        if not excerpt:
            raise RuntimeError(
                "Source evidence missing: "
                f"{task}"
            )

        marker = (
            "<!-- task-id:"
            f"{expected_id} -->"
        )

        if (
            marker in queue_before
            or normalize(task)
            in existing_normalized
            or expected_id
            in state["tasks"]
        ):
            skipped.append(
                {
                    "task_id":
                        expected_id,
                    "task":
                        task,
                    "reason":
                        "task already exists "
                        "in queue or writer "
                        "ledger",
                }
            )

            continue

        accepted.append(
            {
                **item,
                "task_id":
                    expected_id,
                "source_note":
                    source_note.as_posix(),
            }
        )

    if not accepted:

        result = {
            "result":
                "no_new_tasks",
            "writer_version":
                WRITER_VERSION,
            "policy_version":
                policy_version,
            "attestation":
                expected_attestation,
            "appended_count": 0,
            "skipped_count":
                len(skipped),
            "skipped":
                skipped,
            "queue_sha256_before":
                queue_before_sha,
            "queue_sha256_after":
                queue_before_sha,
            "backups": [],
        }

        print(
            json.dumps(
                result,
                indent=2,
            )
        )

        raise SystemExit(0)

    stamp = run_stamp()

    backup_directory = (
        backup_root
        / stamp
    )

    backup_queue = (
        backup_directory
        / queue_relative
    )

    backup_queue.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    shutil.copy2(
        queue_path,
        backup_queue,
    )

    backup_state_path = (
        backup_directory
        / "writer-state.json"
    )

    backup_state = ""

    if state_existed_before:
        backup_state_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copy2(
            state_path,
            backup_state_path,
        )

        backup_state = str(
            backup_state_path
        )

    transaction_manifest = (
        backup_directory
        / "transaction.json"
    )

    manifest = {
        "schema_version":
            "1.0.0",
        "writer_version":
            WRITER_VERSION,
        "run_id":
            stamp,
        "queue_path":
            str(queue_path),
        "queue_backup":
            str(backup_queue),
        "state_path":
            str(state_path),
        "state_existed_before":
            state_existed_before,
        "state_backup":
            backup_state,
        "status":
            "prepared",
    }

    atomic_write(
        transaction_manifest,
        json.dumps(
            manifest,
            indent=2,
        ) + "\n",
    )

    written_at = now_iso()

    lines = [
        "",
        (
            "### Automatic import "
            f"{written_at}"
        ),
        "",
    ]

    for item in accepted:

        lines.extend(
            [
                (
                    f"- [ ] {item['task']} "
                    "<!-- task-id:"
                    f"{item['task_id']} -->"
                ),
                (
                    "  - Automatically created "
                    "through: "
                    f"{policy_version}"
                ),
                (
                    "  - Source: "
                    f"[[{item['source_note']}]]"
                ),
                (
                    "  - Confidence: "
                    f"{item['confidence']}"
                ),
                (
                    "  - Evidence: "
                    f"{item['source_excerpt']}"
                ),
            ]
        )

    lines.append("")

    queue_after = (
        queue_before.rstrip()
        + "\n"
        + "\n".join(lines)
    )

    queue_after_sha_expected = hashlib.sha256(
        queue_after.encode("utf-8")
    ).hexdigest()

    for item in accepted:
        state["tasks"][
            item["task_id"]
        ] = {
            "task":
                item["task"],
            "normalized":
                normalize(
                    item["task"]
                ),
            "source_note":
                item["source_note"],
            "confidence":
                item["confidence"],
            "policy_version":
                policy_version,
            "policy_sha256":
                expected_attestation[
                    "policy_sha256"
                ],
            "production_gate_sha256":
                expected_attestation[
                    "production_gate_sha256"
                ],
            "writer_sha256":
                expected_attestation[
                    "writer_sha256"
                ],
            "task_fingerprint":
                item.get(
                    "task_fingerprint"
                ),
            "source_note_sha256":
                item.get(
                    "source_note_sha256"
                ),
            "first_seen_at":
                written_at,
            "attempt_count":
                1,
            "execution_status":
                "appended",
            "appended_queue_task_id":
                item["task_id"],
            "queue_sha256_before":
                queue_before_sha,
            "queue_sha256_after":
                queue_after_sha_expected,
            "written_at":
                written_at,
        }

    run_record = {
        "run_id":
            stamp,
        "written_at":
            written_at,
        "policy_version":
            policy_version,
        "attestation":
            expected_attestation,
        "queue_sha256_before":
            queue_before_sha,
        "queue_sha256_after":
            queue_after_sha_expected,
        "execution_status":
            "committed",
        "appended_task_ids":
            [
                item["task_id"]
                for item in accepted
            ],
        "skipped_task_ids":
            [
                item["task_id"]
                for item in skipped
            ],
        "queue_backup":
            str(backup_queue),
        "state_backup":
            backup_state,
        "state_existed_before":
            state_existed_before,
        "transaction_manifest":
            str(
                transaction_manifest
            ),
    }

    state["runs"].append(
        run_record
    )

    try:
        atomic_write(
            queue_path,
            queue_after,
        )

        if (
            args.inject_failure_after_queue
        ):
            raise RuntimeError(
                "Injected acceptance-test "
                "failure after queue write."
            )

        atomic_write(
            state_path,
            json.dumps(
                state,
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
        )

    except Exception:

        shutil.copy2(
            backup_queue,
            queue_path,
        )

        if state_existed_before:
            atomic_write(
                state_path,
                state_before,
            )
        else:
            try:
                state_path.unlink()
            except FileNotFoundError:
                pass

        manifest[
            "status"
        ] = "rolled_back"

        atomic_write(
            transaction_manifest,
            json.dumps(
                manifest,
                indent=2,
            ) + "\n",
        )

        raise

    manifest[
        "status"
    ] = "committed"

    atomic_write(
        transaction_manifest,
        json.dumps(
            manifest,
            indent=2,
        ) + "\n",
    )

    result = {
        "result":
            "complete",
        "writer_version":
            WRITER_VERSION,
        "policy_version":
            policy_version,
        "attestation":
            expected_attestation,
        "run_id":
            stamp,
        "appended_count":
            len(accepted),
        "skipped_count":
            len(skipped),
        "appended":
            [
                {
                    "task_id":
                        item[
                            "task_id"
                        ],
                    "task":
                        item["task"],
                    "source_note":
                        item[
                            "source_note"
                        ],
                }
                for item in accepted
            ],
        "skipped":
            skipped,
        "queue_sha256_before":
            queue_before_sha,
        "queue_sha256_after":
            sha256_file(
                queue_path
            ),
        "state_file":
            str(state_path),
        "transaction_manifest":
            str(
                transaction_manifest
            ),
        "backups":
            [
                str(
                    backup_queue
                ),
                *(
                    [backup_state]
                    if backup_state
                    else []
                ),
            ],
    }

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )

finally:
    release_lock(
        lock_path
    )

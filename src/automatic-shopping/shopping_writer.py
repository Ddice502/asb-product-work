#!/usr/bin/env python3

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from shopping_common import (
    enrich_metadata,
)

from shopping_gate import (
    evaluate,
    paths,
)

from shopping_io import (
    append_history,
    append_reviews,
    atomic_write_json,
    atomic_write_text,
    load_json,
    parse_active_items,
    render_active,
    replace_section,
    resolve_policy_path,
)


WRITER_VERSION = (
    "automatic-shopping-writer-1.0.0"
)


def now():
    return datetime.now(
        timezone.utc
    ).isoformat()


def default_state():
    return {
        "schema_version": "1.0.0",
        "writer_version":
            WRITER_VERSION,
        "observations": {},
        "items": {},
        "reviews": {},
    }


def observation(
    candidate,
    status,
):
    return {
        "candidate_id":
            candidate["candidate_id"],

        "shopping_id":
            candidate["shopping_id"],

        "item_key":
            candidate["item_key"],

        "source_note":
            candidate["source_note"],

        "source_note_sha256":
            candidate[
                "source_note_sha256"
            ],

        "source_excerpt":
            candidate[
                "source_excerpt"
            ],

        "fingerprint":
            candidate["fingerprint"],

        "status":
            status,

        "observed_at":
            now(),
    }


def execute():
    (
        policy_path,
        vault,
        policy,
        baseline_path,
        active_path,
        state_path,
    ) = paths()

    if policy.get(
        "write_enabled"
    ) is not True:
        return {
            "writer_version":
                WRITER_VERSION,

            "result":
                "writes_disabled",

            "write_enabled":
                False,
        }

    archive_path = resolve_policy_path(
        policy["purchased_archive"],
        vault,
    )

    review_path = resolve_policy_path(
        policy["review_queue"],
        vault,
    )

    lock_path = Path(
        os.environ.get(
            "SHOP_LOCK_PATH",
            policy["lock_path"],
        )
    )

    lock_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd = None

    try:
        fd = os.open(
            lock_path,
            os.O_CREAT
            | os.O_EXCL
            | os.O_WRONLY,
            0o644,
        )

        os.write(
            fd,
            (
                f"pid={os.getpid()}\n"
                f"started={now()}\n"
            ).encode()
        )

        os.close(fd)
        fd = None

    except FileExistsError:
        raise RuntimeError(
            "Shopping writer lock exists."
        )

    targets = [
        active_path,
        archive_path,
        review_path,
        state_path,
    ]

    before = {}

    try:
        for target in targets:
            target = Path(target)

            before[str(target)] = {
                "exists":
                    target.is_file(),

                "bytes":
                    (
                        target.read_bytes()
                        if target.is_file()
                        else None
                    ),
            }

        gate = evaluate()

        active_text = active_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        archive_text = archive_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        review_text = review_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        state = load_json(
            state_path,
            default=default_state(),
        )

        state.setdefault(
            "observations",
            {},
        )

        state.setdefault(
            "items",
            {},
        )

        state.setdefault(
            "reviews",
            {},
        )

        records = parse_active_items(
            active_text
        )

        checked = [
            record
            for record in records
            if record.get("checked")
        ]

        active = [
            record
            for record in records
            if not record.get("checked")
        ]

        by_key = {
            record["item_key"]:
                record
            for record in active
        }

        appended = []
        merged = []
        reviewed = []
        archived = []
        observed_only = []

        state_changed = False

        for record in checked:
            purchased = dict(record)

            purchased[
                "purchased_at"
            ] = now()

            archived.append(
                purchased
            )

            state_changed = True

        for candidate in gate[
            "candidates"
        ]:
            disposition = (
                candidate[
                    "disposition"
                ]
            )

            cid = candidate[
                "candidate_id"
            ]

            if disposition in {
                "baseline_existing",
                "observed_existing",
            }:
                continue

            if cid in state[
                "observations"
            ]:
                continue

            if disposition == "review":
                reviewed.append(
                    candidate
                )

                state["reviews"][cid] = {
                    "status": "pending",

                    "reasons":
                        candidate[
                            "reasons"
                        ],

                    "created_at":
                        now(),
                }

                state[
                    "observations"
                ][cid] = observation(
                    candidate,
                    "review",
                )

                state_changed = True
                continue

            if disposition == (
                "duplicate_existing"
            ):
                state[
                    "observations"
                ][cid] = observation(
                    candidate,
                    "duplicate_existing",
                )

                observed_only.append(
                    candidate
                )

                state_changed = True
                continue

            if disposition == (
                "auto_eligible"
            ):
                record = {
                    "shopping_id":
                        candidate[
                            "shopping_id"
                        ],

                    "checked":
                        False,

                    "item":
                        candidate["item"],

                    "item_key":
                        candidate[
                            "item_key"
                        ],

                    "quantity":
                        candidate[
                            "quantity"
                        ],

                    "store":
                        candidate[
                            "store"
                        ],

                    "category":
                        candidate[
                            "category"
                        ],

                    "source_excerpt":
                        candidate[
                            "source_excerpt"
                        ],

                    "fingerprint":
                        candidate[
                            "fingerprint"
                        ],
                }

                active.append(record)

                by_key[
                    record["item_key"]
                ] = record

                appended.append(
                    candidate
                )

                state[
                    "observations"
                ][cid] = observation(
                    candidate,
                    "appended",
                )

                state["items"][
                    record[
                        "shopping_id"
                    ]
                ] = {
                    "status": "active",

                    "item_key":
                        record[
                            "item_key"
                        ],

                    "first_seen_at":
                        now(),

                    "last_fingerprint":
                        candidate[
                            "fingerprint"
                        ],
                }

                state_changed = True
                continue

            if disposition == (
                "merge_eligible"
            ):
                record = by_key.get(
                    candidate[
                        "item_key"
                    ]
                )

                if record is None:
                    raise RuntimeError(
                        "Merge target absent."
                    )

                new_record, conflicts = (
                    enrich_metadata(
                        record,
                        candidate,
                    )
                )

                if conflicts:
                    raise RuntimeError(
                        "Writer merge conflict: "
                        + ",".join(conflicts)
                    )

                record.update(
                    new_record
                )

                merged.append(
                    candidate
                )

                state[
                    "observations"
                ][cid] = observation(
                    candidate,
                    "merged",
                )

                state_changed = True

        active_changed = bool(
            checked
            or appended
            or merged
        )

        if not (
            active_changed
            or reviewed
            or archived
            or state_changed
        ):
            return {
                "writer_version":
                    WRITER_VERSION,

                "result":
                    "no_changes",

                "appended_count": 0,
                "merged_count": 0,
                "reviewed_count": 0,
                "archived_count": 0,
            }

        if active_changed:
            active_text = (
                replace_section(
                    active_text,
                    "Active",
                    render_active(
                        active
                    ),
                )
            )

            atomic_write_text(
                active_path,
                active_text,
            )

        if (
            os.environ.get(
                "SHOPPING_INJECT_FAILURE"
            )
            == "after_list_write"
        ):
            raise RuntimeError(
                "Injected failure after "
                "shopping-list write."
            )

        if archived:
            archive_text = (
                append_history(
                    archive_text,
                    archived,
                )
            )

            atomic_write_text(
                archive_path,
                archive_text,
            )

        if reviewed:
            review_text = (
                append_reviews(
                    review_text,
                    reviewed,
                )
            )

            atomic_write_text(
                review_path,
                review_text,
            )

        if state_changed:
            state[
                "writer_version"
            ] = WRITER_VERSION

            state[
                "updated_at"
            ] = now()

            atomic_write_json(
                state_path,
                state,
            )

        return {
            "writer_version":
                WRITER_VERSION,

            "result":
                "complete",

            "appended_count":
                len(appended),

            "merged_count":
                len(merged),

            "reviewed_count":
                len(reviewed),

            "archived_count":
                len(archived),

            "observed_only_count":
                len(observed_only),
        }

    except Exception as exc:
        for raw_path, record in (
            before.items()
        ):
            path = Path(raw_path)

            if record["exists"]:
                path.write_bytes(
                    record["bytes"]
                )
            else:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass

        return {
            "writer_version":
                WRITER_VERSION,

            "result":
                "rolled_back",

            "error":
                str(exc),
        }

    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    result = execute()

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )

    if (
        result.get("result")
        == "rolled_back"
    ):
        raise SystemExit(1)

#!/usr/bin/env python3

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import traceback

from shopping_gate import (
    evaluate,
    paths,
)

from shopping_io import (
    atomic_write_json,
)

from shopping_writer import (
    execute as writer_execute,
)


RUNNER_VERSION = (
    "automatic-shopping-runner-1.0.0"
)


def stamp():
    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def allocate_run_dir(root):
    root = Path(root)

    base = stamp()

    for index in range(100):
        run_id = (
            base
            if index == 0
            else (
                f"{base}-"
                f"{index:02d}"
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
        "Unable to allocate unique "
        "shopping run directory."
    )


def main():
    (
        policy_path,
        vault,
        policy,
        baseline_path,
        active_path,
        state_path,
    ) = paths()

    run_root = Path(
        os.environ.get(
            "SHOP_RUN_ROOT",
            policy[
                "run_log_root"
            ],
        )
    )

    run_id, run_dir = (
        allocate_run_dir(
            run_root
        )
    )

    try:
        gate = evaluate()

        atomic_write_json(
            run_dir /
            "gate.json",
            gate,
        )

        if policy.get(
            "write_enabled"
        ) is not True:
            result = {
                "runner_version":
                    RUNNER_VERSION,

                "configured_runner_version":
                    policy.get(
                        "runner_version"
                    ),

                "result":
                    "dry_run",

                "write_enabled":
                    False,

                "candidate_count":
                    gate.get(
                        "candidate_count",
                        0,
                    ),

                "counts":
                    gate.get(
                        "counts",
                        {},
                    ),

                "active_checked_count":
                    gate.get(
                        "active_checked_count",
                        0,
                    ),

                "run_id":
                    run_id,
            }

        else:
            writer = writer_execute()

            if writer.get(
                "result"
            ) == "rolled_back":
                raise RuntimeError(
                    "Shopping writer "
                    "rolled back: "
                    + writer.get(
                        "error",
                        "unknown error",
                    )
                )

            result = {
                "runner_version":
                    RUNNER_VERSION,

                "configured_runner_version":
                    policy.get(
                        "runner_version"
                    ),

                "result":
                    "writer_complete",

                "write_enabled":
                    True,

                "candidate_count":
                    gate.get(
                        "candidate_count",
                        0,
                    ),

                "counts":
                    gate.get(
                        "counts",
                        {},
                    ),

                "writer":
                    writer,

                "run_id":
                    run_id,
            }

        atomic_write_json(
            run_dir /
            "result.json",
            result,
        )

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )

    except Exception as exc:
        atomic_write_json(
            run_dir /
            "error.json",
            {
                "runner_version":
                    RUNNER_VERSION,

                "result":
                    "failed",

                "run_id":
                    run_id,

                "error":
                    str(exc),

                "traceback":
                    traceback.format_exc(),
            },
        )

        raise


if __name__ == "__main__":
    main()

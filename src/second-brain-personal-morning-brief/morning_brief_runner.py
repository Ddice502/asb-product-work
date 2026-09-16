#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
from typing import Any
from datetime import (
    date as Date,
    datetime,
    time as Time,
    timedelta,
    timezone,
)
from zoneinfo import ZoneInfo
import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile


DEFAULT_CONFIG = Path(
    "/AI/Config/Second Brain/"
    "Personal Morning Brief/config.json"
)

DEFAULT_COLLECTOR = Path(
    "/AI/Scripts/Second Brain Personal Morning Brief/"
    "morning_brief.py"
)

DEFAULT_DELIVERY = Path(
    "/AI/Scripts/Second Brain Personal Morning Brief/"
    "morning_brief_delivery.py"
)


def load_json(
    path: Path,
    default: Any,
) -> Any:
    if not path.exists():
        return default

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def atomic_text(
    path: Path,
    text: str,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temporary = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(
            path.parent
        ),
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write(
                text
            )

            handle.flush()
            os.fsync(
                handle.fileno()
            )

        os.replace(
            temporary,
            path,
        )

    finally:
        if os.path.exists(
            temporary
        ):
            os.unlink(
                temporary
            )


def atomic_json(
    path: Path,
    value: Any,
) -> None:
    atomic_text(
        path,
        json.dumps(
            value,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
    )


def append_jsonl(
    path: Path,
    value: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            json.dumps(
                value,
                ensure_ascii=False,
            )
        )

        handle.write(
            "\n"
        )

        handle.flush()
        os.fsync(
            handle.fileno()
        )


def sha256_file(
    path: Path,
) -> str:
    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def load_module(
    path: Path,
    name: str,
):
    parent = str(
        path.parent
    )

    if parent not in sys.path:
        sys.path.insert(
            0,
            parent,
        )

    spec = importlib.util.spec_from_file_location(
        name,
        path,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "Could not import module: "
            + str(path)
        )

    module = (
        importlib.util.module_from_spec(
            spec
        )
    )

    spec.loader.exec_module(
        module
    )

    return module


def parse_now(
    timezone_name: str,
    override: str | None,
) -> datetime:
    zone = ZoneInfo(
        timezone_name
    )

    if override:
        parsed = datetime.fromisoformat(
            override
        )

        if (
            parsed.tzinfo
            is None
        ):
            raise RuntimeError(
                "--now must include a timezone offset."
            )

        return parsed.astimezone(
            zone
        )

    return datetime.now(
        timezone.utc
    ).astimezone(
        zone
    )


def runtime_paths(
    config: dict[str, Any],
    runtime_root: Path | None,
) -> dict[str, Path]:
    if runtime_root is not None:
        return {
            "report_dir":
                runtime_root
                / "reports",

            "snapshot_dir":
                runtime_root
                / "snapshots",

            "state":
                runtime_root
                / "runner-state.json",

            "log":
                runtime_root
                / "runner-decisions.jsonl",
        }

    vault = Path(
        config[
            "paths"
        ][
            "vault"
        ]
    )

    persistence = (
        config[
            "persistence"
        ]
    )

    return {
        "report_dir":
            (
                vault
                / persistence[
                    "vault_directory"
                ]
            ),

        "snapshot_dir":
            Path(
                persistence[
                    "structured_snapshot_directory"
                ]
            ),

        "state":
            Path(
                persistence[
                    "runner_state_path"
                ]
            ),

        "log":
            Path(
                persistence[
                    "runner_log_path"
                ]
            ),
    }


def empty_state(
    legacy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = {
        "schema_version":
            "2.0.0",

        "scheduled_completed":
            {},

        "expired_scheduled_dates":
            [],

        "pending_notifications":
            {},

        "abandoned_notifications":
            [],

        "last_run":
            None,
    }

    if legacy:
        state[
            "legacy_state"
        ] = legacy

    return state


def load_state(
    path: Path,
) -> dict[str, Any]:
    current = load_json(
        path,
        {},
    )

    if (
        isinstance(
            current,
            dict,
        )
        and current.get(
            "schema_version"
        )
        == "2.0.0"
    ):
        current.setdefault(
            "scheduled_completed",
            {},
        )

        current.setdefault(
            "expired_scheduled_dates",
            [],
        )

        current.setdefault(
            "pending_notifications",
            {},
        )

        current.setdefault(
            "abandoned_notifications",
            [],
        )

        return current

    return empty_state(
        current
        if isinstance(
            current,
            dict,
        )
        else None
    )


def source_link(
    source: Any,
) -> str:
    if not isinstance(
        source,
        dict,
    ):
        return "unavailable"

    return str(
        source.get(
            "obsidian_link"
        )
        or source.get(
            "path"
        )
        or "unavailable"
    )


def health_source_date(
    report: dict[str, Any],
) -> str | None:
    source = (
        report.get(
            "health",
            {}
        ).get(
            "general",
            {}
        ).get(
            "source"
        )
    )

    if not isinstance(
        source,
        dict,
    ):
        return None

    text = str(
        source.get(
            "path",
            "",
        )
    )

    match = re.search(
        r"\b(\d{4}-\d{2}-\d{2})\b",
        text,
    )

    if not match:
        return None

    return match.group(
        1
    )


def normalized_user_content(
    report: dict[str, Any],
) -> dict[str, Any]:
    def compact_source_date() -> str | None:
        return health_source_date(
            report
        )

    reviews = []

    for item in (
        report.get(
            "reviews"
        )
        or []
    ):
        if not isinstance(
            item,
            dict,
        ):
            continue

        reviews.append(
            {
                "id":
                    item.get(
                        "id"
                    ),

                "status":
                    item.get(
                        "status"
                    ),

                "open_count":
                    item.get(
                        "open_count"
                    ),
            }
        )

    external = (
        report.get(
            "external"
        )
        or {}
    )

    weather = (
        external.get(
            "weather"
        )
        or {}
    )

    events = (
        external.get(
            "events"
        )
        or {}
    )

    news = (
        external.get(
            "news"
        )
        or {}
    )

    reddit = (
        external.get(
            "reddit"
        )
        or {}
    )

    return {
        "tasks": {
            "status":
                report.get(
                    "tasks",
                    {}
                ).get(
                    "status"
                ),

            "open_count":
                report.get(
                    "tasks",
                    {}
                ).get(
                    "open_count"
                ),

            "items":
                report.get(
                    "tasks",
                    {}
                ).get(
                    "items",
                    []
                ),
        },

        "shopping": {
            "status":
                report.get(
                    "shopping",
                    {}
                ).get(
                    "status"
                ),

            "open_count":
                report.get(
                    "shopping",
                    {}
                ).get(
                    "open_count"
                ),

            "items":
                report.get(
                    "shopping",
                    {}
                ).get(
                    "items",
                    []
                ),
        },

        "health": {
            "status":
                report.get(
                    "health",
                    {}
                ).get(
                    "general",
                    {}
                ).get(
                    "status"
                ),

            "issue_count":
                report.get(
                    "health",
                    {}
                ).get(
                    "general",
                    {}
                ).get(
                    "issue_count"
                ),

            "source_date":
                compact_source_date(),
        },

        "reviews":
            reviews,

        "weather": {
            "status":
                weather.get(
                    "status"
                ),

            "periods": [
                {
                    "name":
                        item.get(
                            "name"
                        ),

                    "temperature":
                        item.get(
                            "temperature"
                        ),

                    "temperature_unit":
                        item.get(
                            "temperature_unit"
                        ),

                    "wind_speed":
                        item.get(
                            "wind_speed"
                        ),

                    "wind_direction":
                        item.get(
                            "wind_direction"
                        ),

                    "short_forecast":
                        item.get(
                            "short_forecast"
                        ),

                    "detailed_forecast":
                        item.get(
                            "detailed_forecast"
                        ),
                }
                for item in (
                    weather.get(
                        "periods"
                    )
                    or []
                )
            ],
        },

        "events": {
            "status":
                events.get(
                    "status"
                ),

            "items": [
                {
                    "title":
                        item.get(
                            "title"
                        ),

                    "url":
                        item.get(
                            "url"
                        ),

                    "date_match":
                        item.get(
                            "date_match"
                        ),
                }
                for item in (
                    events.get(
                        "items"
                    )
                    or []
                )
            ],
        },

        "news": {
            "status":
                news.get(
                    "status"
                ),

            "items": [
                {
                    "title":
                        item.get(
                            "title"
                        ),

                    "url":
                        item.get(
                            "url"
                        ),
                }
                for item in (
                    news.get(
                        "items"
                    )
                    or []
                )
            ],
        },

        "reddit": {
            "status":
                reddit.get(
                    "status"
                ),

            "items": [
                {
                    "title":
                        item.get(
                            "title"
                        ),

                    "url":
                        item.get(
                            "url"
                        ),
                }
                for item in (
                    reddit.get(
                        "items"
                    )
                    or []
                )
            ],
        },
    }


def materially_changed(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    return (
        normalized_user_content(
            previous
        )
        != normalized_user_content(
            current
        )
    )


def revision_number(
    path: Path,
    report_date: str,
) -> int | None:
    pattern = re.compile(
        "^"
        + re.escape(
            report_date
        )
        + r" Personal Morning Brief"
        + r" - Revision (\d+)\.json$"
    )

    match = pattern.match(
        path.name
    )

    if not match:
        return None

    return int(
        match.group(
            1
        )
    )


def latest_snapshot_for_date(
    snapshot_dir: Path,
    report_date: str,
) -> Path | None:
    canonical = (
        snapshot_dir
        / (
            report_date
            + " Personal Morning Brief.json"
        )
    )

    candidates: list[
        tuple[int, Path]
    ] = []

    if canonical.is_file():
        candidates.append(
            (
                1,
                canonical,
            )
        )

    for path in snapshot_dir.glob(
        report_date
        + " Personal Morning Brief - Revision *.json"
    ):
        number = revision_number(
            path,
            report_date,
        )

        if number is not None:
            candidates.append(
                (
                    number + 1,
                    path,
                )
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda value: value[0]
    )

    return candidates[-1][1]


def next_revision(
    snapshot_dir: Path,
    report_date: str,
) -> int:
    highest = 1

    for path in snapshot_dir.glob(
        report_date
        + " Personal Morning Brief - Revision *.json"
    ):
        number = revision_number(
            path,
            report_date,
        )

        if (
            number is not None
            and number > highest
        ):
            highest = number

    return highest + 1


def artifact_paths(
    paths: dict[str, Path],
    report_date: str,
    revision: int | None,
) -> tuple[Path, Path]:
    if revision is None:
        stem = (
            report_date
            + " Personal Morning Brief"
        )

    else:
        stem = (
            report_date
            + " Personal Morning Brief"
            + " - Revision "
            + str(
                revision
            )
        )

    return (
        paths[
            "report_dir"
        ]
        / (
            stem
            + ".md"
        ),

        paths[
            "snapshot_dir"
        ]
        / (
            stem
            + ".json"
        ),
    )


def render_markdown(
    report: dict[str, Any],
) -> str:
    target_date = str(
        report[
            "report_date"
        ]
    )

    run_kind = str(
        report.get(
            "run_kind",
            "unknown",
        )
    )

    revision = report.get(
        "revision"
    )

    lines = [
        "---",
        'type: "personal-morning-brief"',
        'schema_version: "1.0.0"',
        (
            'report_date: "'
            + target_date
            + '"'
        ),
        (
            'generated_at_utc: "'
            + str(
                report.get(
                    "generated_at_utc"
                )
            )
            + '"'
        ),
        (
            'generated_at_local: "'
            + str(
                report.get(
                    "generated_at_local"
                )
            )
            + '"'
        ),
        (
            'timezone: "'
            + str(
                report.get(
                    "timezone"
                )
            )
            + '"'
        ),
        (
            'run_kind: "'
            + run_kind
            + '"'
        ),
        (
            "revision: "
            + (
                str(
                    revision
                )
                if revision
                is not None
                else "null"
            )
        ),
        "production_delivery: true",
        "---",
        "",
        (
            "# Personal Morning Brief — "
            + target_date
            + (
                (
                    " — Revision "
                    + str(
                        revision
                    )
                )
                if revision
                is not None
                else ""
            )
        ),
        "",
    ]

    catchup = (
        report.get(
            "catchup"
        )
        or {}
    )

    if catchup.get(
        "is_catchup"
    ):
        lines.extend(
            [
                "> **Catch-up report.**",
                (
                    "> This brief is for "
                    + target_date
                    + " but was generated on "
                    + str(
                        catchup.get(
                            "generated_on_date"
                        )
                    )
                    + "."
                ),
                (
                    "> External information reflects data "
                    "available at recovery time; it is not "
                    "presented as historical 7:00 AM data."
                ),
                "",
            ]
        )

    current = (
        report.get(
            "current_state"
        )
        or {}
    )

    lines.extend(
        [
            "## Current Second Brain State",
            "",
        ]
    )

    if (
        current.get(
            "status"
        )
        == "ok"
    ):
        lines.extend(
            [
                (
                    "- Phase: "
                    + str(
                        current.get(
                            "roadmap_phase"
                        )
                    )
                ),
                (
                    "- Step: "
                    + str(
                        current.get(
                            "current_step"
                        )
                    )
                ),
                (
                    "- Source: "
                    + source_link(
                        current.get(
                            "source"
                        )
                    )
                ),
            ]
        )

    else:
        lines.append(
            "- Current Second Brain status: unavailable."
        )

    tasks = (
        report.get(
            "tasks"
        )
        or {}
    )

    lines.extend(
        [
            "",
            "## Current Tasks",
            "",
        ]
    )

    if (
        tasks.get(
            "status"
        )
        != "ok"
    ):
        lines.append(
            "- Task source unavailable."
        )

    elif tasks.get(
        "items"
    ):
        for item in tasks[
            "items"
        ]:
            lines.append(
                "- [ ] "
                + str(
                    item
                )
            )

    else:
        lines.append(
            "- No open canonical tasks."
        )

    if tasks.get(
        "source"
    ):
        lines.append(
            "- Source: "
            + source_link(
                tasks.get(
                    "source"
                )
            )
        )

    shopping = (
        report.get(
            "shopping"
        )
        or {}
    )

    lines.extend(
        [
            "",
            "## Shopping",
            "",
        ]
    )

    if (
        shopping.get(
            "status"
        )
        != "ok"
    ):
        lines.append(
            "- Shopping source unavailable."
        )

    elif shopping.get(
        "items"
    ):
        for item in shopping[
            "items"
        ]:
            lines.append(
                "- [ ] "
                + str(
                    item
                )
            )

    else:
        lines.append(
            "- No active shopping items."
        )

    if shopping.get(
        "source"
    ):
        lines.append(
            "- Source: "
            + source_link(
                shopping.get(
                    "source"
                )
            )
        )

    health = (
        report.get(
            "health",
            {}
        ).get(
            "general",
            {}
        )
    )

    lines.extend(
        [
            "",
            "## Second Brain Health",
            "",
        ]
    )

    if (
        health.get(
            "status"
        )
        == "ok"
    ):
        count = health.get(
            "issue_count"
        )

        lines.append(
            "- General health issue count: "
            + (
                str(
                    count
                )
                if count
                is not None
                else "unavailable"
            )
        )

        source_date = (
            health_source_date(
                report
            )
        )

        if source_date:
            lines.append(
                "- Health report date: "
                + source_date
            )

        lines.append(
            "- General health source: "
            + source_link(
                health.get(
                    "source"
                )
            )
        )

    else:
        lines.append(
            "- General health report unavailable."
        )

    document = (
        report.get(
            "health",
            {}
        ).get(
            "document_pipeline",
            {}
        )
    )

    if (
        document.get(
            "status"
        )
        == "ok"
    ):
        lines.append(
            "- Document health source: "
            + source_link(
                document.get(
                    "source"
                )
            )
        )

    else:
        lines.append(
            "- Document health source: unavailable."
        )

    lines.extend(
        [
            "",
            "## Operational Reviews",
            "",
        ]
    )

    for review in (
        report.get(
            "reviews"
        )
        or []
    ):
        count = review.get(
            "open_count"
        )

        lines.append(
            "- **"
            + str(
                review.get(
                    "label",
                    review.get(
                        "id",
                        "Review",
                    ),
                )
            )
            + "**: "
            + (
                str(
                    count
                )
                if count
                is not None
                else "unavailable"
            )
            + " open"
            + " (status: "
            + str(
                review.get(
                    "status"
                )
            )
            + ")"
        )

        if review.get(
            "source"
        ):
            lines.append(
                "  - Source: "
                + source_link(
                    review.get(
                        "source"
                    )
                )
            )

    external = (
        report.get(
            "external"
        )
        or {}
    )

    weather = (
        external.get(
            "weather"
        )
        or {}
    )

    lines.extend(
        [
            "",
            "## Weather",
            "",
        ]
    )

    if (
        weather.get(
            "status"
        )
        == "ok"
    ):
        for period in (
            weather.get(
                "periods"
            )
            or []
        ):
            lines.append(
                "- **"
                + str(
                    period.get(
                        "name"
                    )
                )
                + "**: "
                + str(
                    period.get(
                        "temperature"
                    )
                )
                + "°"
                + str(
                    period.get(
                        "temperature_unit",
                        "",
                    )
                )
                + ", "
                + str(
                    period.get(
                        "short_forecast"
                    )
                )
                + "; wind "
                + str(
                    period.get(
                        "wind_speed"
                    )
                )
                + " "
                + str(
                    period.get(
                        "wind_direction"
                    )
                )
            )

        lines.append(
            "- Source: ["
            + str(
                weather.get(
                    "source"
                )
            )
            + "]("
            + str(
                weather.get(
                    "source_url"
                )
            )
            + ")"
        )

        lines.append(
            "- Retrieved: "
            + str(
                weather.get(
                    "retrieved_at_utc"
                )
            )
        )

    else:
        lines.append(
            "- Weather unavailable."
        )

    events = (
        external.get(
            "events"
        )
        or {}
    )

    lines.extend(
        [
            "",
            "## Local Events",
            "",
        ]
    )

    if events.get(
        "items"
    ):
        for item in events[
            "items"
        ]:
            lines.append(
                "- ["
                + str(
                    item.get(
                        "title"
                    )
                )
                + "]("
                + str(
                    item.get(
                        "url"
                    )
                )
                + ")"
            )

        lines.append(
            "- Source: ["
            + str(
                events.get(
                    "source"
                )
            )
            + "]("
            + str(
                events.get(
                    "source_url"
                )
            )
            + ")"
        )

    elif (
        events.get(
            "status"
        )
        == "no_events_found"
    ):
        lines.append(
            "- No events matching the collection date "
            "were found in the configured feed."
        )

    else:
        lines.append(
            "- Local events unavailable."
        )

    news = (
        external.get(
            "news"
        )
        or {}
    )

    lines.extend(
        [
            "",
            "## Top News",
            "",
        ]
    )

    if (
        news.get(
            "status"
        )
        == "ok"
    ):
        for item in (
            news.get(
                "items"
            )
            or []
        )[:3]:
            lines.append(
                "- ["
                + str(
                    item.get(
                        "title"
                    )
                )
                + "]("
                + str(
                    item.get(
                        "url"
                    )
                )
                + ")"
            )

    else:
        lines.append(
            "- News unavailable."
        )

    reddit = (
        external.get(
            "reddit"
        )
        or {}
    )

    lines.extend(
        [
            "",
            "## Trending on Reddit",
            "",
        ]
    )

    if (
        reddit.get(
            "status"
        )
        == "ok"
    ):
        for item in (
            reddit.get(
                "items"
            )
            or []
        )[:3]:
            lines.append(
                "- ["
                + str(
                    item.get(
                        "title"
                    )
                )
                + "]("
                + str(
                    item.get(
                        "url"
                    )
                )
                + ")"
            )

    else:
        lines.append(
            "- Reddit unavailable."
        )

    lines.extend(
        [
            "",
            "## Delivery",
            "",
            "- Production notification delivery: enabled.",
            "- Production scheduler: managed by systemd.",
            "",
            "## Safety",
            "",
            "- Collector operation is read-only.",
            "- No tasks were created or modified.",
            "- No shopping items were created or modified.",
            "- No source notes were modified.",
            "- Missing information was not invented.",
            "",
        ]
    )

    return "\n".join(
        lines
    )


def fixture_or_collect(
    config: dict[str, Any],
    collector_path: Path,
    fixture_report: Path | None,
    now: datetime,
) -> dict[str, Any]:
    if fixture_report is not None:
        report = copy.deepcopy(
            load_json(
                fixture_report,
                None,
            )
        )

        if not isinstance(
            report,
            dict,
        ):
            raise RuntimeError(
                "Fixture report is invalid."
            )

        report[
            "generated_at_local"
        ] = now.isoformat()

        report[
            "generated_at_utc"
        ] = now.astimezone(
            timezone.utc
        ).isoformat()

        report[
            "timezone"
        ] = config[
            "timezone"
        ]

        report[
            "mode"
        ] = "production"

        report[
            "collector_version"
        ] = config[
            "collector_version"
        ]

        return report

    collector = load_module(
        collector_path,
        (
            "morning_brief_collector_"
            + hashlib.sha256(
                str(
                    collector_path
                ).encode(
                    "utf-8"
                )
            ).hexdigest()[:8]
        ),
    )

    report = collector.collect(
        config
    )

    if not isinstance(
        report,
        dict,
    ):
        raise RuntimeError(
            "Collector returned invalid report."
        )

    return report


def create_artifact(
    config: dict[str, Any],
    paths: dict[str, Path],
    collector_path: Path,
    fixture_report: Path | None,
    now: datetime,
    target_date: str,
    run_kind: str,
    catchup: bool,
) -> dict[str, Any]:
    paths[
        "report_dir"
    ].mkdir(
        parents=True,
        exist_ok=True,
    )

    paths[
        "snapshot_dir"
    ].mkdir(
        parents=True,
        exist_ok=True,
    )

    previous_path = (
        latest_snapshot_for_date(
            paths[
                "snapshot_dir"
            ],
            target_date,
        )
    )

    previous = (
        load_json(
            previous_path,
            None,
        )
        if previous_path
        else None
    )

    canonical_snapshot = (
        paths[
            "snapshot_dir"
        ]
        / (
            target_date
            + " Personal Morning Brief.json"
        )
    )

    if canonical_snapshot.exists():
        revision = next_revision(
            paths[
                "snapshot_dir"
            ],
            target_date,
        )

    else:
        revision = None

    report = fixture_or_collect(
        config,
        collector_path,
        fixture_report,
        now,
    )

    report[
        "mode"
    ] = "production"

    report[
        "report_date"
    ] = target_date

    report[
        "run_kind"
    ] = run_kind

    report[
        "revision"
    ] = revision

    report[
        "catchup"
    ] = {
        "is_catchup":
            catchup,

        "target_date":
            target_date,

        "generated_on_date":
            now.date().isoformat(),
    }

    changed = (
        True
        if not isinstance(
            previous,
            dict,
        )
        else materially_changed(
            previous,
            report,
        )
    )

    report_path, snapshot_path = (
        artifact_paths(
            paths,
            target_date,
            revision,
        )
    )

    atomic_json(
        snapshot_path,
        report,
    )

    atomic_text(
        report_path,
        render_markdown(
            report
        ),
    )

    return {
        "report_path":
            str(
                report_path
            ),

        "snapshot_path":
            str(
                snapshot_path
            ),

        "revision":
            revision,

        "materially_changed":
            changed,

        "report_sha256":
            sha256_file(
                report_path
            ),

        "snapshot_sha256":
            sha256_file(
                snapshot_path
            ),
    }


def call_delivery(
    delivery_path: Path,
    config_path: Path,
    snapshot_path: Path,
    delivery_key: str,
    kind: str,
    no_send: bool,
    summary_dates: list[str] | None = None,
) -> dict[str, Any]:
    if summary_dates is None:
        command = (
            "preview"
            if no_send
            else "deliver"
        )

        arguments = [
            "/usr/bin/python3",
            str(
                delivery_path
            ),
            command,
            "--config",
            str(
                config_path
            ),
            "--report",
            str(
                snapshot_path
            ),
            "--delivery-key",
            delivery_key,
            "--kind",
            kind,
        ]

    else:
        command = (
            "preview-summary"
            if no_send
            else "deliver-summary"
        )

        arguments = [
            "/usr/bin/python3",
            str(
                delivery_path
            ),
            command,
            "--config",
            str(
                config_path
            ),
            "--report",
            str(
                snapshot_path
            ),
            "--delivery-key",
            delivery_key,
            "--dates-json",
            json.dumps(
                summary_dates
            ),
        ]

    completed = subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        timeout=90,
    )

    if (
        completed.returncode
        != 0
    ):
        raise RuntimeError(
            "Delivery command failed: "
            + completed.stderr[
                :2000
            ]
        )

    return json.loads(
        completed.stdout.strip()
    )


def queue_notification(
    state: dict[str, Any],
    delivery_key: str,
    kind: str,
    snapshot_path: str,
    dates: list[str] | None,
    reason: str,
) -> None:
    state[
        "pending_notifications"
    ][
        delivery_key
    ] = {
        "kind":
            kind,

        "snapshot_path":
            snapshot_path,

        "dates":
            dates,

        "reason":
            reason,
    }




def attempt_notification(
    state: dict[str, Any],
    delivery_path: Path,
    config_path: Path,
    delivery_key: str,
    kind: str,
    snapshot_path: Path,
    no_send: bool,
    simulate_failure: bool,
    summary_dates: list[str] | None = None,
) -> dict[str, Any]:
    queue_notification(
        state,
        delivery_key,
        kind,
        str(snapshot_path),
        summary_dates,
        "pending_delivery_attempt",
    )

    if simulate_failure:
        state[
            "pending_notifications"
        ][
            delivery_key
        ][
            "reason"
        ] = "simulated_delivery_failure"

        return {
            "status":
                "failed",

            "sent":
                False,

            "delivery_key":
                delivery_key,

            "queued_for_retry":
                True,

            "reason":
                "simulated_delivery_failure",
        }

    try:
        result = call_delivery(
            delivery_path,
            config_path,
            snapshot_path,
            delivery_key,
            kind,
            no_send,
            summary_dates,
        )

    except Exception as exc:
        state[
            "pending_notifications"
        ][
            delivery_key
        ][
            "reason"
        ] = (
            type(exc).__name__
            + ": "
            + str(exc)
        )

        return {
            "status":
                "failed",

            "sent":
                False,

            "delivery_key":
                delivery_key,

            "queued_for_retry":
                True,

            "reason":
                type(exc).__name__
                + ": "
                + str(exc),
        }

    state[
        "pending_notifications"
    ].pop(
        delivery_key,
        None,
    )

    return result




def retry_pending(
    state: dict[str, Any],
    delivery_path: Path,
    config_path: Path,
    no_send: bool,
    simulate_failure: bool,
) -> list[dict[str, Any]]:
    results = []

    for (
        key,
        pending,
    ) in list(
        state[
            "pending_notifications"
        ].items()
    ):
        if (
            pending.get(
                "kind"
            )
            == "revision"
        ):
            continue

        snapshot_path = Path(
            pending[
                "snapshot_path"
            ]
        )

        dates = (
            pending.get(
                "dates"
            )
        )

        result = attempt_notification(
            state,
            delivery_path,
            config_path,
            key,
            pending[
                "kind"
            ],
            snapshot_path,
            no_send,
            simulate_failure,
            dates,
        )

        results.append(
            result
        )

    return results


def schedule_weekday_numbers(
    days: list[str],
) -> set[int]:
    mapping = {
        "Monday":
            0,
        "Tuesday":
            1,
        "Wednesday":
            2,
        "Thursday":
            3,
        "Friday":
            4,
        "Saturday":
            5,
        "Sunday":
            6,
    }

    return {
        mapping[
            value
        ]
        for value in days
    }


def scheduled_time(
    config: dict[str, Any],
) -> Time:
    raw = config[
        "schedule"
    ][
        "local_time"
    ]

    hour, minute = [
        int(
            value
        )
        for value in raw.split(
            ":"
        )
    ]

    return Time(
        hour,
        minute,
    )



def due_schedule_dates(
    config: dict[str, Any],
    state: dict[str, Any],
    now: datetime,
) -> tuple[
    list[str],
    list[str],
]:
    production = (
        config[
            "production"
        ]
    )

    activation = Date.fromisoformat(
        production[
            "activation_date"
        ]
    )

    lookback = int(
        production[
            "catchup_lookback_days"
        ]
    )

    cutoff = (
        now.date()
        - timedelta(
            days=lookback
        )
    )

    weekday_numbers = (
        schedule_weekday_numbers(
            config[
                "schedule"
            ][
                "days"
            ]
        )
    )

    target_time = scheduled_time(
        config
    )

    if now.timetz().replace(
        tzinfo=None
    ) < target_time:
        end_date = (
            now.date()
            - timedelta(
                days=1
            )
        )
    else:
        end_date = now.date()

    due = []
    expired = []

    cursor = activation

    completed = (
        state[
            "scheduled_completed"
        ]
    )

    already_expired = set(
        state[
            "expired_scheduled_dates"
        ]
    )

    # ACTIVATION_DAY_DEFERRED:
    # Production activation after today's scheduled
    # time must not create an immediate catch-up.
    # The activation date remains incomplete and
    # becomes catch-up eligible on a later date.
    defer_activation_date = (
        now.date()
        == activation
    )

    while cursor <= end_date:
        if (
            defer_activation_date
            and cursor == activation
        ):
            cursor += timedelta(
                days=1
            )

            continue

        if (
            cursor.weekday()
            in weekday_numbers
        ):
            key = cursor.isoformat()

            if key in completed:
                cursor += timedelta(
                    days=1
                )

                continue

            if cursor < cutoff:
                if key not in already_expired:
                    expired.append(
                        key
                    )
            else:
                due.append(
                    key
                )

        cursor += timedelta(
            days=1
        )

    return (
        due,
        expired,
    )



def is_on_time_today(
    config: dict[str, Any],
    now: datetime,
    target_date: str,
) -> bool:
    if (
        target_date
        != now.date().isoformat()
    ):
        return False

    target = datetime.combine(
        now.date(),
        scheduled_time(
            config
        ),
        tzinfo=now.tzinfo,
    )

    delta_seconds = (
        now
        - target
    ).total_seconds()

    tolerance = int(
        config[
            "production"
        ][
            "timing_tolerance_minutes"
        ]
    ) * 60

    return (
        0
        <= delta_seconds
        <= tolerance
    )


def abandon_old_revision_notifications(
    state: dict[str, Any],
    report_date: str,
) -> None:
    for (
        key,
        pending,
    ) in list(
        state[
            "pending_notifications"
        ].items()
    ):
        if (
            pending.get(
                "kind"
            )
            != "revision"
        ):
            continue

        snapshot = str(
            pending.get(
                "snapshot_path",
                "",
            )
        )

        if (
            report_date
            not in snapshot
        ):
            continue

        state[
            "abandoned_notifications"
        ].append(
            {
                "delivery_key":
                    key,

                "reason":
                    (
                        "Superseded by next "
                        "manual revision."
                    ),
            }
        )

        state[
            "pending_notifications"
        ].pop(
            key,
            None,
        )


def run_manual(
    config_path: Path,
    collector_path: Path,
    delivery_path: Path,
    runtime_root: Path | None,
    fixture_report: Path | None,
    now_override: str | None,
    no_send: bool,
    simulate_failure: bool,
) -> dict[str, Any]:
    config = load_json(
        config_path,
        None,
    )

    if not isinstance(
        config,
        dict,
    ):
        raise RuntimeError(
            "Config is invalid."
        )

    paths = runtime_paths(
        config,
        runtime_root,
    )

    state = load_state(
        paths[
            "state"
        ]
    )

    now = parse_now(
        config[
            "timezone"
        ],
        now_override,
    )

    target_date = (
        now.date().isoformat()
    )

    abandon_old_revision_notifications(
        state,
        target_date,
    )

    artifact = create_artifact(
        config,
        paths,
        collector_path,
        fixture_report,
        now,
        target_date,
        "manual",
        False,
    )

    revision = artifact[
        "revision"
    ]

    if (
        revision is not None
        and artifact[
            "materially_changed"
        ]
        is False
    ):
        notification = {
            "status":
                "not_sent",

            "sent":
                False,

            "reason":
                "unchanged_revision",
        }

    else:
        if revision is None:
            kind = "normal"

            key = (
                "manual:"
                + target_date
            )

        else:
            kind = "revision"

            key = (
                "manual-revision:"
                + target_date
                + ":"
                + str(
                    revision
                )
            )

        notification = attempt_notification(
            state,
            delivery_path,
            config_path,
            key,
            kind,
            Path(
                artifact[
                    "snapshot_path"
                ]
            ),
            no_send,
            simulate_failure,
        )

    result = {
        "status":
            "completed",

        "command":
            "manual",

        "date":
            target_date,

        "artifact":
            artifact,

        "notification":
            notification,
    }

    state[
        "last_run"
    ] = result

    atomic_json(
        paths[
            "state"
        ],
        state,
    )

    append_jsonl(
        paths[
            "log"
        ],
        result,
    )

    return result


def run_activate_existing(
    config_path: Path,
    delivery_path: Path,
    runtime_root: Path | None,
    date_value: str,
    no_send: bool,
    simulate_failure: bool,
) -> dict[str, Any]:
    config = load_json(
        config_path,
        None,
    )

    paths = runtime_paths(
        config,
        runtime_root,
    )

    state = load_state(
        paths[
            "state"
        ]
    )

    snapshot = (
        paths[
            "snapshot_dir"
        ]
        / (
            date_value
            + " Personal Morning Brief.json"
        )
    )

    if not snapshot.is_file():
        raise RuntimeError(
            "Existing canonical snapshot not found: "
            + str(
                snapshot
            )
        )

    key = (
        "activation:"
        + date_value
    )

    notification = attempt_notification(
        state,
        delivery_path,
        config_path,
        key,
        "normal",
        snapshot,
        no_send,
        simulate_failure,
    )

    result = {
        "status":
            "completed",

        "command":
            "activate-existing",

        "date":
            date_value,

        "scheduled_completion_recorded":
            False,

        "notification":
            notification,
    }

    state[
        "last_run"
    ] = result

    atomic_json(
        paths[
            "state"
        ],
        state,
    )

    append_jsonl(
        paths[
            "log"
        ],
        result,
    )

    return result


def mark_scheduled_complete(
    state: dict[str, Any],
    target_date: str,
    artifact: dict[str, Any],
    run_kind: str,
) -> None:
    state[
        "scheduled_completed"
    ][
        target_date
    ] = {
        "run_kind":
            run_kind,

        "report_path":
            artifact[
                "report_path"
            ],

        "snapshot_path":
            artifact[
                "snapshot_path"
            ],

        "revision":
            artifact[
                "revision"
            ],
    }


def run_scheduled(
    config_path: Path,
    collector_path: Path,
    delivery_path: Path,
    runtime_root: Path | None,
    fixture_report: Path | None,
    now_override: str | None,
    no_send: bool,
    simulate_failure: bool,
) -> dict[str, Any]:
    config = load_json(
        config_path,
        None,
    )

    if not isinstance(
        config,
        dict,
    ):
        raise RuntimeError(
            "Config is invalid."
        )

    paths = runtime_paths(
        config,
        runtime_root,
    )

    state = load_state(
        paths[
            "state"
        ]
    )

    now = parse_now(
        config[
            "timezone"
        ],
        now_override,
    )

    retry_results = retry_pending(
        state,
        delivery_path,
        config_path,
        no_send,
        simulate_failure,
    )

    due, newly_expired = (
        due_schedule_dates(
            config,
            state,
            now,
        )
    )

    for value in newly_expired:
        if (
            value
            not in state[
                "expired_scheduled_dates"
            ]
        ):
            state[
                "expired_scheduled_dates"
            ].append(
                value
            )

    current_dates = [
        value
        for value in due
        if is_on_time_today(
            config,
            now,
            value,
        )
    ]

    missed_dates = [
        value
        for value in due
        if value
        not in current_dates
    ]

    recovered = []
    notifications = []

    if len(
        missed_dates
    ) > 1:
        last_artifact = None

        for target_date in missed_dates:
            artifact = create_artifact(
                config,
                paths,
                collector_path,
                fixture_report,
                now,
                target_date,
                "catchup",
                True,
            )

            mark_scheduled_complete(
                state,
                target_date,
                artifact,
                "catchup",
            )

            atomic_json(
                paths[
                    "state"
                ],
                state,
            )

            recovered.append(
                {
                    "date":
                        target_date,

                    "artifact":
                        artifact,
                }
            )

            last_artifact = artifact

        if last_artifact is not None:
            summary_key = (
                "catchup-summary:"
                + missed_dates[0]
                + ":"
                + missed_dates[-1]
                + ":"
                + str(
                    len(
                        missed_dates
                    )
                )
            )

            summary = attempt_notification(
                state,
                delivery_path,
                config_path,
                summary_key,
                "catchup_summary",
                Path(
                    last_artifact[
                        "snapshot_path"
                    ]
                ),
                no_send,
                simulate_failure,
                missed_dates,
            )

            notifications.append(
                summary
            )

    elif len(
        missed_dates
    ) == 1:
        target_date = (
            missed_dates[0]
        )

        artifact = create_artifact(
            config,
            paths,
            collector_path,
            fixture_report,
            now,
            target_date,
            "catchup",
            True,
        )

        mark_scheduled_complete(
            state,
            target_date,
            artifact,
            "catchup",
        )

        atomic_json(
            paths[
                "state"
            ],
            state,
        )

        recovered.append(
            {
                "date":
                    target_date,

                "artifact":
                    artifact,
            }
        )

        notification = attempt_notification(
            state,
            delivery_path,
            config_path,
            (
                "catchup:"
                + target_date
            ),
            "catchup",
            Path(
                artifact[
                    "snapshot_path"
                ]
            ),
            no_send,
            simulate_failure,
        )

        notifications.append(
            notification
        )

    scheduled_current = []

    for target_date in current_dates:
        artifact = create_artifact(
            config,
            paths,
            collector_path,
            fixture_report,
            now,
            target_date,
            "scheduled",
            False,
        )

        mark_scheduled_complete(
            state,
            target_date,
            artifact,
            "scheduled",
        )

        atomic_json(
            paths[
                "state"
            ],
            state,
        )

        scheduled_current.append(
            {
                "date":
                    target_date,

                "artifact":
                    artifact,
            }
        )

        notification = attempt_notification(
            state,
            delivery_path,
            config_path,
            (
                "scheduled:"
                + target_date
            ),
            "normal",
            Path(
                artifact[
                    "snapshot_path"
                ]
            ),
            no_send,
            simulate_failure,
        )

        notifications.append(
            notification
        )

    result = {
        "status":
            "completed",

        "command":
            "scheduled",

        "now":
            now.isoformat(),

        "due_dates":
            due,

        "newly_expired_dates":
            newly_expired,

        "missed_dates":
            missed_dates,

        "current_dates":
            current_dates,

        "retry_results":
            retry_results,

        "recovered":
            recovered,

        "scheduled_current":
            scheduled_current,

        "notifications":
            notifications,

        "pending_notification_count":
            len(
                state[
                    "pending_notifications"
                ]
            ),
    }

    state[
        "last_run"
    ] = result

    atomic_json(
        paths[
            "state"
        ],
        state,
    )

    append_jsonl(
        paths[
            "log"
        ],
        result,
    )

    return result


def self_test() -> None:
    base = {
        "tasks": {
            "status":
                "ok",

            "open_count":
                1,

            "items": [
                "A"
            ],
        },

        "shopping": {
            "status":
                "ok",

            "open_count":
                0,

            "items":
                [],
        },

        "health": {
            "general": {
                "status":
                    "ok",

                "issue_count":
                    2,

                "source": {
                    "path":
                        (
                            "90 System/Health/"
                            "2026-08-14_pipeline-health.md"
                        ),

                    "sha256":
                        "AAA",
                },
            },
        },

        "reviews":
            [],

        "external": {
            "weather": {
                "status":
                    "ok",

                "retrieved_at_utc":
                    "OLD",

                "periods":
                    [],
            },

            "events": {
                "status":
                    "ok",

                "items":
                    [],
            },

            "news": {
                "status":
                    "ok",

                "items":
                    [],
            },

            "reddit": {
                "status":
                    "ok",

                "items":
                    [],
            },
        },

        "generated_at_utc":
            "OLD",

        "generated_at_local":
            "OLD",
    }

    same = copy.deepcopy(
        base
    )

    same[
        "generated_at_utc"
    ] = "NEW"

    same[
        "generated_at_local"
    ] = "NEW"

    same[
        "health"
    ][
        "general"
    ][
        "source"
    ][
        "sha256"
    ] = "BBB"

    same[
        "external"
    ][
        "weather"
    ][
        "retrieved_at_utc"
    ] = "NEW"

    if materially_changed(
        base,
        same,
    ):
        raise RuntimeError(
            "Material comparison incorrectly "
            "included timestamps/hashes."
        )

    changed = copy.deepcopy(
        base
    )

    changed[
        "tasks"
    ][
        "open_count"
    ] = 2

    if not materially_changed(
        base,
        changed,
    ):
        raise RuntimeError(
            "Material comparison missed task change."
        )

    sample_config = {
        "schedule": {
            "days": [
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
            ],

            "local_time":
                "07:00",
        },

        "production": {
            "activation_date":
                "2026-08-14",

            "catchup_lookback_days":
                30,

            "timing_tolerance_minutes":
                5,
        },
    }

    sample_state = empty_state()

    now = datetime.fromisoformat(
        "2026-08-18T07:00:00-04:00"
    )

    due, expired = due_schedule_dates(
        sample_config,
        sample_state,
        now,
    )

    if (
        "2026-08-14"
        not in due
        or
        "2026-08-18"
        not in due
        or
        expired
    ):
        raise RuntimeError(
            "Scheduled-date self-test failed."
        )

    if not is_on_time_today(
        sample_config,
        now,
        "2026-08-18",
    ):
        raise RuntimeError(
            "Timing tolerance self-test failed."
        )

    print(
        json.dumps(
            {
                "status":
                    "completed",

                "self_test":
                    "pass",
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "self-test",
            "manual",
            "scheduled",
            "activate-existing",
        ],
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
    )

    parser.add_argument(
        "--collector",
        type=Path,
        default=DEFAULT_COLLECTOR,
    )

    parser.add_argument(
        "--delivery",
        type=Path,
        default=DEFAULT_DELIVERY,
    )

    parser.add_argument(
        "--runtime-root",
        type=Path,
    )

    parser.add_argument(
        "--fixture-report",
        type=Path,
    )

    parser.add_argument(
        "--now",
    )

    parser.add_argument(
        "--no-send",
        action="store_true",
    )

    parser.add_argument(
        "--simulate-delivery-failure",
        action="store_true",
    )

    parser.add_argument(
        "--date",
    )

    args = parser.parse_args()

    if args.command == "self-test":
        self_test()
        return

    if args.command == "manual":
        result = run_manual(
            args.config,
            args.collector,
            args.delivery,
            args.runtime_root,
            args.fixture_report,
            args.now,
            args.no_send,
            args.simulate_delivery_failure,
        )

    elif args.command == "scheduled":
        result = run_scheduled(
            args.config,
            args.collector,
            args.delivery,
            args.runtime_root,
            args.fixture_report,
            args.now,
            args.no_send,
            args.simulate_delivery_failure,
        )

    else:
        date_value = (
            args.date
            or parse_now(
                load_json(
                    args.config,
                    {}
                ).get(
                    "timezone",
                    (
                        "America/Kentucky/"
                        "Louisville"
                    ),
                ),
                args.now,
            ).date().isoformat()
        )

        result = run_activate_existing(
            args.config,
            args.delivery,
            args.runtime_root,
            date_value,
            args.no_send,
            args.simulate_delivery_failure,
        )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

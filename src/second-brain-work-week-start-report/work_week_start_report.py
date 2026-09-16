#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
from datetime import date as Date
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
import argparse
import copy
import hashlib
import importlib.util
import json
import re
import sys


WORK_TOKENS = (
    "w2 work",
    "current job",
    "career progression",
)

AMBIGUOUS_WORK_TERMS = (
    "work",
    "shift",
    "supervisor",
    "machinist",
    "machine",
)


def load_json(path: Path):
    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def atomic_text(
    path: Path,
    text: str,
):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp = path.with_name(
        path.name + ".tmp"
    )

    tmp.write_text(
        text,
        encoding="utf-8",
    )

    tmp.replace(path)


def atomic_json(
    path: Path,
    value,
):
    atomic_text(
        path,
        json.dumps(
            value,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
    )



def append_jsonl(
    path: Path,
    value,
):
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
                sort_keys=True,
            )
            + "\n"
        )

def parse_frontmatter(
    text: str,
) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text

    end = text.find(
        "\n---",
        4,
    )

    if end == -1:
        return {}, text

    metadata = {}

    for line in text[
        4:end
    ].splitlines():
        match = re.match(
            r"^([A-Za-z0-9_-]+):\s*(.*)$",
            line,
        )

        if match:
            metadata[
                match.group(1).casefold()
            ] = match.group(2).strip()

    body = text[
        end + 4:
    ].lstrip(
        "\n"
    )

    return metadata, body


def source_link(
    relative: str,
) -> str:
    if relative.endswith(".md"):
        relative = relative[:-3]

    return (
        "[["
        + relative
        + "]]"
    )


def safe_iso_datetime(
    value: str | None,
) -> datetime | None:
    if not value:
        return None

    value = str(value).strip()

    if value.endswith("Z"):
        value = (
            value[:-1]
            + "+00:00"
        )

    try:
        parsed = datetime.fromisoformat(
            value
        )
    except ValueError:
        return None

    return parsed


def safe_iso_date(
    value: str | None,
) -> Date | None:
    if not value:
        return None

    match = re.search(
        r"\b(\d{4}-\d{2}-\d{2})\b",
        str(value),
    )

    if not match:
        return None

    try:
        return Date.fromisoformat(
            match.group(1)
        )
    except ValueError:
        return None



def schedule_anchor(
    now: datetime,
) -> datetime:
    local = now

    days_since_friday = (
        local.weekday() - 4
    ) % 7

    friday_date = (
        local.date()
        - timedelta(
            days=days_since_friday
        )
    )

    anchor = datetime.combine(
        friday_date,
        time(
            hour=16,
        ),
        tzinfo=local.tzinfo,
    )

    # On Friday itself, a manual/preflight run before 16:00
    # belongs to today's upcoming Work Week Start Report.
    #
    # On Saturday through Thursday, the most recent Friday
    # remains the active/most-recent report anchor.
    if (
        local < anchor
        and local.weekday() != 4
    ):
        anchor -= timedelta(
            days=7
        )

    return anchor



def report_windows(
    now: datetime,
) -> dict:
    anchor = schedule_anchor(
        now
    )

    work_start = anchor.replace(
        hour=19,
        minute=0,
        second=0,
        microsecond=0,
    )

    work_end = (
        work_start
        + timedelta(
            days=2,
            hours=12,
        )
    )

    return {
        "anchor":
            anchor,

        "email_start":
            anchor
            - timedelta(
                days=7
            ),

        "email_end":
            anchor,

        "work_start":
            work_start,

        "work_end":
            work_end,

        "next_7_end":
            anchor
            + timedelta(
                days=7
            ),
    }


def explicit_work_relationship(
    text: str,
    metadata: dict[str, str],
) -> bool:
    values = " ".join(
        str(value)
        for value in metadata.values()
    ).casefold()

    lower = text.casefold()

    if any(
        token in values
        for token in WORK_TOKENS
    ):
        return True

    explicit_links = (
        "[[10 areas/w2 work/",
        "[[w2 work/",
        "area: work",
        "area: w2 work",
    )

    return any(
        token in lower
        for token in explicit_links
    )


def collect_areas(
    vault: Path,
    config: dict,
) -> dict:
    items = []
    missing = []

    for relative in (
        config[
            "paths"
        ][
            "work_areas"
        ]
    ):
        path = vault / relative

        if not path.is_file():
            missing.append(
                relative
            )
            continue

        metadata, body = parse_frontmatter(
            path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        )

        items.append(
            {
                "name":
                    path.stem,

                "path":
                    relative,

                "source":
                    source_link(
                        relative
                    ),

                "status":
                    metadata.get(
                        "status"
                    ),
            }
        )

    return {
        "status":
            (
                "ok"
                if items
                else "unavailable"
            ),

        "items":
            items,

        "missing":
            missing,
    }


def collect_projects(
    vault: Path,
    config: dict,
) -> dict:
    root_relative = (
        config[
            "paths"
        ][
            "projects_root"
        ]
    )

    root = vault / root_relative

    if not root.is_dir():
        return {
            "status":
                "unavailable",

            "items":
                [],

            "reason":
                "projects_root_missing",
        }

    items = []

    for path in sorted(
        root.rglob(
            "*.md"
        )
    ):
        relative = str(
            path.relative_to(
                vault
            )
        )

        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        metadata, body = (
            parse_frontmatter(
                text
            )
        )

        if not explicit_work_relationship(
            text,
            metadata,
        ):
            continue

        status = (
            metadata.get(
                "status",
                ""
            )
            .strip()
            .casefold()
        )

        if status in {
            "complete",
            "completed",
            "done",
            "archived",
            "closed",
        }:
            continue

        due = (
            safe_iso_date(
                metadata.get(
                    "due"
                )
            )
            or safe_iso_date(
                metadata.get(
                    "due_date"
                )
            )
            or safe_iso_date(
                metadata.get(
                    "deadline"
                )
            )
        )

        items.append(
            {
                "name":
                    path.stem,

                "path":
                    relative,

                "source":
                    source_link(
                        relative
                    ),

                "status":
                    metadata.get(
                        "status"
                    ),

                "due":
                    (
                        due.isoformat()
                        if due
                        else None
                    ),
            }
        )

    return {
        "status":
            "ok",

        "items":
            items,

        "count":
            len(
                items
            ),
    }


def task_blocks(
    text: str,
) -> list[list[str]]:
    lines = text.splitlines()

    blocks = []

    index = 0

    while index < len(lines):
        line = lines[index]

        if not re.match(
            r"^\s*-\s+\[\s\]\s+",
            line,
        ):
            index += 1
            continue

        block = [
            line
        ]

        cursor = index + 1

        while cursor < len(lines):
            candidate = lines[cursor]

            if re.match(
                r"^\s*-\s+\[[ xX]\]\s+",
                candidate,
            ):
                break

            if (
                candidate.startswith(
                    "  "
                )
                or not candidate.strip()
            ):
                block.append(
                    candidate
                )

                cursor += 1
                continue

            break

        blocks.append(
            block
        )

        index = cursor

    return blocks


def collect_tasks(
    vault: Path,
    config: dict,
) -> dict:
    relative = (
        config[
            "paths"
        ][
            "task_queue"
        ]
    )

    path = vault / relative

    if not path.is_file():
        return {
            "status":
                "unavailable",

            "items":
                [],

            "ambiguous":
                [],

            "reason":
                "task_queue_missing",
        }

    text = path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    explicit = []
    ambiguous = []

    for block in task_blocks(
        text
    ):
        joined = "\n".join(
            block
        )

        lower = joined.casefold()

        first = re.sub(
            r"^\s*-\s+\[\s\]\s+",
            "",
            block[0],
        ).strip()

        metadata = {}

        for line in block[1:]:
            match = re.match(
                r"^\s*-\s*([A-Za-z _-]+):\s*(.*)$",
                line,
            )

            if match:
                metadata[
                    match.group(1)
                    .strip()
                    .casefold()
                ] = (
                    match.group(2)
                    .strip()
                )

        explicit_work = (
            explicit_work_relationship(
                joined,
                metadata,
            )
        )

        due = None

        for key in (
            "due",
            "due date",
            "deadline",
        ):
            if key in metadata:
                due = safe_iso_date(
                    metadata[
                        key
                    ]
                )

                if due:
                    break

        item = {
            "text":
                first,

            "source":
                source_link(
                    relative
                ),

            "due":
                (
                    due.isoformat()
                    if due
                    else None
                ),
        }

        if explicit_work:
            explicit.append(
                item
            )

            continue

        if any(
            term in lower
            for term in AMBIGUOUS_WORK_TERMS
        ):
            ambiguous.append(
                item
            )

    return {
        "status":
            "ok",

        "items":
            explicit,

        "count":
            len(
                explicit
            ),

        "ambiguous":
            ambiguous,

        "ambiguous_count":
            len(
                ambiguous
            ),
    }


def strip_frontmatter_body(
    text: str,
) -> str:
    metadata, body = (
        parse_frontmatter(
            text
        )
    )

    return body


def email_summary(
    body: str,
) -> str:
    lines = []

    for raw in body.splitlines():
        line = " ".join(
            raw.split()
        )

        if not line:
            continue

        if line.startswith(
            "#"
        ):
            continue

        lines.append(
            line
        )

    text = " ".join(
        lines
    )

    if len(text) > 280:
        text = (
            text[:277]
            .rstrip()
            + "..."
        )

    return text



def email_relevance_score(
    subject: str,
    body: str,
    relevance_context: list[str] | None = None,
) -> int:
    subject_text = (
        subject
        or ""
    ).casefold()

    body_text = (
        body
        or ""
    ).casefold()

    combined = (
        subject_text
        + "\n"
        + body_text
    )

    weighted_terms = {
        "urgent": 5,
        "deadline": 5,
        "due": 3,
        "blocking": 5,
        "blocked": 5,
        "issue": 4,
        "problem": 4,
        "failure": 4,
        "machine": 4,
        "cnc": 4,
        "equipment": 4,
        "maintenance": 4,
        "quality": 3,
        "safety": 5,
        "production": 3,
        "staffing": 4,
        "schedule": 3,
        "shift": 4,
        "supervisor": 3,
        "project": 3,
        "task": 3,
        "work": 2,
        "weekend": 2,
    }

    score = 0

    for term, weight in (
        weighted_terms.items()
    ):
        if term in subject_text:
            score += (
                weight * 2
            )

        elif term in body_text:
            score += weight

    for raw in (
        relevance_context
        or []
    ):
        term = " ".join(
            str(
                raw
            ).casefold().split()
        )

        if len(term) < 4:
            continue

        if term in combined:
            score += 8

        words = [
            word
            for word in re.findall(
                r"[a-z0-9]+",
                term,
            )
            if len(word) >= 5
        ]

        for word in set(
            words
        ):
            if word in combined:
                score += 2

    return score


def collect_email(
    vault: Path,
    config: dict,
    windows: dict,
    relevance_context: list[str] | None = None,
) -> dict:
    email_config = (
        config[
            "work_email"
        ]
    )

    if not email_config.get(
        "enabled"
    ):
        return {
            "status":
                "unavailable",

            "reason":
                "work_email_source_not_bound",

            "items":
                [],

            "count":
                0,
        }

    configured = (
        email_config.get(
            "source_directory"
        )
    )

    if not configured:
        return {
            "status":
                "unavailable",

            "reason":
                "work_email_source_not_bound",

            "items":
                [],

            "count":
                0,
        }

    source_dir = (
        Path(configured)
        if Path(
            configured
        ).is_absolute()
        else vault
        / configured
    )

    if not source_dir.is_dir():
        return {
            "status":
                "unavailable",

            "reason":
                "work_email_source_missing",

            "items":
                [],

            "count":
                0,
        }

    candidates = []

    for path in sorted(
        source_dir.rglob(
            "*.md"
        )
    ):
        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        metadata, body = (
            parse_frontmatter(
                text
            )
        )

        if str(
            metadata.get(
                "spam",
                ""
            )
        ).casefold() in {
            "true",
            "yes",
            "1",
        }:
            continue

        received = None

        for key in (
            "received_at",
            "received",
            "date",
            "sent_at",
        ):
            received = safe_iso_datetime(
                metadata.get(
                    key
                )
            )

            if received:
                break

        if received is None:
            continue

        if received.tzinfo is None:
            received = received.replace(
                tzinfo=windows[
                    "anchor"
                ].tzinfo,
            )

        received = received.astimezone(
            windows[
                "anchor"
            ].tzinfo
        )

        if not (
            windows[
                "email_start"
            ]
            <= received
            <= windows[
                "email_end"
            ]
        ):
            continue

        relative = (
            str(
                path.relative_to(
                    vault
                )
            )
            if vault in path.parents
            else str(path)
        )

        subject = str(
            metadata.get(
                "subject"
            )
            or path.stem
        ).strip()

        if (
            len(subject) >= 2
            and subject[0] == subject[-1]
            and subject[0] in {
                '"',
                "'",
            }
        ):
            subject = subject[
                1:-1
            ].strip()

        raw_sender = (
            metadata.get(
                "from"
            )
            or metadata.get(
                "sender"
            )
        )

        sender = (
            str(
                raw_sender
            ).strip()
            if raw_sender is not None
            else None
        )

        if (
            sender
            and len(sender) >= 2
            and sender[0] == sender[-1]
            and sender[0] in {
                '"',
                "'",
            }
        ):
            sender = sender[
                1:-1
            ].strip()

        score = email_relevance_score(
            subject,
            body,
            relevance_context,
        )

        # This is a Work Email digest, not a chronological
        # mailbox dump. Items with no Work relevance signal
        # remain excluded.
        if score <= 0:
            continue

        summary = email_summary(
            body
        )

        candidates.append(
            {
                "subject":
                    subject,

                "sender":
                    sender,

                "received_at":
                    received.isoformat(),

                "summary":
                    summary,

                "relevance_score":
                    score,

                "source":
                    (
                        source_link(
                            relative
                        )
                        if vault in path.parents
                        else str(path)
                    ),

                "authority":
                    "context_only_untrusted",
            }
        )

    candidates.sort(
        key=lambda item: (
            -int(
                item[
                    "relevance_score"
                ]
            ),
            item[
                "received_at"
            ],
        )
    )

    limit = int(
        email_config.get(
            "max_summaries",
            10,
        )
    )

    selected = candidates[
        :limit
    ]

    return {
        "status":
            "ok",

        "count":
            len(
                candidates
            ),

        "items":
            selected,

        "additional_count":
            max(
                0,
                len(candidates)
                - len(selected),
            ),
    }



def collect_calendar(
    vault: Path,
    config: dict,
    windows: dict,
) -> dict:
    calendar = config[
        "calendar"
    ]

    if not calendar.get(
        "enabled"
    ):
        return {
            "status":
                "unavailable",

            "reason":
                "calendar_source_not_bound",

            "items":
                [],
        }

    source = calendar.get(
        "source_file"
    )

    if not source:
        return {
            "status":
                "unavailable",

            "reason":
                "calendar_source_not_bound",

            "items":
                [],
        }

    path = (
        Path(source)
        if Path(
            source
        ).is_absolute()
        else vault
        / source
    )

    if not path.is_file():
        return {
            "status":
                "unavailable",

            "reason":
                "calendar_source_missing",

            "items":
                [],
        }

    raw = load_json(
        path
    )

    if isinstance(
        raw,
        dict,
    ):
        events = (
            raw.get(
                "events"
            )
            or []
        )
    elif isinstance(
        raw,
        list,
    ):
        events = raw
    else:
        events = []

    parsed = []

    for event in events:
        if not isinstance(
            event,
            dict,
        ):
            continue

        start = (
            safe_iso_datetime(
                event.get(
                    "start"
                )
            )
        )

        if start is None:
            continue

        if start.tzinfo is None:
            start = start.replace(
                tzinfo=windows[
                    "anchor"
                ].tzinfo,
            )

        start = start.astimezone(
            windows[
                "anchor"
            ].tzinfo
        )

        parsed.append(
            {
                "title":
                    str(
                        event.get(
                            "title",
                            "Untitled event",
                        )
                    ),

                "start":
                    start.isoformat(),

                "source":
                    str(
                        event.get(
                            "source",
                            path,
                        )
                    ),
            }
        )

    parsed.sort(
        key=lambda item:
            item[
                "start"
            ]
    )

    return {
        "status":
            "ok",

        "items":
            parsed,
    }


def collect_deadlines(
    projects: dict,
    tasks: dict,
    windows: dict,
) -> dict:
    all_items = []

    for item in projects.get(
        "items",
        []
    ):
        due = safe_iso_date(
            item.get(
                "due"
            )
        )

        if due:
            all_items.append(
                {
                    "kind":
                        "project",

                    "text":
                        item[
                            "name"
                        ],

                    "due":
                        due.isoformat(),

                    "source":
                        item[
                            "source"
                        ],
                }
            )

    for item in tasks.get(
        "items",
        []
    ):
        due = safe_iso_date(
            item.get(
                "due"
            )
        )

        if due:
            all_items.append(
                {
                    "kind":
                        "task",

                    "text":
                        item[
                            "text"
                        ],

                    "due":
                        due.isoformat(),

                    "source":
                        item[
                            "source"
                        ],
                }
            )

    work_start_date = (
        windows[
            "work_start"
        ].date()
    )

    work_end_date = (
        windows[
            "work_end"
        ].date()
    )

    next_7_date = (
        windows[
            "next_7_end"
        ].date()
    )

    work_week = [
        item
        for item in all_items
        if (
            work_start_date
            <= Date.fromisoformat(
                item[
                    "due"
                ]
            )
            <= work_end_date
        )
    ]

    next_7 = [
        item
        for item in all_items
        if (
            work_start_date
            <= Date.fromisoformat(
                item[
                    "due"
                ]
            )
            <= next_7_date
        )
    ]

    return {
        "work_week":
            work_week,

        "next_7_days":
            next_7,
    }


def event_horizons(
    calendar: dict,
    windows: dict,
) -> dict:
    if calendar.get(
        "status"
    ) != "ok":
        return {
            "status":
                "unavailable",

            "work_week":
                [],

            "next_7_days":
                [],
        }

    work_week = []
    next_7 = []

    for item in calendar.get(
        "items",
        []
    ):
        start = safe_iso_datetime(
            item.get(
                "start"
            )
        )

        if start is None:
            continue

        if (
            windows[
                "work_start"
            ]
            <= start
            <= windows[
                "work_end"
            ]
        ):
            work_week.append(
                item
            )

        if (
            windows[
                "anchor"
            ]
            <= start
            <= windows[
                "next_7_end"
            ]
        ):
            next_7.append(
                item
            )

    return {
        "status":
            "ok",

        "work_week":
            work_week,

        "next_7_days":
            next_7,
    }


def derive_priorities(
    projects: dict,
    tasks: dict,
    deadlines: dict,
    events: dict,
) -> list[dict]:
    candidates = []

    for item in deadlines.get(
        "work_week",
        []
    ):
        candidates.append(
            {
                "text":
                    item[
                        "text"
                    ],

                "reason":
                    (
                        "Deadline "
                        + item[
                            "due"
                        ]
                    ),

                "source":
                    item[
                        "source"
                    ],
            }
        )

    for item in tasks.get(
        "items",
        []
    ):
        candidates.append(
            {
                "text":
                    item[
                        "text"
                    ],

                "reason":
                    "Open explicit Work task",

                "source":
                    item[
                        "source"
                    ],
            }
        )

    for item in projects.get(
        "items",
        []
    ):
        candidates.append(
            {
                "text":
                    item[
                        "name"
                    ],

                "reason":
                    "Active explicit Work Project",

                "source":
                    item[
                        "source"
                    ],
            }
        )

    for item in events.get(
        "work_week",
        []
    ):
        candidates.append(
            {
                "text":
                    item[
                        "title"
                    ],

                "reason":
                    "Upcoming Work event",

                "source":
                    item.get(
                        "source"
                    ),
            }
        )

    selected = []
    seen = set()

    for item in candidates:
        key = (
            item[
                "text"
            ].casefold()
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        selected.append(
            item
        )

        if len(selected) >= 3:
            break

    return selected



def collect(
    config: dict,
    now: datetime,
) -> dict:
    timezone = ZoneInfo(
        config[
            "timezone"
        ]
    )

    if now.tzinfo is None:
        now = now.replace(
            tzinfo=timezone,
        )
    else:
        now = now.astimezone(
            timezone
        )

    vault = Path(
        config[
            "paths"
        ][
            "vault_root"
        ]
    )

    windows = report_windows(
        now
    )

    areas = collect_areas(
        vault,
        config,
    )

    projects = collect_projects(
        vault,
        config,
    )

    tasks = collect_tasks(
        vault,
        config,
    )

    calendar = collect_calendar(
        vault,
        config,
        windows,
    )

    events = event_horizons(
        calendar,
        windows,
    )

    relevance_context = []

    for item in projects.get(
        "items",
        []
    ):
        relevance_context.append(
            str(
                item.get(
                    "name",
                    "",
                )
            )
        )

    for item in tasks.get(
        "items",
        []
    ):
        relevance_context.append(
            str(
                item.get(
                    "text",
                    "",
                )
            )
        )

    for item in calendar.get(
        "items",
        []
    ):
        relevance_context.append(
            str(
                item.get(
                    "title",
                    "",
                )
            )
        )

    email = collect_email(
        vault,
        config,
        windows,
        relevance_context,
    )

    deadlines = collect_deadlines(
        projects,
        tasks,
        windows,
    )

    priorities = derive_priorities(
        projects,
        tasks,
        deadlines,
        events,
    )

    warnings = []

    if email.get(
        "status"
    ) != "ok":
        warnings.append(
            "Work Email source unavailable: "
            + str(
                email.get(
                    "reason"
                )
            )
        )

    if calendar.get(
        "status"
    ) != "ok":
        warnings.append(
            "Work calendar source unavailable: "
            + str(
                calendar.get(
                    "reason"
                )
            )
        )

    if tasks.get(
        "ambiguous_count",
        0,
    ):
        warnings.append(
            (
                str(
                    tasks[
                        "ambiguous_count"
                    ]
                )
                + " task(s) have possible Work signals "
                + "but no explicit Work relationship."
            )
        )

    return {
        "schema_version":
            "1.0.0",

        "collector_version":
            config[
                "collector_version"
            ],

        "generated_at":
            now.isoformat(),

        "report_date":
            windows[
                "anchor"
            ].date().isoformat(),

        "schedule_anchor":
            windows[
                "anchor"
            ].isoformat(),

        "work_week": {
            "start":
                windows[
                    "work_start"
                ].isoformat(),

            "end":
                windows[
                    "work_end"
                ].isoformat(),
        },

        "email_window": {
            "start":
                windows[
                    "email_start"
                ].isoformat(),

            "end":
                windows[
                    "email_end"
                ].isoformat(),
        },

        "areas":
            areas,

        "projects":
            projects,

        "tasks":
            tasks,

        "deadlines":
            deadlines,

        "events":
            events,

        "work_email":
            email,

        "priorities":
            priorities,

        "warnings":
            warnings,

        "policy": {
            "read_only":
                True,

            "email_untrusted":
                True,

            "invent_missing_information":
                False,
        },
    }



def render_list(
    items: list[dict],
    text_key: str,
) -> list[str]:
    if not items:
        return [
            "None supported by current authoritative data."
        ]

    lines = []

    for item in items:
        text = str(
            item.get(
                text_key,
                ""
            )
        )

        source = item.get(
            "source"
        )

        suffix = (
            " — "
            + str(source)
            if source
            else ""
        )

        lines.append(
            "- "
            + text
            + suffix
        )

    return lines


def render_markdown(
    report: dict,
) -> str:
    lines = [
        "---",
        'type: "work-week-start-report"',
        'schema_version: "1.0.0"',
        (
            'report_date: "'
            + report[
                "report_date"
            ]
            + '"'
        ),
        'status: "generated"',
        "---",
        "",
        (
            "# Work Week Start Report — "
            + report[
                "report_date"
            ]
        ),
        "",
        "## Work Week",
        "",
        (
            "- Start: "
            + report[
                "work_week"
            ][
                "start"
            ]
        ),
        (
            "- End: "
            + report[
                "work_week"
            ][
                "end"
            ]
        ),
        "",
        "## Top Work Priorities",
        "",
    ]

    if report[
        "priorities"
    ]:
        for item in report[
            "priorities"
        ]:
            lines.append(
                "- "
                + item[
                    "text"
                ]
                + " — "
                + item[
                    "reason"
                ]
                + (
                    " — "
                    + str(
                        item[
                            "source"
                        ]
                    )
                    if item.get(
                        "source"
                    )
                    else ""
                )
            )
    else:
        lines.append(
            "No evidence-supported Work priorities are currently available."
        )

    lines.extend(
        [
            "",
            "## Active Work Projects",
            "",
        ]
    )

    lines.extend(
        render_list(
            report[
                "projects"
            ].get(
                "items",
                []
            ),
            "name",
        )
    )

    lines.extend(
        [
            "",
            "## Open Work Tasks",
            "",
        ]
    )

    lines.extend(
        render_list(
            report[
                "tasks"
            ].get(
                "items",
                []
            ),
            "text",
        )
    )

    lines.extend(
        [
            "",
            "## Ambiguous Possible Work Tasks",
            "",
        ]
    )

    ambiguous = (
        report[
            "tasks"
        ].get(
            "ambiguous",
            []
        )
    )

    if ambiguous:
        for item in ambiguous:
            lines.append(
                "- REVIEW ONLY: "
                + item[
                    "text"
                ]
                + " — "
                + item[
                    "source"
                ]
            )
    else:
        lines.append(
            "None."
        )

    lines.extend(
        [
            "",
            "## Work-Week Deadlines",
            "",
        ]
    )

    deadlines = (
        report[
            "deadlines"
        ][
            "work_week"
        ]
    )

    if deadlines:
        for item in deadlines:
            lines.append(
                "- "
                + item[
                    "due"
                ]
                + " — "
                + item[
                    "text"
                ]
                + " — "
                + item[
                    "source"
                ]
            )
    else:
        lines.append(
            "None supported by current authoritative data."
        )

    lines.extend(
        [
            "",
            "## Next 7 Days — Deadline Preview",
            "",
        ]
    )

    next_deadlines = (
        report[
            "deadlines"
        ][
            "next_7_days"
        ]
    )

    if next_deadlines:
        for item in next_deadlines:
            lines.append(
                "- "
                + item[
                    "due"
                ]
                + " — "
                + item[
                    "text"
                ]
                + " — "
                + item[
                    "source"
                ]
            )
    else:
        lines.append(
            "None supported by current authoritative data."
        )

    lines.extend(
        [
            "",
            "## Upcoming Work Events",
            "",
        ]
    )

    if (
        report[
            "events"
        ].get(
            "status"
        )
        != "ok"
    ):
        lines.append(
            "Work calendar source unavailable."
        )
    elif report[
        "events"
    ][
        "work_week"
    ]:
        for item in report[
            "events"
        ][
            "work_week"
        ]:
            lines.append(
                "- "
                + item[
                    "start"
                ]
                + " — "
                + item[
                    "title"
                ]
                + (
                    " — "
                    + str(
                        item[
                            "source"
                        ]
                    )
                    if item.get(
                        "source"
                    )
                    else ""
                )
            )
    else:
        lines.append(
            "No Work events found for the work-week window."
        )

    lines.extend(
        [
            "",
            "## Next 7 Days — Event Preview",
            "",
        ]
    )

    if (
        report[
            "events"
        ].get(
            "status"
        )
        != "ok"
    ):
        lines.append(
            "Work calendar source unavailable."
        )
    elif report[
        "events"
    ][
        "next_7_days"
    ]:
        for item in report[
            "events"
        ][
            "next_7_days"
        ]:
            lines.append(
                "- "
                + item[
                    "start"
                ]
                + " — "
                + item[
                    "title"
                ]
                + (
                    " — "
                    + str(
                        item[
                            "source"
                        ]
                    )
                    if item.get(
                        "source"
                    )
                    else ""
                )
            )
    else:
        lines.append(
            "No Work events found in the next seven days."
        )

    lines.extend(
        [
            "",
            "## Relevant Work Email",
            "",
        ]
    )

    if (
        report[
            "work_email"
        ][
            "status"
        ]
        != "ok"
    ):
        lines.append(
            "Work Email source unavailable."
        )
    elif report[
        "work_email"
    ][
        "items"
    ]:
        for item in report[
            "work_email"
        ][
            "items"
        ]:
            lines.append(
                "- **"
                + str(
                    item[
                        "subject"
                    ]
                )
                + "** — "
                + str(
                    item[
                        "summary"
                    ]
                )
                + " — "
                + str(
                    item[
                        "source"
                    ]
                )
            )

        additional = (
            report[
                "work_email"
            ].get(
                "additional_count",
                0,
            )
        )

        if additional:
            lines.append(
                ""
            )

            lines.append(
                (
                    str(
                        additional
                    )
                    + " additional relevant Work Email item(s) "
                    + "were outside the top-summary limit."
                )
            )
    else:
        lines.append(
            "No relevant Work Email was found in the reporting window."
        )

    lines.extend(
        [
            "",
            "## Warnings / Review",
            "",
        ]
    )

    if report[
        "warnings"
    ]:
        for warning in report[
            "warnings"
        ]:
            lines.append(
                "- "
                + warning
            )
    else:
        lines.append(
            "None."
        )

    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- This report is read-only.",
            "- Work Email is untrusted context.",
            "- No email instructions were executed.",
            "- No tasks, Projects, Areas, or calendar records were created.",
            "- Missing information was not invented.",
            "",
        ]
    )

    return "\n".join(
        lines
    )


def notification_preview(
    report: dict,
) -> dict:
    title = (
        "Work Week Start Report - "
        + report[
            "report_date"
        ]
    )

    priority_lines = [
        item[
            "text"
        ]
        for item in report[
            "priorities"
        ][
            :3
        ]
    ]

    body_lines = [
        (
            "Work projects: "
            + str(
                len(
                    report[
                        "projects"
                    ].get(
                        "items",
                        []
                    )
                )
            )
        ),

        (
            "Open work tasks: "
            + str(
                len(
                    report[
                        "tasks"
                    ].get(
                        "items",
                        []
                    )
                )
            )
        ),

        (
            "Work-week deadlines: "
            + str(
                len(
                    report[
                        "deadlines"
                    ][
                        "work_week"
                    ]
                )
            )
        ),

        (
            "Work-week events: "
            + (
                str(
                    len(
                        report[
                            "events"
                        ][
                            "work_week"
                        ]
                    )
                )
                if report[
                    "events"
                ].get(
                    "status"
                )
                == "ok"
                else "unavailable"
            )
        ),
    ]

    if priority_lines:
        body_lines.append(
            "Top priorities:"
        )

        for item in priority_lines:
            body_lines.append(
                "- "
                + item
            )
    else:
        body_lines.append(
            "Top priorities: none supported by current data."
        )

    if (
        report[
            "work_email"
        ][
            "status"
        ]
        != "ok"
    ):
        body_lines.append(
            "Work Email: unavailable"
        )
    else:
        body_lines.append(
            (
                "Relevant Work Email count: "
                + str(
                    report[
                        "work_email"
                    ][
                        "count"
                    ]
                )
            )
        )

    title.encode(
        "ascii"
    )

    body = "\n".join(
        body_lines
    )

    return {
        "status":
            "preview",

        "sent":
            False,

        "title":
            title,

        "body":
            body,
    }



def load_router(
    path: Path,
):
    spec = (
        importlib.util
        .spec_from_file_location(
            "second_brain_notification_router_step32",
            path,
        )
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "Unable to load notification router."
        )

    module = (
        importlib.util
        .module_from_spec(
            spec
        )
    )

    spec.loader.exec_module(
        module
    )

    if not hasattr(
        module,
        "send_ntfy",
    ):
        raise RuntimeError(
            "Notification router does not expose send_ntfy."
        )

    return module


def delivery_state_paths(
    config: dict,
    runtime_root: Path,
) -> tuple[Path, Path]:
    delivery = config[
        "delivery"
    ]

    runtime_root = Path(
        runtime_root
    )

    if (
        runtime_root
        == PRODUCTION_RUNTIME_ROOT
    ):
        return (
            Path(
                delivery[
                    "state_path"
                ]
            ),

            Path(
                delivery[
                    "log_path"
                ]
            ),
        )

    return (
        runtime_root
        / "delivery-state.json",

        runtime_root
        / "delivery-decisions.jsonl",
    )


def load_delivery_state(
    path: Path,
) -> dict:
    if not path.is_file():
        return {
            "schema_version":
                "1.0.0",

            "deliveries":
                {},
        }

    value = load_json(
        path
    )

    if not isinstance(
        value.get(
            "deliveries"
        ),
        dict,
    ):
        raise RuntimeError(
            "Invalid delivery-state schema."
        )

    return value


def payload_sha256(
    title: str,
    body: str,
) -> str:
    return hashlib.sha256(
        (
            title
            + "\n"
            + body
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def artifact_delivery_key(
    artifact: dict,
) -> str:
    base = (
        "work-week-start:"
        + artifact[
            "report_date"
        ]
    )

    revision = artifact.get(
        "revision"
    )

    if revision is None:
        return (
            base
            + ":canonical"
        )

    return (
        base
        + ":revision:"
        + str(
            revision
        )
    )


def deliver_notification(
    config: dict,
    report: dict,
    delivery_key: str,
    send: bool,
    runtime_root: Path,
) -> dict:
    preview = notification_preview(
        report
    )

    title = preview[
        "title"
    ]

    body = preview[
        "body"
    ]

    if (
        "\r" in title
        or "\n" in title
    ):
        raise RuntimeError(
            "Notification title contains a newline."
        )

    title.encode(
        "ascii"
    )

    state_path, log_path = (
        delivery_state_paths(
            config,
            runtime_root,
        )
    )

    state = load_delivery_state(
        state_path
    )

    deliveries = state[
        "deliveries"
    ]

    if delivery_key in deliveries:
        return {
            "status":
                "completed",

            "result":
                "no_changes",

            "reason":
                "idempotent_duplicate",

            "delivery_key":
                delivery_key,

            "sent":
                False,
        }

    delivery = config[
        "delivery"
    ]

    if not delivery.get(
        "enabled"
    ):
        return {
            "status":
                "completed",

            "result":
                "disabled",

            "reason":
                "delivery_disabled",

            "delivery_key":
                delivery_key,

            "sent":
                False,
        }

    if delivery.get(
        "preview_only"
    ):
        send = False

    if not send:
        return {
            "status":
                "completed",

            "result":
                "preview",

            "delivery_key":
                delivery_key,

            "sent":
                False,

            "title":
                title,

            "body":
                body,
        }

    if (
        config.get(
            "mode"
        )
        != "production"
    ):
        raise RuntimeError(
            "Real notification delivery requires production mode."
        )

    router_path = Path(
        delivery[
            "router_script"
        ]
    )

    router_config_path = Path(
        delivery[
            "router_config"
        ]
    )

    if not router_path.is_file():
        raise RuntimeError(
            "Notification router script missing."
        )

    if not router_config_path.is_file():
        raise RuntimeError(
            "Notification router config missing."
        )

    router_config = load_json(
        router_config_path
    )

    ntfy_url = str(
        router_config.get(
            "ntfy_url",
            "",
        )
    )

    if not ntfy_url.startswith(
        "https://"
    ):
        raise RuntimeError(
            "Notification router target is not HTTPS."
        )

    router = load_router(
        router_path
    )

    digest = payload_sha256(
        title,
        body,
    )

    attempt = {
        "delivery_key":
            delivery_key,

        "payload_sha256":
            digest,

        "attempted_at":
            datetime.now(
                ZoneInfo(
                    "UTC"
                )
            ).isoformat(),

        "sent":
            False,
    }

    try:
        status = int(
            router.send_ntfy(
                router_config,
                title,
                body,
                str(
                    delivery.get(
                        "priority",
                        "default",
                    )
                ),
                str(
                    delivery.get(
                        "tags",
                        "briefcase",
                    )
                ),
            )
        )

        if not (
            200
            <= status
            < 300
        ):
            raise RuntimeError(
                "Notification transport HTTP status "
                + str(
                    status
                )
            )

        record = {
            "delivery_key":
                delivery_key,

            "delivered_at":
                datetime.now(
                    ZoneInfo(
                        "UTC"
                    )
                ).isoformat(),

            "http_status":
                status,

            "payload_sha256":
                digest,
        }

        deliveries[
            delivery_key
        ] = record

        atomic_json(
            state_path,
            state,
        )

        attempt[
            "sent"
        ] = True

        attempt[
            "http_status"
        ] = status

        append_jsonl(
            log_path,
            attempt,
        )

        return {
            "status":
                "completed",

            "result":
                "sent",

            "delivery_key":
                delivery_key,

            "sent":
                True,

            "http_status":
                status,
        }

    except Exception as exc:
        attempt[
            "error_type"
        ] = type(
            exc
        ).__name__

        append_jsonl(
            log_path,
            attempt,
        )

        raise

def material_content(
    report: dict,
) -> dict:
    return {
        "projects":
            report[
                "projects"
            ].get(
                "items",
                []
            ),

        "tasks":
            report[
                "tasks"
            ].get(
                "items",
                []
            ),

        "ambiguous_tasks":
            report[
                "tasks"
            ].get(
                "ambiguous",
                []
            ),

        "deadlines":
            report[
                "deadlines"
            ],

        "events":
            report[
                "events"
            ],

        "work_email":
            report[
                "work_email"
            ],

        "priorities":
            report[
                "priorities"
            ],
    }


def latest_snapshot(
    snapshots: Path,
    report_date: str,
) -> Path | None:
    matches = sorted(
        snapshots.glob(
            report_date
            + " Work Week Start Report*.json"
        )
    )

    return (
        matches[-1]
        if matches
        else None
    )


def next_revision(
    snapshots: Path,
    report_date: str,
) -> int:
    highest = 1

    pattern = re.compile(
        re.escape(
            report_date
        )
        + r" Work Week Start Report - Revision (\d+)\.json$"
    )

    for path in snapshots.glob(
        report_date
        + " Work Week Start Report*.json"
    ):
        match = pattern.match(
            path.name
        )

        if match:
            highest = max(
                highest,
                int(
                    match.group(1)
                ),
            )

    return highest + 1



def artifact_directories(
    config: dict,
    runtime_root: Path,
) -> tuple[Path, Path]:
    runtime_root = Path(
        runtime_root
    )

    if (
        runtime_root
        == PRODUCTION_RUNTIME_ROOT
    ):
        reports = (
            Path(
                config[
                    "paths"
                ][
                    "vault_root"
                ]
            )
            / config[
                "persistence"
            ][
                "vault_directory"
            ]
        )

        snapshots = Path(
            config[
                "persistence"
            ][
                "structured_snapshot_directory"
            ]
        )

        return (
            reports,
            snapshots,
        )

    return (
        runtime_root
        / "reports",

        runtime_root
        / "snapshots",
    )


def create_artifact(
    config: dict,
    report: dict,
    runtime_root: Path,
    run_kind: str,
) -> dict:
    reports, snapshots = (
        artifact_directories(
            config,
            runtime_root,
        )
    )

    reports.mkdir(
        parents=True,
        exist_ok=True,
    )

    snapshots.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_date = (
        report[
            "report_date"
        ]
    )

    canonical_snapshot = (
        snapshots
        / (
            report_date
            + " Work Week Start Report.json"
        )
    )

    previous = latest_snapshot(
        snapshots,
        report_date,
    )

    if not canonical_snapshot.exists():
        revision = None

        md_path = (
            reports
            / (
                report_date
                + " Work Week Start Report.md"
            )
        )

        json_path = (
            canonical_snapshot
        )

    else:
        revision = next_revision(
            snapshots,
            report_date,
        )

        md_path = (
            reports
            / (
                report_date
                + " Work Week Start Report - Revision "
                + str(
                    revision
                )
                + ".md"
            )
        )

        json_path = (
            snapshots
            / (
                report_date
                + " Work Week Start Report - Revision "
                + str(
                    revision
                )
                + ".json"
            )
        )

    report = copy.deepcopy(
        report
    )

    report[
        "revision"
    ] = revision

    report[
        "run_kind"
    ] = run_kind

    materially_changed = True

    if previous is not None:
        previous_data = load_json(
            previous
        )

        materially_changed = (
            material_content(
                previous_data
            )
            != material_content(
                report
            )
        )

    report[
        "materially_changed"
    ] = materially_changed

    atomic_text(
        md_path,
        render_markdown(
            report
        ),
    )

    atomic_json(
        json_path,
        report,
    )

    return {
        "report_date":
            report_date,

        "revision":
            revision,

        "materially_changed":
            materially_changed,

        "markdown":
            str(
                md_path
            ),

        "snapshot":
            str(
                json_path
            ),
    }




def manual_run(
    config: dict,
    now: datetime,
    runtime_root: Path,
) -> dict:
    report = collect(
        config,
        now,
    )

    artifact = create_artifact(
        config,
        report,
        runtime_root,
        "manual",
    )

    preview = notification_preview(
        report
    )

    if not config[
        "delivery"
    ].get(
        "enabled"
    ):
        notification = {
            "status":
                "completed",

            "result":
                "disabled",

            "reason":
                "delivery_disabled",

            "sent":
                False,
        }

    elif (
        artifact.get(
            "revision"
        )
        is not None
        and artifact.get(
            "materially_changed"
        )
        is False
    ):
        notification = {
            "status":
                "completed",

            "result":
                "suppressed",

            "reason":
                "unchanged_revision",

            "sent":
                False,
        }

    else:
        notification = (
            deliver_notification(
                config,
                report,
                artifact_delivery_key(
                    artifact
                ),
                True,
                runtime_root,
            )
        )

    return {
        "status":
            "completed",

        "command":
            "manual",

        "artifact":
            artifact,

        "notification_preview":
            preview,

        "notification":
            notification,
    }



def scheduled_target(
    now: datetime,
) -> dict:
    windows = report_windows(
        now
    )

    target = windows[
        "anchor"
    ]

    deadline = windows[
        "work_end"
    ]

    if now < target:
        return {
            "status":
                "not_due",
        }

    if now <= deadline:
        return {
            "status":
                "due",

            "target_date":
                target.date().isoformat(),

            "catchup":
                now > (
                    target
                    + timedelta(
                        minutes=5
                    )
                ),
        }

    return {
        "status":
            "expired",

        "target_date":
            target.date().isoformat(),
    }


def load_state(
    path: Path,
) -> dict:
    if not path.is_file():
        return {
            "schema_version":
                "1.0.0",

            "scheduled_completed":
                [],

            "scheduled_expired":
                [],
        }

    return load_json(
        path
    )



def scheduled_run(
    config: dict,
    now: datetime,
    runtime_root: Path,
) -> dict:
    state_path = (
        runtime_root
        / "runner-state.json"
    )

    state = load_state(
        state_path
    )

    decision = scheduled_target(
        now
    )

    if (
        decision[
            "status"
        ]
        == "not_due"
    ):
        return {
            "status":
                "not_due",

            "decision":
                decision,
        }

    target = decision.get(
        "target_date"
    )

    if (
        target
        in state[
            "scheduled_completed"
        ]
    ):
        return {
            "status":
                "already_completed",

            "decision":
                decision,
        }

    if (
        decision[
            "status"
        ]
        == "expired"
    ):
        if (
            target
            not in state[
                "scheduled_expired"
            ]
        ):
            state[
                "scheduled_expired"
            ].append(
                target
            )

            atomic_json(
                state_path,
                state,
            )

        return {
            "status":
                "expired",

            "decision":
                decision,
        }

    report = collect(
        config,
        now,
    )

    artifact = create_artifact(
        config,
        report,
        runtime_root,
        (
            "scheduled_catchup"
            if decision[
                "catchup"
            ]
            else "scheduled"
        ),
    )

    preview = notification_preview(
        report
    )

    if not config[
        "delivery"
    ].get(
        "enabled"
    ):
        notification = {
            "status":
                "completed",

            "result":
                "disabled",

            "reason":
                "delivery_disabled",

            "sent":
                False,
        }

    elif (
        artifact.get(
            "revision"
        )
        is not None
        and artifact.get(
            "materially_changed"
        )
        is False
    ):
        notification = {
            "status":
                "completed",

            "result":
                "suppressed",

            "reason":
                "unchanged_revision",

            "sent":
                False,
        }

    else:
        # Delivery is attempted before scheduled completion is
        # committed. A transport failure therefore cannot mark
        # an undelivered scheduled report as fully complete.
        notification = (
            deliver_notification(
                config,
                report,
                artifact_delivery_key(
                    artifact
                ),
                True,
                runtime_root,
            )
        )

    state[
        "scheduled_completed"
    ].append(
        target
    )

    atomic_json(
        state_path,
        state,
    )

    return {
        "status":
            "completed",

        "decision":
            decision,

        "artifact":
            artifact,

        "notification_preview":
            preview,

        "notification":
            notification,
    }




def validate_config(
    config: dict,
):
    required = {
        "schema_version",
        "collector_version",
        "mode",
        "timezone",
        "schedule",
        "work_week",
        "paths",
        "work_email",
        "calendar",
        "report",
        "delivery",
        "persistence",
        "policy",
        "catchup",
    }

    missing = (
        required
        - set(
            config
        )
    )

    if missing:
        raise RuntimeError(
            "Missing config keys: "
            + repr(
                sorted(
                    missing
                )
            )
        )

    if config[
        "mode"
    ] not in {
        "staging",
        "production",
    }:
        raise RuntimeError(
            "Invalid execution mode."
        )

    if (
        config[
            "timezone"
        ]
        != "America/Kentucky/Louisville"
    ):
        raise RuntimeError(
            "Wrong timezone."
        )

    if (
        config[
            "schedule"
        ][
            "day"
        ]
        != "Friday"
    ):
        raise RuntimeError(
            "Wrong schedule day."
        )

    if (
        config[
            "schedule"
        ][
            "local_time"
        ]
        != "16:00"
    ):
        raise RuntimeError(
            "Wrong schedule time."
        )

    policy = config[
        "policy"
    ]

    if (
        policy.get(
            "read_only"
        )
        is not True
    ):
        raise RuntimeError(
            "Report must remain read-only."
        )

    for key in (
        "create_tasks",
        "modify_tasks",
        "modify_projects",
        "modify_areas",
        "modify_source_notes",
        "invent_missing_information",
    ):
        if (
            policy[
                key
            ]
            is not False
        ):
            raise RuntimeError(
                "Unsafe policy: "
                + key
            )

    delivery = config[
        "delivery"
    ]

    if delivery.get(
        "enabled"
    ):
        if (
            config[
                "mode"
            ]
            != "production"
        ):
            raise RuntimeError(
                "Delivery may only be enabled in production mode."
            )

        required_delivery = (
            "channel",
            "router_script",
            "router_config",
            "priority",
            "tags",
            "state_path",
            "log_path",
        )

        for key in required_delivery:
            if not str(
                delivery.get(
                    key,
                    "",
                )
            ).strip():
                raise RuntimeError(
                    "Missing production delivery field: "
                    + key
                )

        if (
            delivery[
                "channel"
            ]
            != "existing_notification_router_transport"
        ):
            raise RuntimeError(
                "Unexpected notification transport."
            )

        if (
            delivery.get(
                "privacy"
            )
            != "minimal"
        ):
            raise RuntimeError(
                "Phone privacy mode must remain minimal."
            )

        for key in (
            "include_email_subjects",
            "include_email_body",
            "include_sensitive_details",
        ):
            if (
                delivery.get(
                    key
                )
                is not False
            ):
                raise RuntimeError(
                    "Unsafe delivery privacy field: "
                    + key
                )

    elif (
        config[
            "mode"
        ]
        == "staging"
        and delivery.get(
            "enabled"
        )
        is not False
    ):
        raise RuntimeError(
            "Staging delivery must remain disabled."
        )



def self_test():
    tz = ZoneInfo(
        "America/Kentucky/Louisville"
    )

    friday = datetime(
        2026,
        8,
        14,
        16,
        0,
        tzinfo=tz,
    )

    windows = report_windows(
        friday
    )

    assert (
        windows[
            "work_start"
        ].isoformat()
        == "2026-08-14T19:00:00-04:00"
    )

    assert (
        windows[
            "work_end"
        ].isoformat()
        == "2026-08-17T07:00:00-04:00"
    )

    sunday = datetime(
        2026,
        8,
        16,
        12,
        0,
        tzinfo=tz,
    )

    decision = scheduled_target(
        sunday
    )

    assert (
        decision[
            "status"
        ]
        == "due"
    )

    assert (
        decision[
            "catchup"
        ]
        is True
    )

    monday_late = datetime(
        2026,
        8,
        17,
        7,
        1,
        tzinfo=tz,
    )

    assert (
        scheduled_target(
            monday_late
        )[
            "status"
        ]
        == "expired"
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


# STEP32_CLI_V3

PRODUCTION_CONFIG = Path(
    "/AI/Config/Second Brain/"
    "Work Week Start Report/config.json"
)

PRODUCTION_RUNTIME_ROOT = Path(
    "/AI/State/Second Brain/"
    "Work Week Start Report"
)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "self-test",
            "collect",
            "preview",
            "manual",
            "scheduled",
        ],
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=PRODUCTION_CONFIG,
    )

    parser.add_argument(
        "--output",
        type=Path,
    )

    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=PRODUCTION_RUNTIME_ROOT,
    )

    args = parser.parse_args()

    if args.command == "self-test":
        self_test()
        return

    if not args.config.is_file():
        raise RuntimeError(
            "Config file missing: "
            + str(
                args.config
            )
        )

    config = load_json(
        args.config
    )

    validate_config(
        config
    )

    now = datetime.now(
        ZoneInfo(
            config[
                "timezone"
            ]
        )
    )

    if args.command == "manual":
        result = manual_run(
            config,
            now,
            args.runtime_root,
        )

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )

        return

    if args.command == "scheduled":
        result = scheduled_run(
            config,
            now,
            args.runtime_root,
        )

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            )
        )

        return

    report = collect(
        config,
        now,
    )

    if args.command == "collect":
        if args.output:
            atomic_json(
                args.output,
                report,
            )

        print(
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
            )
        )

        return

    print(
        json.dumps(
            notification_preview(
                report
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

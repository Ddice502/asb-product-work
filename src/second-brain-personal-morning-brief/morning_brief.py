#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from morning_brief_external import (
    collect_external,
    render_external_markdown,
)


CONFIG = Path(
    "/AI/Config/Second Brain/"
    "Personal Morning Brief/config.json"
)

UNCHECKED_RE = re.compile(
    r"(?m)^\s*-\s*\[\s\]\s+(.+?)\s*$"
)

CHECKED_RE = re.compile(
    r"(?m)^\s*-\s*\[[xX]\]\s+(.+?)\s*$"
)

TASK_ID_RE = re.compile(
    r"\s*<!--\s*task-id:[^>]+-->\s*$"
)

SHOPPING_ID_RE = re.compile(
    r"\s*<!--\s*shopping-id:[^>]+-->\s*$"
)

DATED_HEALTH_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}_pipeline-health\.md$"
)


def sha256_bytes(
    data: bytes,
) -> str:
    return hashlib.sha256(
        data
    ).hexdigest()


def read_text(
    path: Path,
) -> str:
    return path.read_text(
        encoding="utf-8",
        errors="replace",
    )


def file_source(
    vault: Path,
    path: Path,
) -> dict[str, Any]:
    data = path.read_bytes()

    relative = str(
        path.relative_to(
            vault
        )
    )

    stat = path.stat()

    return {
        "path":
            relative,

        "obsidian_link":
            f"[[{relative[:-3] if relative.endswith('.md') else relative}]]",

        "sha256":
            sha256_bytes(
                data
            ),

        "modified_at_utc":
            datetime.fromtimestamp(
                stat.st_mtime,
                timezone.utc,
            ).isoformat(),
    }


def strip_record_marker(
    text: str,
) -> str:
    text = TASK_ID_RE.sub(
        "",
        text,
    )

    text = SHOPPING_ID_RE.sub(
        "",
        text,
    )

    return text.strip()


def unchecked_items(
    text: str,
) -> list[str]:
    return [
        strip_record_marker(
            match
        )
        for match in UNCHECKED_RE.findall(
            text
        )
    ]


def checked_items(
    text: str,
) -> list[str]:
    return [
        strip_record_marker(
            match
        )
        for match in CHECKED_RE.findall(
            text
        )
    ]


def parse_frontmatter(
    text: str,
) -> dict[str, str]:
    if not text.startswith(
        "---\n"
    ):
        return {}

    end = text.find(
        "\n---",
        4,
    )

    if end < 0:
        return {}

    result: dict[str, str] = {}

    for line in text[
        4:end
    ].splitlines():

        if ":" not in line:
            continue

        key, value = line.split(
            ":",
            1,
        )

        result[
            key.strip()
        ] = value.strip().strip(
            "\"'"
        )

    return result


def count_issue_section(
    text: str,
) -> int | None:
    match = re.search(
        r"(?ms)^##\s+Issues\s*$"
        r"(.*?)"
        r"(?=^##\s+|\Z)",
        text,
    )

    if not match:
        return None

    body = match.group(
        1
    )

    return len(
        re.findall(
            r"(?m)^\s*-\s+\S.+$",
            body,
        )
    )


def health_issue_count(
    text: str,
) -> int | None:
    frontmatter = parse_frontmatter(
        text
    )

    raw = frontmatter.get(
        "issue_count"
    )

    if raw is not None:
        try:
            return int(
                raw
            )
        except ValueError:
            pass

    match = re.search(
        r"(?im)^\s*issue[_ ]count\s*[:=]\s*(\d+)\s*$",
        text,
    )

    if match:
        return int(
            match.group(
                1
            )
        )

    return count_issue_section(
        text
    )


def latest_dated_health(
    directory: Path,
) -> Path:
    candidates = [
        path
        for path in directory.iterdir()
        if (
            path.is_file()
            and DATED_HEALTH_RE.match(
                path.name
            )
        )
    ]

    if not candidates:
        raise RuntimeError(
            "No dated pipeline-health report found."
        )

    candidates.sort(
        key=lambda path: path.name
    )

    return candidates[-1]


def current_position(
    text: str,
) -> dict[str, Any]:
    phase_match = re.search(
        r"(?m)^roadmap_phase:\s*(\d+)\s*$",
        text,
    )

    step_match = re.search(
        r"(?m)^roadmap_step:\s*(\d+)\s*$",
        text,
    )

    visible_match = re.search(
        r"(?m)^- \*\*Current step:\*\*\s*(.+?)\s*$",
        text,
    )

    return {
        "roadmap_phase":
            int(
                phase_match.group(
                    1
                )
            )
            if phase_match
            else None,

        "roadmap_step":
            int(
                step_match.group(
                    1
                )
            )
            if step_match
            else None,

        "current_step":
            visible_match.group(
                1
            ).strip()
            if visible_match
            else None,
    }


def collect_review_source(
    vault: Path,
    source: dict[str, Any],
) -> dict[str, Any]:
    kind = source[
        "kind"
    ]

    target = (
        vault
        / source[
            "path"
        ]
    )

    base = {
        "id":
            source[
                "id"
            ],

        "label":
            source[
                "label"
            ],

        "kind":
            kind,

        "configured_path":
            source[
                "path"
            ],
    }

    if kind == "file":
        if not target.is_file():
            return {
                **base,
                "status":
                    "missing",

                "open_count":
                    None,
            }

        text = read_text(
            target
        )

        return {
            **base,
            "status":
                "ok",

            "open_count":
                len(
                    unchecked_items(
                        text
                    )
                ),

            "source":
                file_source(
                    vault,
                    target,
                ),
        }

    if kind == "latest_file":
        if not target.is_dir():
            return {
                **base,
                "status":
                    "missing",

                "open_count":
                    None,
            }

        pattern = source.get(
            "glob",
            "*.md",
        )

        candidates = [
            path
            for path in target.glob(
                pattern
            )
            if path.is_file()
        ]

        if not candidates:
            return {
                **base,
                "status":
                    "empty",

                "open_count":
                    0,
            }

        candidates.sort(
            key=lambda path: (
                path.stat().st_mtime_ns,
                path.name,
            ),
            reverse=True,
        )

        selected = candidates[0]

        text = read_text(
            selected
        )

        return {
            **base,
            "status":
                "ok",

            "open_count":
                len(
                    unchecked_items(
                        text
                    )
                ),

            "source":
                file_source(
                    vault,
                    selected,
                ),
        }

    if kind == "directory_count":
        if not target.is_dir():
            return {
                **base,
                "status":
                    "missing",

                "open_count":
                    None,
            }

        pattern = source.get(
            "glob",
            "*.md",
        )

        files = sorted(
            [
                path
                for path in target.glob(
                    pattern
                )
                if path.is_file()
            ],
            key=lambda path: path.name,
        )

        return {
            **base,
            "status":
                "ok",

            "open_count":
                len(
                    files
                ),

            "files":
                [
                    file_source(
                        vault,
                        path,
                    )
                    for path in files
                ],
        }

    raise RuntimeError(
        f"Unsupported review source kind: {kind}"
    )



def validate_config(
    config: dict[str, Any],
) -> None:
    if (
        config.get(
            "schema_version"
        )
        != "1.0.0"
    ):
        raise RuntimeError(
            "Unsupported config schema."
        )

    if (
        config.get(
            "collector_version"
        )
        != "personal-morning-brief-collector-0.3.0"
    ):
        raise RuntimeError(
            "Unexpected collector version."
        )

    if (
        config.get(
            "mode"
        )
        not in {
            "dry_run",
            "production",
        }
    ):
        raise RuntimeError(
            "Unsupported collector mode."
        )

    if (
        config.get(
            "timezone"
        )
        != "America/Kentucky/Louisville"
    ):
        raise RuntimeError(
            "Morning Brief timezone is not Louisville."
        )

    schedule = (
        config.get(
            "schedule"
        )
        or {}
    )

    if (
        schedule.get(
            "days"
        )
        != [
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
        ]
    ):
        raise RuntimeError(
            "Morning Brief schedule days are invalid."
        )

    if (
        schedule.get(
            "local_time"
        )
        != "07:00"
    ):
        raise RuntimeError(
            "Morning Brief local time is invalid."
        )

    policy = (
        config.get(
            "policy"
        )
        or {}
    )

    required_false = (
        "create_tasks",
        "modify_tasks",
        "modify_shopping",
        "modify_source_notes",
        "invent_missing_information",
    )

    if (
        policy.get(
            "read_only"
        )
        is not True
    ):
        raise RuntimeError(
            "Collector is not configured read-only."
        )

    for key in required_false:
        if (
            policy.get(
                key
            )
            is not False
        ):
            raise RuntimeError(
                "Unsafe policy value: "
                + key
            )

    delivery_enabled = (
        config.get(
            "delivery",
            {}
        ).get(
            "enabled"
        )
    )

    if not isinstance(
        delivery_enabled,
        bool,
    ):
        raise RuntimeError(
            "Delivery enabled flag must be boolean."
        )

    if (
        config.get(
            "mode"
        )
        == "dry_run"
        and delivery_enabled
    ):
        raise RuntimeError(
            "Dry-run mode cannot enable production delivery."
        )

    external_sources = (
        config.get(
            "external_sources"
        )
        or {}
    )

    for name in (
        "weather",
        "events",
        "news",
        "reddit",
    ):
        source = (
            external_sources.get(
                name
            )
            or {}
        )

        if (
            source.get(
                "enabled"
            )
            is not True
        ):
            raise RuntimeError(
                "External source is not enabled: "
                + name
            )

    if (
        config.get(
            "mode"
        )
        == "production"
    ):
        for name in (
            "weather",
            "events",
        ):
            location = (
                external_sources.get(
                    name,
                    {}
                ).get(
                    "location"
                )
            )

            if (
                location
                != (
                    "Louisville, Kentucky "
                    "(city reference point)"
                )
            ):
                raise RuntimeError(
                    "Production location label is invalid: "
                    + name
                )




def collect(
    config: dict[str, Any],
) -> dict[str, Any]:
    validate_config(
        config
    )

    vault = Path(
        config[
            "paths"
        ][
            "vault"
        ]
    )

    timezone_name = (
        config[
            "timezone"
        ]
    )

    local_tz = ZoneInfo(
        timezone_name
    )

    now_utc = datetime.now(
        timezone.utc
    )

    now_local = (
        now_utc.astimezone(
            local_tz
        )
    )

    paths = config[
        "paths"
    ]

    current_status_path = (
        vault
        / paths[
            "current_status"
        ]
    )

    task_path = (
        vault
        / paths[
            "task_queue"
        ]
    )

    shopping_path = (
        vault
        / paths[
            "shopping_list"
        ]
    )

    health_directory = (
        vault
        / paths[
            "health_directory"
        ]
    )

    document_health_path = (
        vault
        / paths[
            "document_health"
        ]
    )

    current_state = {
        "status":
            "missing",

        "roadmap_phase":
            None,

        "roadmap_step":
            None,

        "current_step":
            None,

        "source":
            None,

        "errors":
            [],
    }

    if current_status_path.is_file():
        try:
            current_text = read_text(
                current_status_path
            )

            parsed_current = (
                current_position(
                    current_text
                )
            )

            current_state.update(
                {
                    **parsed_current,

                    "status":
                        "ok",

                    "source":
                        file_source(
                            vault,
                            current_status_path,
                        ),
                }
            )

        except Exception as exc:
            current_state[
                "status"
            ] = "error"

            current_state[
                "errors"
            ].append(
                type(exc).__name__
                + ": "
                + str(exc)
            )

    tasks = {
        "status":
            "missing",

        "open_count":
            None,

        "items":
            [],

        "source":
            None,

        "errors":
            [],
    }

    if task_path.is_file():
        try:
            task_text = read_text(
                task_path
            )

            task_items = (
                unchecked_items(
                    task_text
                )
            )

            tasks.update(
                {
                    "status":
                        "ok",

                    "open_count":
                        len(
                            task_items
                        ),

                    "items":
                        task_items,

                    "source":
                        file_source(
                            vault,
                            task_path,
                        ),
                }
            )

        except Exception as exc:
            tasks[
                "status"
            ] = "error"

            tasks[
                "errors"
            ].append(
                type(exc).__name__
                + ": "
                + str(exc)
            )

    shopping = {
        "status":
            "missing",

        "open_count":
            None,

        "items":
            [],

        "source":
            None,

        "errors":
            [],
    }

    if shopping_path.is_file():
        try:
            shopping_text = read_text(
                shopping_path
            )

            shopping_items = (
                unchecked_items(
                    shopping_text
                )
            )

            shopping.update(
                {
                    "status":
                        "ok",

                    "open_count":
                        len(
                            shopping_items
                        ),

                    "items":
                        shopping_items,

                    "source":
                        file_source(
                            vault,
                            shopping_path,
                        ),
                }
            )

        except Exception as exc:
            shopping[
                "status"
            ] = "error"

            shopping[
                "errors"
            ].append(
                type(exc).__name__
                + ": "
                + str(exc)
            )

    general_health = {
        "status":
            "missing",

        "issue_count":
            None,

        "source":
            None,

        "errors":
            [],
    }

    if health_directory.is_dir():
        try:
            latest_health_path = (
                latest_dated_health(
                    health_directory
                )
            )

            latest_health_text = (
                read_text(
                    latest_health_path
                )
            )

            general_health.update(
                {
                    "status":
                        "ok",

                    "issue_count":
                        health_issue_count(
                            latest_health_text
                        ),

                    "source":
                        file_source(
                            vault,
                            latest_health_path,
                        ),
                }
            )

        except Exception as exc:
            general_health[
                "status"
            ] = "error"

            general_health[
                "errors"
            ].append(
                type(exc).__name__
                + ": "
                + str(exc)
            )

    document_health = {
        "status":
            "missing",

        "source":
            None,

        "errors":
            [],
    }

    if document_health_path.is_file():
        try:
            document_health.update(
                {
                    "status":
                        "ok",

                    "source":
                        file_source(
                            vault,
                            document_health_path,
                        ),
                }
            )

        except Exception as exc:
            document_health[
                "status"
            ] = "error"

            document_health[
                "errors"
            ].append(
                type(exc).__name__
                + ": "
                + str(exc)
            )

    reviews = []

    for source in config[
        "review_sources"
    ]:
        try:
            reviews.append(
                collect_review_source(
                    vault,
                    source,
                )
            )

        except Exception as exc:
            reviews.append(
                {
                    "id":
                        source.get(
                            "id"
                        ),

                    "label":
                        source.get(
                            "label"
                        ),

                    "kind":
                        source.get(
                            "kind"
                        ),

                    "configured_path":
                        source.get(
                            "path"
                        ),

                    "status":
                        "error",

                    "open_count":
                        None,

                    "errors":
                        [
                            type(exc).__name__
                            + ": "
                            + str(exc)
                        ],
                }
            )

    external = collect_external(
        config[
            "external_sources"
        ],
        now_local,
    )

    return {
        "schema_version":
            "1.0.0",

        "collector_version":
            config[
                "collector_version"
            ],

        "mode":
            config[
                "mode"
            ],

        "generated_at_utc":
            now_utc.isoformat(),

        "generated_at_local":
            now_local.isoformat(),

        "timezone":
            timezone_name,

        "schedule":
            config[
                "schedule"
            ],

        "current_state":
            current_state,

        "tasks":
            tasks,

        "shopping":
            shopping,

        "health": {
            "general":
                general_health,

            "document_pipeline":
                document_health,
        },

        "reviews":
            reviews,

        "external":
            external,

        "delivery": {
            "status":
                (
                    "enabled_production"
                    if config.get(
                        "delivery",
                        {}
                    ).get(
                        "enabled"
                    )
                    is True
                    else "disabled"
                ),

            "channel":
                config[
                    "delivery"
                ][
                    "channel"
                ],
        },

        "safety": {
            "read_only":
                True,

            "task_creation":
                False,

            "task_modification":
                False,

            "shopping_modification":
                False,

            "source_note_modification":
                False,
        },
    }



def render_markdown(
    report: dict[str, Any],
) -> str:
    lines: list[str] = [
        "---",
        'type: "personal-morning-brief-dry-run"',
        'schema_version: "1.0.0"',
        f'generated_at_utc: "{report["generated_at_utc"]}"',
        f'generated_at_local: "{report["generated_at_local"]}"',
        f'timezone: "{report["timezone"]}"',
        "production_delivery: false",
        "---",
        "",
        "# Personal Morning Brief — Step 31C Dry Run",
        "",
        "> Deterministic local and external source collection.",
        "> External sources are enabled; notifications and scheduling remain disabled.",
        "",
        "## Current Second Brain State",
        "",
        (
            f"- Phase: "
            f"{report['current_state']['roadmap_phase']}"
        ),
        (
            f"- Step: "
            f"{report['current_state']['current_step']}"
        ),
        (
            f"- Source: "
            f"{report['current_state']['source']['obsidian_link']}"
        ),
        "",
        "## Current Tasks",
        "",
    ]

    task_items = report[
        "tasks"
    ][
        "items"
    ]

    if task_items:
        for item in task_items:
            lines.append(
                f"- [ ] {item}"
            )
    else:
        lines.append(
            "- No open canonical tasks."
        )

    lines.extend(
        [
            "",
            (
                "- Source: "
                + report[
                    "tasks"
                ][
                    "source"
                ][
                    "obsidian_link"
                ]
            ),
            "",
            "## Shopping",
            "",
        ]
    )

    shopping_items = report[
        "shopping"
    ][
        "items"
    ]

    if shopping_items:
        for item in shopping_items:
            lines.append(
                f"- [ ] {item}"
            )
    else:
        lines.append(
            "- No active shopping items."
        )

    lines.extend(
        [
            "",
            (
                "- Source: "
                + report[
                    "shopping"
                ][
                    "source"
                ][
                    "obsidian_link"
                ]
            ),
            "",
            "## Second Brain Health",
            "",
        ]
    )

    health = report[
        "health"
    ][
        "general"
    ]

    issue_count = health.get(
        "issue_count"
    )

    if issue_count is None:
        lines.append(
            "- General health issue count: not deterministically parsed."
        )
    else:
        lines.append(
            f"- General health issue count: {issue_count}"
        )

    lines.append(
        "- General health source: "
        + health[
            "source"
        ][
            "obsidian_link"
        ]
    )

    document = report[
        "health"
    ][
        "document_pipeline"
    ]

    if (
        document.get(
            "status"
        )
        == "ok"
    ):
        lines.append(
            "- Document health source: "
            + document[
                "source"
            ][
                "obsidian_link"
            ]
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

    for review in report[
        "reviews"
    ]:
        count = review.get(
            "open_count"
        )

        status = review.get(
            "status"
        )

        if count is None:
            rendered_count = (
                "unavailable"
            )
        else:
            rendered_count = str(
                count
            )

        line = (
            f"- **{review['label']}**: "
            f"{rendered_count} open "
            f"(status: {status})"
        )

        source = review.get(
            "source"
        )

        if source:
            line += (
                " — "
                + source[
                    "obsidian_link"
                ]
            )

        lines.append(
            line
        )

    lines.extend(
        [
            "",
            "## External Information",
            "",
            "- Weather: not implemented in Step 31B.",
            "- Local events: not implemented in Step 31B.",
            "- News: not implemented in Step 31B.",
            "- Reddit: not implemented in Step 31B.",
            "",
            "## Delivery",
            "",
            "- Notification delivery: disabled in Step 31B.",
            "- Production scheduler: not installed in Step 31B.",
            "",
            "## Safety",
            "",
            "- This collector is read-only.",
            "- No tasks were created or modified.",
            "- No shopping items were created or modified.",
            "- No source notes were modified.",
            "- Missing information was not invented.",
            "",
        ]
    )

    rendered = "\n".join(
        lines
    )

    placeholder = """## External Information

- Weather: not implemented in Step 31B.
- Local events: not implemented in Step 31B.
- News: not implemented in Step 31B.
- Reddit: not implemented in Step 31B."""

    if placeholder not in rendered:
        raise RuntimeError(
            "Step 31B rendered external placeholder is missing."
        )

    rendered = rendered.replace(
        placeholder,
        render_external_markdown(
            report
        ),
        1,
    )

    return rendered


def write_outputs(
    report: dict[str, Any],
    output_dir: Path,
) -> None:
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    json_path = (
        output_dir
        / "morning-brief.json"
    )

    markdown_path = (
        output_dir
        / "morning-brief.md"
    )

    json_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    markdown_path.write_text(
        render_markdown(
            report
        ),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status":
                    "completed",

                "mode":
                    report[
                        "mode"
                    ],

                "output_json":
                    str(
                        json_path
                    ),

                "output_markdown":
                    str(
                        markdown_path
                    ),

                "open_tasks":
                    report[
                        "tasks"
                    ][
                        "open_count"
                    ],

                "shopping_items":
                    report[
                        "shopping"
                    ][
                        "open_count"
                    ],

                "general_health_issues":
                    report[
                        "health"
                    ][
                        "general"
                    ][
                        "issue_count"
                    ],

                "review_sources":
                    len(
                        report[
                            "reviews"
                        ]
                    ),
            },
            ensure_ascii=False,
        )
    )



def self_test() -> None:
    if (
        unchecked_items(
            "- [ ] Alpha\n- [x] Beta\n"
        )
        != [
            "Alpha"
        ]
    ):
        raise RuntimeError(
            "Task parsing self-test failed."
        )

    import morning_brief_external as external_module

    external_module.external_self_test()

    print(
        json.dumps(
            {
                "status":
                    "completed",

                "self_test":
                    "pass",

                "collector_version":
                    (
                        "personal-morning-brief-"
                        "collector-0.3.0"
                    ),
            }
        )
    )



def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "command",
        choices=[
            "self-test",
            "collect",
        ],
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
    )

    args = parser.parse_args()

    if args.command == "self-test":
        self_test()
        return

    if args.output_dir is None:
        parser.error(
            "--output-dir is required for collect"
        )

    config = json.loads(
        CONFIG.read_text(
            encoding="utf-8"
        )
    )

    report = collect(
        config
    )

    write_outputs(
        report,
        args.output_dir,
    )


if __name__ == "__main__":
    main()

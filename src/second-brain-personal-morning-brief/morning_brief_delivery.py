#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import hashlib
import importlib.util
import json
import os
import tempfile
from datetime import datetime, timezone


DEFAULT_CONFIG = Path(
    "/AI/Config/Second Brain/"
    "Personal Morning Brief/config.json"
)


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


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


def atomic_json(
    path: Path,
    value: Any,
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
            json.dump(
                value,
                handle,
                indent=2,
                ensure_ascii=False,
            )

            handle.write(
                "\n"
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


def load_router(
    path: Path,
):
    spec = importlib.util.spec_from_file_location(
        "second_brain_notification_router",
        path,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "Could not load notification router."
        )

    module = (
        importlib.util.module_from_spec(
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
            "Notification router send_ntfy() is missing."
        )

    return module


def report_date(
    report: dict[str, Any],
) -> str:
    explicit = str(
        report.get(
            "report_date",
            "",
        )
    )

    if len(
        explicit
    ) >= 10:
        return explicit[:10]

    generated = str(
        report.get(
            "generated_at_local",
            "",
        )
    )

    if len(
        generated
    ) < 10:
        raise RuntimeError(
            "Report date is unavailable."
        )

    return generated[:10]


def total_reviews(
    report: dict[str, Any],
) -> int | None:
    total = 0
    available = False

    for review in (
        report.get(
            "reviews"
        )
        or []
    ):
        if not isinstance(
            review,
            dict,
        ):
            continue

        count = review.get(
            "open_count"
        )

        if isinstance(
            count,
            int,
        ):
            available = True
            total += count

    return (
        total
        if available
        else None
    )


def weather_lines(
    report: dict[str, Any],
) -> list[str]:
    weather = (
        report.get(
            "external",
            {}
        ).get(
            "weather",
            {}
        )
    )

    if (
        weather.get(
            "status"
        )
        != "ok"
    ):
        return [
            "Weather: unavailable"
        ]

    periods = (
        weather.get(
            "periods"
        )
        or []
    )[:2]

    if not periods:
        return [
            "Weather: unavailable"
        ]

    lines = [
        "Weather:"
    ]

    for period in periods:
        name = (
            period.get(
                "name"
            )
            or "Period"
        )

        temperature = (
            period.get(
                "temperature"
            )
        )

        unit = (
            period.get(
                "temperature_unit"
            )
            or ""
        )

        forecast = (
            period.get(
                "short_forecast"
            )
            or "Unavailable"
        )

        temperature_text = (
            (
                str(
                    temperature
                )
                + "°"
                + str(
                    unit
                )
            )
            if temperature
            is not None
            else "temperature unavailable"
        )

        lines.append(
            "- "
            + str(name)
            + ": "
            + temperature_text
            + ", "
            + str(forecast)
        )

    return lines


def news_lines(
    report: dict[str, Any],
) -> list[str]:
    news = (
        report.get(
            "external",
            {}
        ).get(
            "news",
            {}
        )
    )

    if (
        news.get(
            "status"
        )
        != "ok"
    ):
        return [
            "Top news: unavailable"
        ]

    items = (
        news.get(
            "items"
        )
        or []
    )[:3]

    if not items:
        return [
            "Top news: unavailable"
        ]

    lines = [
        "Top news:"
    ]

    for index, item in enumerate(
        items,
        start=1,
    ):
        title = (
            item.get(
                "title"
            )
            or "Unavailable"
        )

        lines.append(
            str(index)
            + ". "
            + str(title)
        )

    return lines


def status_lines(
    report: dict[str, Any],
) -> list[str]:
    tasks = (
        report.get(
            "tasks",
            {}
        ).get(
            "open_count"
        )
    )

    shopping = (
        report.get(
            "shopping",
            {}
        ).get(
            "open_count"
        )
    )

    health = (
        report.get(
            "health",
            {}
        ).get(
            "general",
            {}
        ).get(
            "issue_count"
        )
    )

    reviews = total_reviews(
        report
    )

    return [
        (
            "Open tasks: "
            + (
                str(tasks)
                if tasks is not None
                else "unavailable"
            )
        ),
        (
            "Shopping items: "
            + (
                str(shopping)
                if shopping is not None
                else "unavailable"
            )
        ),
        (
            "Health issues: "
            + (
                str(health)
                if health is not None
                else "unavailable"
            )
        ),
        (
            "Open review items: "
            + (
                str(reviews)
                if reviews is not None
                else "unavailable"
            )
        ),
    ]


def build_notification(
    report: dict[str, Any],
    kind: str = "normal",
) -> tuple[str, str]:
    date = report_date(
        report
    )

    if kind == "revision":
        title = (
            "Personal Morning Brief Revision - "
            + date
        )

        intro = (
            "A materially changed Personal Morning Brief "
            "revision is ready in Second Brain."
        )

    elif kind == "catchup":
        title = (
            "Morning Brief Catch-up - "
            + date
        )

        intro = (
            "A missed Personal Morning Brief has been "
            "recovered using data available at recovery time."
        )

    else:
        title = (
            "Personal Morning Brief - "
            + date
        )

        intro = (
            "Your Personal Morning Brief is ready "
            "in Second Brain."
        )

    body_lines = [
        intro,
        "",
        *status_lines(
            report
        ),
        "",
        *weather_lines(
            report
        ),
        "",
        *news_lines(
            report
        ),
    ]

    return (
        title,
        "\n".join(
            body_lines
        ),
    )


def build_summary_notification(
    report: dict[str, Any],
    recovered_dates: list[str],
) -> tuple[str, str]:
    title = (
        "Morning Brief Catch-up - "
        + str(
            len(
                recovered_dates
            )
        )
        + " recovered"
    )

    body_lines = [
        (
            "Recovered Morning Brief dates: "
            + ", ".join(
                recovered_dates
            )
        ),
        "",
        (
            "Weather and headlines below come from "
            "the last recovery collection."
        ),
        "",
        *weather_lines(
            report
        ),
        "",
        *news_lines(
            report
        ),
    ]

    return (
        title,
        "\n".join(
            body_lines
        ),
    )


def delivery_context(
    config_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    Path,
    Path,
    Path,
    Path,
]:
    config = load_json(
        config_path,
        None,
    )

    if not isinstance(
        config,
        dict,
    ):
        raise RuntimeError(
            "Morning Brief config is invalid."
        )

    delivery = (
        config.get(
            "delivery"
        )
        or {}
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

    state_path = Path(
        delivery[
            "state_path"
        ]
    )

    log_path = Path(
        delivery[
            "log_path"
        ]
    )

    return (
        config,
        delivery,
        router_path,
        router_config_path,
        state_path,
        log_path,
    )


def load_delivery_state(
    path: Path,
) -> dict[str, Any]:
    current = load_json(
        path,
        {},
    )

    if not isinstance(
        current,
        dict,
    ):
        current = {}

    if (
        current.get(
            "schema_version"
        )
        == "2.0.0"
        and isinstance(
            current.get(
                "deliveries"
            ),
            dict,
        )
    ):
        return current

    migrated = {
        "schema_version":
            "2.0.0",

        "deliveries":
            {},
    }

    legacy_key = (
        current.get(
            "last_delivery_key"
        )
    )

    if legacy_key:
        migrated[
            "deliveries"
        ][
            legacy_key
        ] = {
            "legacy":
                True,

            "delivered_at":
                current.get(
                    "last_delivered_at"
                ),

            "http_status":
                current.get(
                    "last_http_status"
                ),
        }

    return migrated


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


def send_payload(
    config_path: Path,
    title: str,
    body: str,
    delivery_key: str,
    kind: str,
    send: bool,
) -> dict[str, Any]:
    (
        _,
        delivery,
        router_path,
        router_config_path,
        state_path,
        log_path,
    ) = delivery_context(
        config_path
    )

    if (
        delivery.get(
            "enabled"
        )
        is not True
    ):
        raise RuntimeError(
            "Production Morning Brief delivery is disabled."
        )

    if (
        "\\r" in title
        or "\\n" in title
    ):
        raise RuntimeError(
            "Notification title contains forbidden newline characters."
        )

    try:
        title.encode(
            "ascii"
        )
    except UnicodeEncodeError as exc:
        raise RuntimeError(
            "Notification title is not ASCII-safe for HTTP header transport."
        ) from exc

    digest = payload_sha256(
        title,
        body,
    )

    state = load_delivery_state(
        state_path
    )

    if (
        delivery_key
        in state[
            "deliveries"
        ]
    ):
        return {
            "status":
                "completed",

            "result":
                "no_changes",

            "delivery_key":
                delivery_key,

            "sent":
                False,

            "reason":
                "idempotent_duplicate",
        }

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

            "payload_sha256":
                digest,
        }

    router_config = load_json(
        router_config_path,
        None,
    )

    if not isinstance(
        router_config,
        dict,
    ):
        raise RuntimeError(
            "Notification router config is invalid."
        )

    router = load_router(
        router_path
    )

    status = router.send_ntfy(
        router_config,
        title,
        body,
        delivery.get(
            "priority",
            "default",
        ),
        delivery.get(
            "tags",
            "sunrise",
        ),
    )

    status = int(
        status
    )

    if not (
        200 <= status < 300
    ):
        raise RuntimeError(
            "Notification delivery returned HTTP "
            + str(
                status
            )
        )

    completed_at = utc_now()

    state[
        "deliveries"
    ][
        delivery_key
    ] = {
        "kind":
            kind,

        "delivered_at":
            completed_at,

        "http_status":
            status,

        "payload_sha256":
            digest,
    }

    state[
        "last_delivery_key"
    ] = delivery_key

    atomic_json(
        state_path,
        state,
    )

    result = {
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

        "delivered_at":
            completed_at,

        "payload_sha256":
            digest,
    }

    append_jsonl(
        log_path,
        result,
    )

    return result


def deliver(
    report_path: Path,
    delivery_key: str,
    kind: str,
    config_path: Path,
    send: bool,
) -> dict[str, Any]:
    report = load_json(
        report_path,
        None,
    )

    if not isinstance(
        report,
        dict,
    ):
        raise RuntimeError(
            "Morning Brief report JSON is invalid."
        )

    title, body = build_notification(
        report,
        kind,
    )

    return send_payload(
        config_path,
        title,
        body,
        delivery_key,
        kind,
        send,
    )


def deliver_summary(
    report_path: Path,
    delivery_key: str,
    recovered_dates: list[str],
    config_path: Path,
    send: bool,
) -> dict[str, Any]:
    report = load_json(
        report_path,
        None,
    )

    if not isinstance(
        report,
        dict,
    ):
        raise RuntimeError(
            "Morning Brief report JSON is invalid."
        )

    title, body = (
        build_summary_notification(
            report,
            recovered_dates,
        )
    )

    return send_payload(
        config_path,
        title,
        body,
        delivery_key,
        "catchup_summary",
        send,
    )


def self_test() -> None:
    sample = {
        "report_date":
            "2026-08-14",

        "generated_at_local":
            "2026-08-14T07:00:00-04:00",

        "tasks": {
            "open_count":
                2,

            "items": [
                "PRIVATE TASK"
            ],
        },

        "shopping": {
            "open_count":
                1,

            "items": [
                "PRIVATE SHOPPING"
            ],
        },

        "health": {
            "general": {
                "issue_count":
                    3,
            },
        },

        "reviews": [
            {
                "open_count":
                    2,
            },
            {
                "open_count":
                    4,
            },
        ],

        "external": {
            "weather": {
                "status":
                    "ok",

                "periods": [
                    {
                        "name":
                            "Today",

                        "temperature":
                            80,

                        "temperature_unit":
                            "F",

                        "short_forecast":
                            "Sunny",
                    },
                    {
                        "name":
                            "Tonight",

                        "temperature":
                            65,

                        "temperature_unit":
                            "F",

                        "short_forecast":
                            "Clear",
                    },
                ],
            },

            "news": {
                "status":
                    "ok",

                "items": [
                    {
                        "title":
                            "Headline One",

                        "url":
                            "https://example.invalid/one",
                    },
                    {
                        "title":
                            "Headline Two",

                        "url":
                            "https://example.invalid/two",
                    },
                    {
                        "title":
                            "Headline Three",

                        "url":
                            "https://example.invalid/three",
                    },
                ],
            },
        },
    }

    title, body = build_notification(
        sample
    )

    required = (
        "Open tasks: 2",
        "Shopping items: 1",
        "Health issues: 3",
        "Open review items: 6",
        "Today: 80°F, Sunny",
        "Tonight: 65°F, Clear",
        "Headline One",
        "Headline Two",
        "Headline Three",
    )

    for value in required:
        if value not in body:
            raise RuntimeError(
                "Notification self-test missing: "
                + value
            )

    forbidden = (
        "PRIVATE TASK",
        "PRIVATE SHOPPING",
        "example.invalid",
    )

    for value in forbidden:
        if value in body:
            raise RuntimeError(
                "Notification privacy self-test failed: "
                + value
            )

    summary_title, summary_body = (
        build_summary_notification(
            sample,
            [
                "2026-08-12",
                "2026-08-13",
            ],
        )
    )

    if (
        "2 recovered"
        not in summary_title
        or
        "2026-08-12"
        not in summary_body
        or
        "Headline One"
        not in summary_body
    ):
        raise RuntimeError(
            "Catch-up summary self-test failed."
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
            "preview",
            "deliver",
            "preview-summary",
            "deliver-summary",
        ],
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
    )

    parser.add_argument(
        "--report",
        type=Path,
    )

    parser.add_argument(
        "--delivery-key",
    )

    parser.add_argument(
        "--kind",
        choices=[
            "normal",
            "revision",
            "catchup",
        ],
        default="normal",
    )

    parser.add_argument(
        "--dates-json",
    )

    args = parser.parse_args()

    if args.command == "self-test":
        self_test()
        return

    if args.report is None:
        parser.error(
            "--report is required"
        )

    if not args.delivery_key:
        parser.error(
            "--delivery-key is required"
        )

    if args.command in {
        "preview",
        "deliver",
    }:
        result = deliver(
            args.report,
            args.delivery_key,
            args.kind,
            args.config,
            args.command == "deliver",
        )

    else:
        if not args.dates_json:
            parser.error(
                "--dates-json is required"
            )

        dates = json.loads(
            args.dates_json
        )

        if not isinstance(
            dates,
            list,
        ):
            parser.error(
                "--dates-json must decode to a list"
            )

        result = deliver_summary(
            args.report,
            args.delivery_key,
            [
                str(
                    value
                )
                for value in dates
            ],
            args.config,
            args.command == "deliver-summary",
        )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

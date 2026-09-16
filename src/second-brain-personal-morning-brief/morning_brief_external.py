#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import re
import ssl

from datetime import datetime, timezone
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


USER_AGENT = (
    "AI-Second-Brain-Morning-Brief/0.2 "
    "(personal self-hosted read-only morning brief)"
)

HTTP_TIMEOUT = 20
SSL_CONTEXT = ssl.create_default_context()


def sha256_bytes(
    data: bytes,
) -> str:
    return hashlib.sha256(
        data
    ).hexdigest()



def http_fetch(
    url: str,
    accept: str,
) -> dict[str, Any]:
    from urllib.parse import (
        urljoin,
        urlparse,
    )
    from urllib.request import (
        HTTPRedirectHandler,
        HTTPSHandler,
        Request,
        build_opener,
    )
    from urllib.error import (
        HTTPError,
        URLError,
    )

    max_bytes = 2 * 1024 * 1024

    allowed_hosts = {
        "api.weather.gov",
        "www.gotolouisville.com",
        "www.pbs.org",
        "www.reddit.com",
    }

    retrieved = datetime.now(
        timezone.utc
    ).isoformat()

    def validate_url(
        candidate: str,
    ) -> str:
        parsed = urlparse(
            candidate
        )

        scheme = (
            parsed.scheme
            or ""
        ).casefold()

        host = (
            parsed.hostname
            or ""
        ).casefold()

        if scheme != "https":
            raise RuntimeError(
                "External URL is not HTTPS."
            )

        if host not in allowed_hosts:
            raise RuntimeError(
                "External host is not allowlisted: "
                + repr(host)
            )

        if parsed.username or parsed.password:
            raise RuntimeError(
                "Credentials in external URL are forbidden."
            )

        return host

    try:
        validate_url(
            url
        )
    except Exception as exc:
        return {
            "ok": False,
            "requested_url": url,
            "final_url": None,
            "status_code": None,
            "content_type": None,
            "retrieved_at_utc": retrieved,
            "sha256": None,
            "error":
                type(exc).__name__
                + ": "
                + str(exc),
            "data": b"",
        }

    class GuardedRedirectHandler(
        HTTPRedirectHandler
    ):
        def redirect_request(
            self,
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        ):
            resolved = urljoin(
                req.full_url,
                newurl,
            )

            validate_url(
                resolved
            )

            return super().redirect_request(
                req,
                fp,
                code,
                msg,
                headers,
                resolved,
            )

    request = Request(
        url,
        headers={
            "User-Agent":
                USER_AGENT,

            "Accept":
                accept,

            "Accept-Encoding":
                "identity",
        },
    )

    opener = build_opener(
        HTTPSHandler(
            context=SSL_CONTEXT
        ),
        GuardedRedirectHandler(),
    )

    try:
        with opener.open(
            request,
            timeout=HTTP_TIMEOUT,
        ) as response:

            final_url = (
                response.geturl()
            )

            try:
                validate_url(
                    final_url
                )
            except Exception as exc:
                return {
                    "ok": False,
                    "requested_url": url,
                    "final_url": final_url,
                    "status_code":
                        getattr(
                            response,
                            "status",
                            None,
                        ),
                    "content_type":
                        response.headers.get(
                            "Content-Type"
                        ),
                    "retrieved_at_utc":
                        retrieved,
                    "sha256": None,
                    "error":
                        type(exc).__name__
                        + ": "
                        + str(exc),
                    "data": b"",
                }

            raw_length = (
                response.headers.get(
                    "Content-Length"
                )
            )

            if raw_length:
                try:
                    declared_length = int(
                        raw_length
                    )
                except ValueError:
                    declared_length = None

                if (
                    declared_length is not None
                    and declared_length > max_bytes
                ):
                    return {
                        "ok": False,
                        "requested_url": url,
                        "final_url": final_url,
                        "status_code":
                            getattr(
                                response,
                                "status",
                                None,
                            ),
                        "content_type":
                            response.headers.get(
                                "Content-Type"
                            ),
                        "retrieved_at_utc":
                            retrieved,
                        "sha256": None,
                        "error":
                            (
                                "Response exceeds "
                                "2 MiB Content-Length limit."
                            ),
                        "data": b"",
                    }

            data = response.read(
                max_bytes + 1
            )

            if len(data) > max_bytes:
                return {
                    "ok": False,
                    "requested_url": url,
                    "final_url": final_url,
                    "status_code":
                        getattr(
                            response,
                            "status",
                            None,
                        ),
                    "content_type":
                        response.headers.get(
                            "Content-Type"
                        ),
                    "retrieved_at_utc":
                        retrieved,
                    "sha256": None,
                    "error":
                        (
                            "Response exceeds "
                            "2 MiB read limit."
                        ),
                    "data": b"",
                }

            return {
                "ok": True,
                "requested_url": url,
                "final_url": final_url,
                "status_code":
                    getattr(
                        response,
                        "status",
                        None,
                    ),
                "content_type":
                    response.headers.get(
                        "Content-Type"
                    ),
                "retrieved_at_utc":
                    retrieved,
                "sha256":
                    sha256_bytes(
                        data
                    ),
                "data":
                    data,
            }

    except HTTPError as exc:
        try:
            body = exc.read(
                max_bytes + 1
            )
        except Exception:
            body = b""

        if len(body) > max_bytes:
            body = b""

        final_url = (
            exc.geturl()
            if hasattr(
                exc,
                "geturl",
            )
            else None
        )

        if final_url:
            try:
                validate_url(
                    final_url
                )
            except Exception as final_exc:
                return {
                    "ok": False,
                    "requested_url": url,
                    "final_url": final_url,
                    "status_code":
                        getattr(
                            exc,
                            "code",
                            None,
                        ),
                    "content_type":
                        (
                            exc.headers.get(
                                "Content-Type"
                            )
                            if exc.headers
                            else None
                        ),
                    "retrieved_at_utc":
                        retrieved,
                    "sha256": None,
                    "error":
                        type(final_exc).__name__
                        + ": "
                        + str(final_exc),
                    "data": b"",
                }

        return {
            "ok": False,
            "requested_url": url,
            "final_url": final_url,
            "status_code":
                getattr(
                    exc,
                    "code",
                    None,
                ),
            "content_type":
                (
                    exc.headers.get(
                        "Content-Type"
                    )
                    if exc.headers
                    else None
                ),
            "retrieved_at_utc":
                retrieved,
            "sha256":
                (
                    sha256_bytes(
                        body
                    )
                    if body
                    else None
                ),
            "error":
                "HTTPError: "
                + str(exc),
            "data":
                body,
        }

    except (
        URLError,
        TimeoutError,
        OSError,
        RuntimeError,
    ) as exc:

        return {
            "ok": False,
            "requested_url": url,
            "final_url": None,
            "status_code": None,
            "content_type": None,
            "retrieved_at_utc": retrieved,
            "sha256": None,
            "error":
                type(exc).__name__
                + ": "
                + str(exc),
            "data": b"",
        }



def public_http(
    response: dict[str, Any],
) -> dict[str, Any]:
    return {
        key:
            value
        for key, value
        in response.items()
        if key != "data"
    }


def clean_html_text(
    value: str,
) -> str:
    value = re.sub(
        r"(?is)<script.*?</script>",
        " ",
        value,
    )

    value = re.sub(
        r"(?is)<style.*?</style>",
        " ",
        value,
    )

    value = re.sub(
        r"(?s)<[^>]+>",
        " ",
        value,
    )

    value = unescape(
        value
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def rss_items(
    data: bytes,
) -> list[dict[str, Any]]:
    root = ET.fromstring(
        data
    )

    items = []

    for item in root.findall(
        ".//item"
    ):
        title = (
            item.findtext(
                "title"
            )
            or ""
        ).strip()

        link = (
            item.findtext(
                "link"
            )
            or ""
        ).strip()

        guid = (
            item.findtext(
                "guid"
            )
            or ""
        ).strip()

        published = (
            item.findtext(
                "pubDate"
            )
            or ""
        ).strip()

        description = (
            item.findtext(
                "description"
            )
            or ""
        )

        if not title or not link:
            continue

        items.append(
            {
                "title":
                    title,

                "url":
                    link,

                "id":
                    guid
                    or None,

                "published":
                    published
                    or None,

                "description":
                    description,
            }
        )

    return items


def collect_weather(
    config: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "status":
            "unavailable",

        "source":
            config[
                "source"
            ],

        "source_url":
            config[
                "points_url"
            ],

        "location":
            config[
                "location"
            ],

        "periods":
            [],

        "errors":
            [],
    }

    try:
        points = http_fetch(
            config[
                "points_url"
            ],
            "application/geo+json, application/json",
        )

        result[
            "points_request"
        ] = public_http(
            points
        )

        if not points[
            "ok"
        ]:
            raise RuntimeError(
                points.get(
                    "error",
                    "NWS points request failed"
                )
            )

        payload = json.loads(
            points[
                "data"
            ].decode(
                "utf-8"
            )
        )

        forecast_url = (
            payload.get(
                "properties",
                {}
            ).get(
                "forecast"
            )
        )

        if not forecast_url:
            raise RuntimeError(
                "NWS points response missing forecast URL"
            )

        forecast = http_fetch(
            forecast_url,
            "application/geo+json, application/json",
        )

        result[
            "forecast_request"
        ] = public_http(
            forecast
        )

        result[
            "source_url"
        ] = forecast_url

        if not forecast[
            "ok"
        ]:
            raise RuntimeError(
                forecast.get(
                    "error",
                    "NWS forecast request failed"
                )
            )

        forecast_payload = json.loads(
            forecast[
                "data"
            ].decode(
                "utf-8"
            )
        )

        periods = (
            forecast_payload.get(
                "properties",
                {}
            ).get(
                "periods",
                []
            )
        )

        limit = int(
            config.get(
                "period_limit",
                2,
            )
        )

        normalized = []

        for period in periods[
            :limit
        ]:
            normalized.append(
                {
                    "name":
                        period.get(
                            "name"
                        ),

                    "start_time":
                        period.get(
                            "startTime"
                        ),

                    "end_time":
                        period.get(
                            "endTime"
                        ),

                    "temperature":
                        period.get(
                            "temperature"
                        ),

                    "temperature_unit":
                        period.get(
                            "temperatureUnit"
                        ),

                    "wind_speed":
                        period.get(
                            "windSpeed"
                        ),

                    "wind_direction":
                        period.get(
                            "windDirection"
                        ),

                    "short_forecast":
                        period.get(
                            "shortForecast"
                        ),

                    "detailed_forecast":
                        period.get(
                            "detailedForecast"
                        ),
                }
            )

        if not normalized:
            raise RuntimeError(
                "NWS returned no forecast periods"
            )

        result[
            "periods"
        ] = normalized

        result[
            "retrieved_at_utc"
        ] = forecast[
            "retrieved_at_utc"
        ]

        result[
            "status"
        ] = "ok"

    except Exception as exc:
        result[
            "errors"
        ].append(
            type(
                exc
            ).__name__
            + ": "
            + str(
                exc
            )
        )

    return result


def collect_news(
    config: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "status":
            "unavailable",

        "source":
            config[
                "source"
            ],

        "source_url":
            config[
                "feed_url"
            ],

        "items":
            [],

        "errors":
            [],
    }

    try:
        response = http_fetch(
            config[
                "feed_url"
            ],
            "application/rss+xml, application/xml, text/xml",
        )

        result[
            "request"
        ] = public_http(
            response
        )

        if not response[
            "ok"
        ]:
            raise RuntimeError(
                response.get(
                    "error",
                    "News RSS request failed"
                )
            )

        parsed = rss_items(
            response[
                "data"
            ]
        )

        limit = int(
            config.get(
                "limit",
                3,
            )
        )

        normalized = [
            {
                "title":
                    item[
                        "title"
                    ],

                "url":
                    item[
                        "url"
                    ],

                "id":
                    item[
                        "id"
                    ],

                "published":
                    item[
                        "published"
                    ],
            }
            for item in parsed[
                :limit
            ]
        ]

        if len(
            normalized
        ) < limit:
            raise RuntimeError(
                "News RSS returned fewer than "
                + str(
                    limit
                )
                + " usable items"
            )

        result[
            "items"
        ] = normalized

        result[
            "retrieved_at_utc"
        ] = response[
            "retrieved_at_utc"
        ]

        result[
            "status"
        ] = "ok"

    except Exception as exc:
        result[
            "errors"
        ].append(
            type(
                exc
            ).__name__
            + ": "
            + str(
                exc
            )
        )

    return result


def collect_reddit(
    config: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "status":
            "unavailable",

        "source":
            config[
                "source"
            ],

        "source_url":
            config[
                "feed_url"
            ],

        "items":
            [],

        "errors":
            [],
    }

    try:
        response = http_fetch(
            config[
                "feed_url"
            ],
            "application/atom+xml, application/xml, text/xml",
        )

        result[
            "request"
        ] = public_http(
            response
        )

        if not response[
            "ok"
        ]:
            raise RuntimeError(
                response.get(
                    "error",
                    "Reddit RSS request failed"
                )
            )

        root = ET.fromstring(
            response[
                "data"
            ]
        )

        ns = {
            "atom":
                "http://www.w3.org/2005/Atom"
        }

        limit = int(
            config.get(
                "limit",
                3,
            )
        )

        normalized = []

        for entry in root.findall(
            "atom:entry",
            ns,
        ):
            title = (
                entry.findtext(
                    "atom:title",
                    default="",
                    namespaces=ns,
                )
                or ""
            ).strip()

            entry_id = (
                entry.findtext(
                    "atom:id",
                    default="",
                    namespaces=ns,
                )
                or ""
            ).strip()

            published = (
                entry.findtext(
                    "atom:published",
                    default="",
                    namespaces=ns,
                )
                or ""
            ).strip()

            url = None

            for node in entry.findall(
                "atom:link",
                ns,
            ):
                href = node.attrib.get(
                    "href"
                )

                rel = node.attrib.get(
                    "rel"
                )

                if (
                    href
                    and rel in {
                        None,
                        "alternate",
                    }
                ):
                    url = href
                    break

            if not title or not url:
                continue

            normalized.append(
                {
                    "title":
                        title,

                    "url":
                        url,

                    "id":
                        entry_id
                        or None,

                    "published":
                        published
                        or None,
                }
            )

            if len(
                normalized
            ) >= limit:
                break

        if len(
            normalized
        ) < limit:
            raise RuntimeError(
                "Reddit RSS returned fewer than "
                + str(
                    limit
                )
                + " usable entries"
            )

        result[
            "items"
        ] = normalized

        result[
            "retrieved_at_utc"
        ] = response[
            "retrieved_at_utc"
        ]

        result[
            "status"
        ] = "ok"

    except Exception as exc:
        result[
            "errors"
        ].append(
            type(
                exc
            ).__name__
            + ": "
            + str(
                exc
            )
        )

    return result


def date_labels(
    local_now: datetime,
) -> list[str]:
    weekday = local_now.strftime(
        "%A"
    )

    month = local_now.strftime(
        "%B"
    )

    day = str(
        local_now.day
    )

    year = str(
        local_now.year
    )

    return [
        weekday
        + ", "
        + month
        + " "
        + day,

        month
        + " "
        + day
        + ", "
        + year,

        month
        + " "
        + day,
    ]


def collect_events(
    config: dict[str, Any],
    local_now: datetime,
) -> dict[str, Any]:
    result = {
        "status":
            "unavailable",

        "source":
            config[
                "source"
            ],

        "source_url":
            config[
                "feed_url"
            ],

        "location":
            config[
                "location"
            ],

        "date":
            local_now.date().isoformat(),

        "items":
            [],

        "errors":
            [],
    }

    try:
        response = http_fetch(
            config[
                "feed_url"
            ],
            "application/rss+xml, application/xml, text/xml",
        )

        result[
            "request"
        ] = public_http(
            response
        )

        if not response[
            "ok"
        ]:
            raise RuntimeError(
                response.get(
                    "error",
                    "Louisville events RSS request failed"
                )
            )

        parsed = rss_items(
            response[
                "data"
            ]
        )

        labels = [
            label.casefold()
            for label in date_labels(
                local_now
            )
        ]

        matches = []

        for item in parsed:
            description = clean_html_text(
                str(
                    item.get(
                        "description"
                    )
                    or ""
                )
            )

            searchable = (
                item[
                    "title"
                ]
                + " "
                + description
            ).casefold()

            if not any(
                label in searchable
                for label in labels
            ):
                continue

            matches.append(
                {
                    "title":
                        item[
                            "title"
                        ],

                    "url":
                        item[
                            "url"
                        ],

                    "id":
                        item[
                            "id"
                        ],

                    "published":
                        item[
                            "published"
                        ],

                    "date_match":
                        local_now.date().isoformat(),

                    "description_excerpt":
                        description[
                            :400
                        ],
                }
            )

        limit = int(
            config.get(
                "limit",
                3,
            )
        )

        result[
            "items"
        ] = matches[
            :limit
        ]

        result[
            "retrieved_at_utc"
        ] = response[
            "retrieved_at_utc"
        ]

        if len(
            result[
                "items"
            ]
        ) >= limit:
            result[
                "status"
            ] = "ok"

        elif result[
            "items"
        ]:
            result[
                "status"
            ] = "partial"

        else:
            result[
                "status"
            ] = "no_events_found"

    except Exception as exc:
        result[
            "errors"
        ].append(
            type(
                exc
            ).__name__
            + ": "
            + str(
                exc
            )
        )

    return result





def collect_external(
    config: dict[str, Any],
    local_now: datetime,
) -> dict[str, Any]:
    collected = {
        "weather":
            collect_weather(
                config["weather"]
            ),

        "events":
            collect_events(
                config["events"],
                local_now,
            ),

        "news":
            collect_news(
                config["news"]
            ),

        "reddit":
            collect_reddit(
                config["reddit"]
            ),
    }

    for source_name, result in collected.items():
        retrieved = result.get(
            "retrieved_at_utc"
        )

        if retrieved is None:
            request = (
                result.get("request")
                or result.get("forecast_request")
                or result.get("points_request")
                or {}
            )

            retrieved = request.get(
                "retrieved_at_utc"
            )

        status = str(
            result.get(
                "status",
                "unavailable",
            )
        )

        usable = status in {
            "ok",
            "partial",
            "no_events_found",
        }

        result["fetched_at"] = retrieved

        result["freshness"] = {
            "fetched_at":
                retrieved,

            "basis":
                "collection_time",

            "status":
                (
                    "current_at_collection_time"
                    if retrieved
                    else "unavailable"
                ),
        }

        result["quality"] = {
            "status":
                status,

            "usable":
                usable,

            "error_count":
                len(
                    result.get(
                        "errors",
                        []
                    )
                ),

            "source_id":
                source_name,

            "url":
                result.get(
                    "source_url"
                ),
        }

    return collected





def markdown_link(
    title: str,
    url: str,
) -> str:
    return (
        "["
        + title.replace(
            "]",
            r"\]",
        )
        + "]("
        + url
        + ")"
    )


def render_external_markdown(
    report: dict[str, Any],
) -> str:
    external = report[
        "external"
    ]

    lines = [
        "## Weather",
        "",
    ]

    weather = external[
        "weather"
    ]

    if weather[
        "status"
    ] == "ok":
        for period in weather[
            "periods"
        ]:
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
                        "temperature_unit"
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
            "- Source: "
            + markdown_link(
                weather[
                    "source"
                ],
                weather[
                    "source_url"
                ],
            )
        )
    else:
        lines.append(
            "- Weather unavailable: "
            + "; ".join(
                weather.get(
                    "errors",
                    []
                )
            )
        )

    lines.extend(
        [
            "",
            "## Local Events",
            "",
        ]
    )

    events = external[
        "events"
    ]

    if events[
        "items"
    ]:
        for item in events[
            "items"
        ]:
            lines.append(
                "- "
                + markdown_link(
                    item[
                        "title"
                    ],
                    item[
                        "url"
                    ],
                )
            )

        lines.append(
            "- Source: "
            + markdown_link(
                events[
                    "source"
                ],
                events[
                    "source_url"
                ],
            )
        )

        if events[
            "status"
        ] == "partial":
            lines.append(
                "- Fewer than 3 events matching today's local date "
                "were available."
            )

    elif events[
        "status"
    ] == "no_events_found":
        lines.append(
            "- No events matching today's Louisville local date "
            "were found in the feed."
        )

        lines.append(
            "- Source: "
            + markdown_link(
                events[
                    "source"
                ],
                events[
                    "source_url"
                ],
            )
        )

    else:
        lines.append(
            "- Events unavailable: "
            + "; ".join(
                events.get(
                    "errors",
                    []
                )
            )
        )

    lines.extend(
        [
            "",
            "## Top News",
            "",
        ]
    )

    news = external[
        "news"
    ]

    if news[
        "status"
    ] == "ok":
        for item in news[
            "items"
        ]:
            lines.append(
                "- "
                + markdown_link(
                    item[
                        "title"
                    ],
                    item[
                        "url"
                    ],
                )
            )
    else:
        lines.append(
            "- News unavailable: "
            + "; ".join(
                news.get(
                    "errors",
                    []
                )
            )
        )

    lines.extend(
        [
            "",
            "## Trending on Reddit",
            "",
        ]
    )

    reddit = external[
        "reddit"
    ]

    if reddit[
        "status"
    ] == "ok":
        for item in reddit[
            "items"
        ]:
            lines.append(
                "- "
                + markdown_link(
                    item[
                        "title"
                    ],
                    item[
                        "url"
                    ],
                )
            )
    else:
        lines.append(
            "- Reddit unavailable: "
            + "; ".join(
                reddit.get(
                    "errors",
                    []
                )
            )
        )

    return "\n".join(
        lines
    )


def external_self_test() -> None:
    html = (
        "<p>Hello <strong>Louisville</strong> "
        "&amp; Kentucky</p>"
    )

    actual = clean_html_text(
        html
    )

    if actual != "Hello Louisville & Kentucky":
        raise RuntimeError(
            "HTML normalization failed: "
            + repr(
                actual
            )
        )

    sample = datetime(
        2026,
        8,
        14,
        7,
        0,
    )

    labels = date_labels(
        sample
    )

    if (
        "Friday, August 14"
        not in labels
        or
        "August 14, 2026"
        not in labels
    ):
        raise RuntimeError(
            "Date label test failed."
        )

    rss = b"""<?xml version="1.0"?>
<rss version="2.0">
<channel>
<item>
<title>Test Event</title>
<link>https://example.com/event</link>
<guid>example-event</guid>
<pubDate>Fri, 14 Aug 2026 09:00:00 -0400</pubDate>
<description>Friday, August 14</description>
</item>
</channel>
</rss>
"""

    parsed = rss_items(
        rss
    )

    if len(
        parsed
    ) != 1:
        raise RuntimeError(
            "RSS parsing test failed."
        )

    print(
        json.dumps(
            {
                "status":
                    "completed",

                "external_self_test":
                    "pass",
            }
        )
    )


if __name__ == "__main__":
    external_self_test()

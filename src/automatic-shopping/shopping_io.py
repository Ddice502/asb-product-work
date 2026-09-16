#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from shopping_common import (
    normalize_category,
    normalize_item,
    parse_metadata,
)

ACTIVE_RE = re.compile(
    r"^- \[([ xX])\] "
    r"(.+?)\s+"
    r"<!-- shopping-id:"
    r"([0-9a-f]{16}) -->\s*$"
)


def sha256_file(path):
    path = Path(path)

    if not path.is_file():
        return None

    return hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


def load_json(path, default=None):
    path = Path(path)

    if not path.is_file():
        return (
            {}
            if default is None
            else default
        )

    return json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )


def atomic_write_text(path, text):
    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, name = tempfile.mkstemp(
        prefix=path.name + ".",
        dir=str(path.parent),
    )

    temp = Path(name)

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        os.replace(
            temp,
            path,
        )

    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def atomic_write_json(path, data):
    atomic_write_text(
        path,
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ) + "\n",
    )


def resolve_policy_path(
    value,
    vault,
):
    path = Path(value)

    if path.is_absolute():
        return path

    return Path(vault) / path


def parse_active_items(text):
    match = re.search(
        r"^## Active\s*$",
        text,
        re.M,
    )

    if not match:
        return []

    remaining = text[
        match.end():
    ]

    next_heading = re.search(
        r"^##\s+",
        remaining,
        re.M,
    )

    if next_heading:
        remaining = remaining[
            :next_heading.start()
        ]

    records = []
    current = None

    for line in remaining.splitlines():
        match = ACTIVE_RE.match(line)

        if match:
            item, metadata = parse_metadata(
                match.group(2)
            )

            current = {
                "shopping_id":
                    match.group(3),

                "checked":
                    match.group(1).lower()
                    == "x",

                "item":
                    item,

                "item_key":
                    normalize_item(item),

                "quantity":
                    metadata.get(
                        "quantity",
                        "",
                    ),

                "store":
                    metadata.get(
                        "store",
                        "",
                    ),

                "category":
                    normalize_category(
                        metadata.get(
                            "category",
                            "",
                        )
                    ),

                "source_excerpt":
                    "",
            }

            records.append(current)
            continue

        if (
            current is not None
            and line.lstrip().startswith(
                "> Source:"
            )
        ):
            current[
                "source_excerpt"
            ] = line.split(
                ":",
                1,
            )[1].strip()

    return records


def metadata_suffix(record):
    parts = []

    for field in (
        "quantity",
        "store",
        "category",
    ):
        value = str(
            record.get(
                field,
                "",
            )
        ).strip()

        if value:
            parts.append(
                f"{field}: {value}"
            )

    if not parts:
        return ""

    return (
        " ("
        + ", ".join(parts)
        + ")"
    )


def render_active(records):
    if not records:
        return (
            "No active shopping items."
        )

    lines = []

    for record in sorted(
        records,
        key=lambda item: (
            item.get(
                "item_key",
                "",
            ),
            item.get(
                "shopping_id",
                "",
            ),
        ),
    ):
        mark = (
            "x"
            if record.get("checked")
            else " "
        )

        lines.append(
            f"- [{mark}] "
            f"{record['item']}"
            f"{metadata_suffix(record)} "
            f"<!-- shopping-id:"
            f"{record['shopping_id']} -->"
        )

        source = str(
            record.get(
                "source_excerpt",
                "",
            )
        ).strip()

        if source:
            lines.append(
                f"  > Source: {source}"
            )

        fingerprint = str(
            record.get(
                "fingerprint",
                "",
            )
        ).strip()

        if fingerprint:
            lines.append(
                "  "
                "<!-- shopping-fingerprint:"
                f"{fingerprint} -->"
            )

        lines.append("")

    return "\n".join(
        lines
    ).rstrip()


def replace_section(
    text,
    heading,
    body,
):
    pattern = re.compile(
        rf"(^## {re.escape(heading)}\s*$)"
        r".*?"
        r"(?=^##\s+|\Z)",
        re.M | re.S,
    )

    replacement = (
        f"## {heading}\n\n"
        f"{body.rstrip()}\n"
    )

    match = pattern.search(text)

    if match:
        return (
            text[:match.start()]
            + replacement
            + text[match.end():]
        )

    return (
        text.rstrip()
        + "\n\n"
        + replacement
    )


def append_history(
    text,
    entries,
):
    if not entries:
        return text

    match = re.search(
        r"^## History\s*$",
        text,
        re.M,
    )

    existing = ""

    if match:
        existing = text[
            match.end():
        ].strip()

    placeholder = (
        "No purchased shopping items "
        "have been archived yet."
    )

    if existing == placeholder:
        existing = ""

    blocks = (
        [existing]
        if existing
        else []
    )

    for record in entries:
        block = [
            f"- [x] "
            f"{record['item']}"
            f"{metadata_suffix(record)} "
            f"<!-- shopping-id:"
            f"{record['shopping_id']} -->",

            f"  > Purchased: "
            f"{record['purchased_at']}",
        ]

        source = str(
            record.get(
                "source_excerpt",
                "",
            )
        ).strip()

        if source:
            block.append(
                f"  > Source: {source}"
            )

        blocks.append(
            "\n".join(block)
        )

    return replace_section(
        text,
        "History",
        "\n\n".join(blocks),
    )


def append_reviews(
    text,
    entries,
):
    if not entries:
        return text

    match = re.search(
        r"^## Pending\s*$",
        text,
        re.M,
    )

    existing = ""

    if match:
        existing = text[
            match.end():
        ].strip()

    if (
        existing
        == "No shopping items require review."
    ):
        existing = ""

    blocks = (
        [existing]
        if existing
        else []
    )

    for record in entries:
        reasons = ", ".join(
            record.get(
                "reasons",
                [],
            )
        )

        block = [
            f"- [ ] {record['item']} "
            f"<!-- shopping-review-id:"
            f"{record['candidate_id']} -->",

            f"  > Reason: {reasons}",

            f"  > Source note: "
            f"{record['source_note']}",
        ]

        source = str(
            record.get(
                "source_excerpt",
                "",
            )
        ).strip()

        if source:
            block.append(
                f"  > Source: {source}"
            )

        blocks.append(
            "\n".join(block)
        )

    result = replace_section(
        text,
        "Pending",
        "\n\n".join(blocks),
    )

    count = len(
        re.findall(
            r"shopping-review-id:",
            result,
        )
    )

    result = re.sub(
        r"^pending_count:\s*\d+\s*$",
        f"pending_count: {count}",
        result,
        count=1,
        flags=re.M,
    )

    return result

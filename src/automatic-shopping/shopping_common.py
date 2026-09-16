#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import re
import unicodedata


ALLOWED_CATEGORIES = {
    "groceries",
    "household",
    "personal-care",
    "pet",
    "pharmacy",
    "hardware",
    "clothing",
    "electronics",
    "automotive",
    "outdoor",
    "office",
    "other",
}


CATEGORY_ALIASES = {
    "grocery": "groceries",
    "food": "groceries",
    "foods": "groceries",
    "house": "household",
    "household supplies": "household",
    "personal care": "personal-care",
    "hygiene": "personal-care",
    "pets": "pet",
    "pet supplies": "pet",
    "medicine": "pharmacy",
    "medical": "pharmacy",
    "hardware store": "hardware",
    "clothes": "clothing",
    "apparel": "clothing",
    "electronic": "electronics",
    "car": "automotive",
    "auto": "automotive",
    "outdoors": "outdoor",
    "outdoor gear": "outdoor",
    "office supplies": "office",
    "misc": "other",
    "miscellaneous": "other",
}


INTENT_PREFIX = re.compile(
    r"^(?:please\s+)?"
    r"(?:(?:i|we)\s+)?"
    r"(?:"
    r"need(?:\s+to)?(?:\s+get)?(?:\s+more)?"
    r"|want(?:\s+to)?"
    r"|have\s+to\s+get"
    r"|remember\s+to\s+buy"
    r"|buy"
    r"|get"
    r"|purchase"
    r"|pick\s+up"
    r"|pickup"
    r"|grab"
    r"|add(?:\s+some)?"
    r")"
    r"\s+",
    re.I,
)


CHECKBOX_RE = re.compile(
    r"^\s*-\s*\[\s*\]\s+(.+?)\s*$"
)


SOURCE_RE = re.compile(
    r"^\s*>\s*Source:\s*(.+?)\s*$",
    re.I,
)


def sha256_text(value):
    return hashlib.sha256(
        str(value).encode("utf-8")
    ).hexdigest()


def normalize_basic(value):
    value = unicodedata.normalize(
        "NFKD",
        str(value or ""),
    )

    value = "".join(
        ch
        for ch in value
        if not unicodedata.combining(ch)
    )

    value = value.lower()

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


def singularize_last_word(value):
    words = value.split()

    if not words:
        return value

    word = words[-1]

    if (
        len(word) > 4
        and word.endswith("ies")
    ):
        word = word[:-3] + "y"

    elif (
        len(word) > 3
        and word.endswith("s")
        and not word.endswith(
            (
                "ss",
                "us",
                "is",
            )
        )
    ):
        word = word[:-1]

    words[-1] = word

    return " ".join(words)


def normalize_item(value):
    value = str(
        value or ""
    ).strip()

    previous = None

    while value != previous:
        previous = value

        value = INTENT_PREFIX.sub(
            "",
            value,
            count=1,
        ).strip()

    value = re.sub(
        r"^(?:a|an|the|some)\s+",
        "",
        value,
        flags=re.I,
    )

    return singularize_last_word(
        normalize_basic(value)
    )


def normalize_quantity(value):
    return normalize_basic(value)


def normalize_store(value):
    return normalize_basic(value)


def normalize_category(value):
    value = normalize_basic(value)

    canonical = CATEGORY_ALIASES.get(
        value,
        value.replace(
            " ",
            "-",
        ),
    )

    if canonical in ALLOWED_CATEGORIES:
        return canonical

    return ""


def extract_frontmatter(text):
    text = str(text or "")

    if not text.startswith("---"):
        return {}

    match = re.match(
        r"^---\s*\n(.*?)\n---\s*(?:\n|$)",
        text,
        re.S,
    )

    if not match:
        return {}

    result = {}

    for line in match.group(
        1
    ).splitlines():
        if ":" not in line:
            continue

        key, raw = line.split(
            ":",
            1,
        )

        key = key.strip()
        raw = raw.strip()

        if (
            len(raw) >= 2
            and raw[0] == raw[-1]
            and raw[0] in {
                '"',
                "'",
            }
        ):
            raw = raw[1:-1]

        result[key] = raw

    return result


def parse_bool(value):
    value = normalize_basic(value)

    if value in {
        "true",
        "yes",
        "1",
    }:
        return True

    if value in {
        "false",
        "no",
        "0",
    }:
        return False

    return None


def source_review_flag(text):
    frontmatter = extract_frontmatter(
        text
    )

    for key in (
        "shopping_requires_review",
        "requires_review",
    ):
        if key in frontmatter:
            return parse_bool(
                frontmatter[key]
            )

    return None


def parse_metadata(raw):
    raw = str(
        raw or ""
    ).strip()

    metadata = {}

    match = re.search(
        r"\(([^()]*)\)\s*$",
        raw,
    )

    if not match:
        return raw, metadata

    parts = [
        part.strip()
        for part in
        match.group(1).split(",")
        if part.strip()
    ]

    recognized = 0

    for part in parts:
        if ":" not in part:
            continue

        key, value = part.split(
            ":",
            1,
        )

        key = normalize_basic(key)
        value = value.strip()

        if key in {
            "quantity",
            "qty",
        }:
            metadata["quantity"] = value
            recognized += 1

        elif key == "store":
            metadata["store"] = value
            recognized += 1

        elif key == "category":
            metadata["category"] = value
            recognized += 1

        elif key == "confidence":
            try:
                metadata["confidence"] = (
                    float(value)
                )
            except ValueError:
                metadata["confidence"] = None

            recognized += 1

    if recognized == 0:
        return raw, {}

    return (
        raw[:match.start()].strip(),
        metadata,
    )


def extract_shopping(text):
    text = str(text or "")

    heading = re.search(
        r"^## Shopping\s*$",
        text,
        re.I | re.M,
    )

    if not heading:
        return []

    remaining = text[
        heading.end():
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

    result = []
    current = None

    for line in remaining.splitlines():
        item_match = CHECKBOX_RE.match(
            line
        )

        if item_match:
            item_raw, metadata = (
                parse_metadata(
                    item_match.group(1)
                )
            )

            current = {
                "item": item_raw,

                "item_key":
                    normalize_item(
                        item_raw
                    ),

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
                    metadata.get(
                        "category",
                        "",
                    ),

                "confidence":
                    metadata.get(
                        "confidence"
                    ),

                "source_excerpt": "",
            }

            result.append(current)
            continue

        source_match = SOURCE_RE.match(
            line
        )

        if (
            source_match
            and current is not None
        ):
            current[
                "source_excerpt"
            ] = source_match.group(
                1
            ).strip()

    return result


def source_fingerprint(
    source_note,
    source_excerpt,
):
    return sha256_text(
        normalize_basic(source_note)
        + "\n"
        + normalize_basic(
            source_excerpt
        )
    )


def item_fingerprint(
    source_note,
    item_key,
    source_excerpt,
):
    return sha256_text(
        normalize_basic(source_note)
        + "\n"
        + normalize_basic(item_key)
        + "\n"
        + normalize_basic(
            source_excerpt
        )
    )


def metadata_conflicts(
    existing,
    candidate,
):
    conflicts = []

    fields = [
        (
            "quantity",
            normalize_quantity,
        ),
        (
            "store",
            normalize_store,
        ),
        (
            "category",
            normalize_category,
        ),
    ]

    for field, normalizer in fields:
        left = normalizer(
            existing.get(
                field,
                "",
            )
        )

        right = normalizer(
            candidate.get(
                field,
                "",
            )
        )

        if (
            left
            and right
            and left != right
        ):
            conflicts.append(field)

    return conflicts


def enrich_metadata(
    existing,
    candidate,
):
    merged = dict(existing)

    conflicts = metadata_conflicts(
        existing,
        candidate,
    )

    if conflicts:
        return merged, conflicts

    for field in (
        "quantity",
        "store",
        "category",
    ):
        if (
            not str(
                merged.get(
                    field,
                    "",
                )
            ).strip()
            and str(
                candidate.get(
                    field,
                    "",
                )
            ).strip()
        ):
            merged[field] = (
                candidate[field]
            )

    return merged, []

"""Pure rendering helpers for grounded answer source references.

This module takes evidence rows already selected and ranked elsewhere
(ask_my_brain.py) and renders the citation block a grounded answer must
carry. It performs no ranking, scoring, retrieval, or index behaviour of
its own, and it must never touch the filesystem, network, a database, or
a vault. It must never import ask_my_brain, to keep this additive to the
frozen V1 retrieval benchmark.

Public API:
    render_source_references(rows) -> list[dict]
    format_source_references(refs) -> str
"""

import re

_WHITESPACE_RE = re.compile(r"\s+")

_SNIPPET_MAX_LEN = 200
_ELLIPSIS = "\u2026"
_EM_DASH = "\u2014"


def _collapse_whitespace(text):
    if text is None:
        return ""
    return _WHITESPACE_RE.sub(" ", str(text)).strip()


def _truncate_snippet(text):
    collapsed = _collapse_whitespace(text)
    if len(collapsed) > _SNIPPET_MAX_LEN:
        return collapsed[:_SNIPPET_MAX_LEN] + _ELLIPSIS
    return collapsed


def _coerce_chunk_index(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def render_source_references(rows):
    """Build one reference per distinct row['path'], first-appearance order.

    Each reference dict has the keys: n, title, path, chunks, snippet.
    - n: 1-based index in first-appearance order.
    - title: row['title'] from the first row seen for that path, falling
      back to the path itself when missing or empty.
    - chunks: sorted, deduplicated list of that path's integer
      chunk_index values (rows lacking a usable chunk_index contribute
      nothing to this list).
    - snippet: the first row's snippet for that path, with whitespace
      collapsed and truncated to 200 characters plus a single ellipsis.

    A row with no path (missing, None, or empty string) is skipped. An
    empty input returns []. The input rows are never mutated.
    """
    if not rows:
        return []

    order = []
    grouped_rows = {}

    for row in rows:
        if row is None:
            continue
        path = row.get("path")
        if not path:
            continue
        if path not in grouped_rows:
            grouped_rows[path] = []
            order.append(path)
        grouped_rows[path].append(row)

    references = []
    for index, path in enumerate(order, start=1):
        rows_for_path = grouped_rows[path]
        first_row = rows_for_path[0]

        title = first_row.get("title")
        if not title:
            title = path

        snippet = _truncate_snippet(first_row.get("snippet"))

        chunk_set = set()
        for r in rows_for_path:
            chunk_value = _coerce_chunk_index(r.get("chunk_index"))
            if chunk_value is not None:
                chunk_set.add(chunk_value)
        chunks = sorted(chunk_set)

        references.append(
            {
                "n": index,
                "title": title,
                "path": path,
                "chunks": chunks,
                "snippet": snippet,
            }
        )

    return references


def _format_chunk_part(chunks):
    if not chunks:
        return ""
    if len(chunks) == 1:
        return "(chunk {0})".format(chunks[0])
    return "(chunks {0})".format(", ".join(str(c) for c in chunks))


def format_source_references(refs):
    """Render one line per reference.

    Format: '[n] title EM_DASH path (chunk N)' for a single chunk, or
    '[n] title EM_DASH path (chunks A, B)' for several. Returns '' when
    there are no references.
    """
    if not refs:
        return ""

    lines = []
    for ref in refs:
        chunk_part = _format_chunk_part(ref.get("chunks"))
        line = "[{n}] {title} {dash} {path}".format(
            n=ref.get("n"),
            title=ref.get("title"),
            dash=_EM_DASH,
            path=ref.get("path"),
        )
        if chunk_part:
            line = "{0} {1}".format(line, chunk_part)
        lines.append(line)

    return "\n".join(lines)

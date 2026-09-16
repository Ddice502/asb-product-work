"""Pure adapter for search() rows to the shape expected by the merged
source-reference renderer.

search() returns chunk rows shaped like:
    id, path, title, heading, body, start_line, end_line, plus any
    retrieval metadata (score, rank, etc).

The renderer expects each row to additionally carry:
    snippet      -- text to display, derived from body
    chunk_index  -- an integer position, derived from start_line

This module performs no ranking, scoring, indexing, chunking, model,
or data changes: it only adds the two derived keys on top of a copy
of each input row.
"""


def adapt_search_rows(rows):
    """Return a new list of dicts, one per input row, each a superset
    of the corresponding input row with 'snippet' and 'chunk_index'
    added.

    - snippet: str(row['body']), or '' if 'body' is missing.
    - chunk_index: int(row['start_line']) if that value is present and
      an integer (bool excluded), otherwise 0.

    Input rows are never mutated. An empty input returns [].
    """
    adapted = []
    for row in rows:
        new_row = dict(row)

        if "body" in row:
            new_row["snippet"] = str(row["body"])
        else:
            new_row["snippet"] = ""

        start_line = row.get("start_line")
        if isinstance(start_line, int) and not isinstance(start_line, bool):
            new_row["chunk_index"] = int(start_line)
        else:
            new_row["chunk_index"] = 0

        adapted.append(new_row)

    return adapted

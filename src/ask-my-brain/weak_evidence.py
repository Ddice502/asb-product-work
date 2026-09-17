"""Decide whether the retrieved evidence is strong enough to answer from.

This module adds a single pure function, evidence_verdict, that sits on top
of the existing deterministic HIGH/MEDIUM/LOW strength computation performed
elsewhere in ask_my_brain.py. It does not change or duplicate that
computation; it only consumes its result (the `strength` argument) alongside
the raw source rows to decide whether the system has enough grounding to
answer, and if not, what to say instead.

The function is intentionally side-effect free: it does not read or write
any file, does not touch a network, database, or vault, and never mutates
the `source_rows` argument passed in.

Decision rules (fail closed):
  1. If none of the source rows carry a usable path, there is no evidence
     at all: sufficient=False, reason='no_evidence'.
  2. If the (already computed) strength is 'MEDIUM' or 'HIGH' (case
     insensitive), the evidence is sufficient: sufficient=True,
     reason='sufficient', message=''.
  3. Otherwise -- including an explicit 'LOW' strength, or any strength
     value that is missing, empty, or unrecognised -- the function behaves
     exactly as it would for LOW:
       a. If there are two or more distinct evidence paths, the low-
          confidence evidence is considered corroborated by multiple
          independent sources: sufficient=True,
          reason='low_but_corroborated', message=''.
       b. Otherwise there is only a single note/source backing the answer:
          sufficient=False, reason='weak_evidence', with a non-empty
          message explaining that the evidence is too weak.

Every insufficient verdict carries a non-empty, human-readable message.
Every sufficient verdict carries an empty message.
"""

from typing import Any, Dict, Iterable, Optional


_RECOGNISED_STRONG_STRENGTHS = ("MEDIUM", "HIGH")

NO_EVIDENCE_MESSAGE = (
    "I could not find any evidence in your notes to answer this question, "
    "so I can't answer it."
)

WEAK_EVIDENCE_MESSAGE = (
    "The only evidence I found is a single low-confidence source, which is "
    "too weak to answer this question confidently."
)


def _extract_path(row: Any) -> Optional[str]:
    """Return the path carried by a source row, or None if it has none.

    Accepts either a mapping-like row (dict, or anything with .get) or an
    object exposing a `path` attribute. Never mutates the row.
    """
    path = None
    if isinstance(row, dict):
        path = row.get("path")
    else:
        get = getattr(row, "get", None)
        if callable(get):
            try:
                path = get("path")
            except TypeError:
                path = None
        else:
            path = getattr(row, "path", None)

    if not path:
        return None
    if not isinstance(path, str):
        path = str(path)
    path = path.strip()
    if not path:
        return None
    return path


def _normalize_strength(strength: Any) -> str:
    if not isinstance(strength, str):
        return ""
    return strength.strip().upper()


def evidence_verdict(source_rows: Optional[Iterable[Any]], strength: Any) -> Dict[str, Any]:
    """Decide if evidence is sufficient to answer, and what to say if not.

    Args:
        source_rows: an iterable of evidence rows (typically dicts) that may
            carry a 'path' entry identifying where the evidence came from.
            This argument is only read, never mutated.
        strength: the previously computed deterministic strength label,
            expected to be one of 'LOW', 'MEDIUM', or 'HIGH'. Any other
            value (including None, '', or an unrecognised string) is
            treated exactly as 'LOW' -- fail closed.

    Returns:
        A dict with keys:
            'sufficient': bool
            'reason': one of 'no_evidence', 'sufficient',
                      'low_but_corroborated', 'weak_evidence'
            'message': '' when sufficient is True, otherwise a non-empty
                       human-readable explanation.
    """
    rows = list(source_rows) if source_rows is not None else []

    distinct_paths = []
    seen = set()
    for row in rows:
        path = _extract_path(row)
        if path is None:
            continue
        if path not in seen:
            seen.add(path)
            distinct_paths.append(path)

    if not distinct_paths:
        return {
            "sufficient": False,
            "reason": "no_evidence",
            "message": NO_EVIDENCE_MESSAGE,
        }

    normalized_strength = _normalize_strength(strength)

    if normalized_strength in _RECOGNISED_STRONG_STRENGTHS:
        return {
            "sufficient": True,
            "reason": "sufficient",
            "message": "",
        }

    # Everything else -- explicit LOW, or missing/empty/unrecognised -- is
    # treated exactly like LOW, including its corroboration rule.
    if len(distinct_paths) >= 2:
        return {
            "sufficient": True,
            "reason": "low_but_corroborated",
            "message": "",
        }

    return {
        "sufficient": False,
        "reason": "weak_evidence",
        "message": WEAK_EVIDENCE_MESSAGE,
    }

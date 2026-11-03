"""Optional book details lookup on Open Library (https://openlibrary.org)."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from persephone import __version__

API_URL = "https://openlibrary.org/api/books"
USER_AGENT = f"Persephone/{__version__} (personal library app)"
TIMEOUT_SECONDS = 8

Fetcher = Callable[[str], Any]


class OpenLibraryError(RuntimeError):
    """Open Library could not be reached or answered something unexpected."""


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise OpenLibraryError(f"Open Library lookup failed: {exc}") from exc


def _year(text: str | None) -> int | None:
    match = re.search(r"\b(\d{4})\b", text or "")
    return int(match.group(1)) if match else None


def parse_book(isbn: str, record: dict[str, Any]) -> dict[str, Any]:
    """Turn an Open Library ``jscmd=data`` record into :class:`BookCreate` fields."""
    cover = record.get("cover") or {}
    publishers = record.get("publishers") or []
    return {
        "title": record.get("title") or "",
        "subtitle": record.get("subtitle"),
        "authors": [a["name"] for a in record.get("authors") or [] if a.get("name")],
        "isbn": isbn,
        "publisher": publishers[0].get("name") if publishers else None,
        "published_year": _year(record.get("publish_date")),
        "pages": record.get("number_of_pages"),
        "cover_url": cover.get("large") or cover.get("medium") or cover.get("small"),
    }


def lookup_isbn(isbn: str, fetch: Fetcher = _fetch_json) -> dict[str, Any] | None:
    """Return book fields for a (normalized) ISBN, or ``None`` when it is unknown."""
    query = urllib.parse.urlencode({"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"})
    payload = fetch(f"{API_URL}?{query}")
    if not isinstance(payload, dict):
        raise OpenLibraryError("Open Library answered with an unexpected payload")
    record = payload.get(f"ISBN:{isbn}")
    if not record:
        return None
    return parse_book(isbn, record)

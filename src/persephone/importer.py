"""Import books from the files in a folder (by default ``info/``).

Understood formats:

* **Goodreads export** (``goodreads_library_export.csv``): ratings, shelves,
  read dates, reviews and owned copies are all carried over. Series written in
  the title, like ``Dune (Dune Chronicles, #1)``, are split out.
* **Generic CSV** with a header row. Column names are matched loosely
  (``author``/``authors``, ``isbn``/``isbn13``, ``year``/``published_year``...);
  see :data:`GENERIC_COLUMNS`. Lists (authors, tags) are separated by ``;`` or ``|``.
* **JSON**: a list of book objects using the same field names, or a file made by
  ``python -m persephone.userdata export``.

Re-running an import is safe: books already in the library are recognized (by
ISBN, or by title and author) and only get their missing details filled in.

    python -m persephone.importer info [--owner NAME]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from persephone import isbn as isbn_utils
from persephone import userdata
from persephone.cli import add_common_arguments, open_from_args
from persephone.db import session_scope
from persephone.schemas import Format, ImportSummary, Status

# ---------------------------------------------------------------- parsing ---

_TRUE = {"1", "true", "yes", "y", "x", "si", "sí", "owned", "★", "♥", "fav", "favorite"}
_FALSE = {"0", "false", "no", "n", "", "-"}
_LIST_SPLIT = re.compile(r"\s*[;|]\s*")
_TAG_SPLIT = re.compile(r"\s*[;|,]\s*")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"-?\d+", _text(value) or "")
    return int(match.group()) if match else None


def _float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = re.search(r"-?\d+(?:[.,]\d+)?", _text(value) or "")
    return float(match.group().replace(",", ".")) if match else None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = (_text(value) or "").casefold()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    number = _int(text)
    return number > 0 if number is not None else None


def _date(value: Any) -> dt.date | None:
    text = _text(value)
    if not text:
        return None
    for candidate in dict.fromkeys((text, text[:10])):  # "2024/05/01 10:00" -> "2024/05/01"
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                return dt.datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _rating(value: Any) -> float | None:
    number = _float(value)
    if number is None or number <= 0:
        return None
    if number > 5:  # a 0-10 scale
        number = number / 2
    return min(5.0, max(0.5, round(number * 2) / 2))


def _list(value: Any, splitter: re.Pattern[str] = _LIST_SPLIT) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    text = _text(value)
    return [part for part in splitter.split(text) if part] if text else []


_STATUS_WORDS = {
    Status.READ: {"read", "finished", "done", "leído", "leido", "completed"},
    Status.READING: {"reading", "currently-reading", "currently reading", "in progress", "leyendo"},
    Status.ABANDONED: {"abandoned", "dnf", "did-not-finish", "did not finish", "abandonado"},
    Status.TO_READ: {"to-read", "to read", "to_read", "want to read", "unread", "tbr", "pending"},
}


def _status(value: Any) -> Status | None:
    text = (_text(value) or "").casefold()
    for status, words in _STATUS_WORDS.items():
        if text in words or text == status.value:
            return status
    return None


def _format(value: Any) -> Format:
    text = (_text(value) or "").casefold()
    if "audio" in text:
        return Format.AUDIOBOOK
    if any(word in text for word in ("kindle", "ebook", "e-book", "epub", "digital", "nook")):
        return Format.EBOOK
    if "hard" in text or "tapa dura" in text:
        return Format.HARDCOVER
    if "paper" in text or "rústica" in text or "rustica" in text or "bolsillo" in text:
        return Format.PAPERBACK
    if not text:
        return Format.PAPERBACK
    return Format.OTHER


_SERIES_IN_TITLE = re.compile(r"^(?P<title>.+?)\s*\((?P<series>[^()]+?),?\s*#(?P<index>[\d.]+)\)$")


def split_series(title: str) -> tuple[str, str | None, float | None]:
    """``"Dune (Dune Chronicles, #1)"`` -> ``("Dune", "Dune Chronicles", 1.0)``."""
    match = _SERIES_IN_TITLE.match(title.strip())
    if not match:
        return title.strip(), None, None
    try:
        index = float(match["index"])
    except ValueError:
        index = None
    return match["title"].strip(), match["series"].strip(), index


def _copies(count: int, fmt: Format, owner: str | None, location: str | None = None):
    return [{"owner": owner, "format": fmt.value, "location": location} for _ in range(count)]


# -------------------------------------------------------------- goodreads ---

GOODREADS_COLUMNS = {"Title", "Author", "Exclusive Shelf"}
_GOODREADS_BUILTIN_SHELVES = {"read", "currently-reading", "to-read"}
_FAVORITE_SHELVES = {"favorites", "favourites", "favorite", "favourite", "favoritos"}


def is_goodreads(fieldnames: Iterable[str]) -> bool:
    return set(fieldnames) >= GOODREADS_COLUMNS


def goodreads_record(row: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    title, series, series_index = split_series(row.get("Title") or "")
    authors = [_text(row.get("Author")), *_list(row.get("Additional Authors"), _TAG_SPLIT)]
    exclusive = (_text(row.get("Exclusive Shelf")) or "to-read").casefold()
    shelves = [s.casefold() for s in _list(row.get("Bookshelves"), _TAG_SPLIT)]
    tags = [
        shelf
        for shelf in shelves
        if shelf not in _GOODREADS_BUILTIN_SHELVES
        and shelf not in _FAVORITE_SHELVES
        and shelf != exclusive
    ]
    review = _text(row.get("My Review"))
    if review:
        review = re.sub(r"<br\s*/?>", "\n", review).strip()
    status = _status(exclusive) or Status.TO_READ
    owned = _int(row.get("Owned Copies")) or 0
    added = _date(row.get("Date Added"))
    return {
        "title": title,
        "series": series,
        "series_index": series_index,
        "authors": [a for a in authors if a],
        "isbn": isbn_utils.normalize(row.get("ISBN13")) or isbn_utils.normalize(row.get("ISBN")),
        "publisher": _text(row.get("Publisher")),
        "published_year": _int(row.get("Year Published"))
        or _int(row.get("Original Publication Year")),
        "pages": _int(row.get("Number of Pages")) or None,
        "rating": _rating(row.get("My Rating")),
        "status": status.value,
        "favorite": bool(set(shelves) & _FAVORITE_SHELVES),
        "finished_on": _date(row.get("Date Read")) if status == Status.READ else None,
        "review": review,
        "tags": tags,
        "copies": _copies(owned, _format(row.get("Binding")), owner),
        "added_at": str(added) if added else None,
    }


# ---------------------------------------------------------------- generic ---

GENERIC_COLUMNS: dict[str, tuple[str, ...]] = {
    "title": ("title", "name", "book", "titulo", "título"),
    "subtitle": ("subtitle",),
    "authors": ("authors", "author", "writer", "autor", "autores"),
    "isbn": ("isbn13", "isbn", "isbn10", "isbn_13", "isbn_10", "ean"),
    "publisher": ("publisher", "editorial"),
    "published_year": ("published_year", "year", "publication year", "year published", "año"),
    "pages": ("pages", "page count", "number of pages", "páginas", "paginas"),
    "language": ("language", "lang", "idioma"),
    "description": ("description", "summary", "synopsis"),
    "cover_url": ("cover_url", "cover", "image", "thumbnail"),
    "series": ("series", "saga", "serie"),
    "series_index": ("series_index", "series number", "volume", "number in series"),
    "status": ("status", "shelf", "state", "estado"),
    "rating": ("rating", "my rating", "stars", "score"),
    "favorite": ("favorite", "favourite", "fav", "favorito"),
    "tags": ("tags", "genres", "genre", "categories", "bookshelves", "género", "genero"),
    "started_on": ("started_on", "started", "date started", "start date"),
    "finished_on": ("finished_on", "finished", "date read", "date finished", "end date"),
    "review": ("review", "notes", "comments", "my review", "notas"),
    "owner": ("owner", "owned by", "dueño"),
    "format": ("format", "binding", "type", "formato"),
    "location": ("location", "where", "place", "ubicación", "ubicacion"),
    "owned": ("owned", "owned copies", "copies", "own", "tengo"),
    "added_at": ("added_at", "date added", "added"),
}
_ALIASES = {alias: field for field, aliases in GENERIC_COLUMNS.items() for alias in aliases}


def _canonical(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        if key is None:
            continue
        field = _ALIASES.get(str(key).strip().casefold().replace("-", " "))
        field = field or _ALIASES.get(str(key).strip().casefold())
        if field and field not in result:
            result[field] = value
    return result


def generic_record(row: dict[str, Any], owner: str | None = None) -> dict[str, Any]:
    row = _canonical(row)
    title, series, series_index = split_series(_text(row.get("title")) or "")

    copies_value = row.get("owned")
    if isinstance(copies_value, list):  # already a list of copies (JSON)
        copies = copies_value
    else:
        count = _int(copies_value)
        flag = _bool(copies_value)
        if count is None:
            count = 1 if flag else 0
        has_copy_details = any(_text(row.get(k)) for k in ("owner", "format", "location"))
        if copies_value in (None, "") and has_copy_details:
            count = 1
        copies = _copies(
            count,
            _format(row.get("format")),
            _text(row.get("owner")) or owner,
            _text(row.get("location")),
        )

    status = _status(row.get("status"))
    if status is None:
        status = Status.READ if _date(row.get("finished_on")) else Status.TO_READ
    added = _date(row.get("added_at"))
    return {
        "title": title,
        "subtitle": _text(row.get("subtitle")),
        "authors": _list(row.get("authors")),
        "isbn": isbn_utils.normalize(_text(row.get("isbn"))),
        "publisher": _text(row.get("publisher")),
        "published_year": _int(row.get("published_year")),
        "pages": _int(row.get("pages")) or None,
        "language": _text(row.get("language")),
        "description": _text(row.get("description")),
        "cover_url": _text(row.get("cover_url")),
        "series": _text(row.get("series")) or series,
        "series_index": _float(row.get("series_index")) or series_index,
        "status": status.value,
        "rating": _rating(row.get("rating")),
        "favorite": bool(_bool(row.get("favorite"))),
        "tags": _list(row.get("tags"), _TAG_SPLIT),
        "started_on": _date(row.get("started_on")),
        "finished_on": _date(row.get("finished_on")),
        "review": _text(row.get("review")),
        "copies": copies,
        "added_at": str(added) if added else None,
    }


# ------------------------------------------------------------------ files ---


def read_csv(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    text = path.read_text("utf-8-sig")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    rows = [row for row in reader if any((v or "").strip() for v in row.values() if v)]
    return list(reader.fieldnames or []), rows


def _json_records(payload: Any) -> list[Any]:
    if isinstance(payload, dict) and payload.get("format") == userdata.FORMAT_NAME:
        return userdata._records(payload)
    if isinstance(payload, dict) and isinstance(payload.get("books"), list):
        return payload["books"]
    if isinstance(payload, list):
        return payload
    raise userdata.FormatError("expected a list of books")


def import_file(session: Session, path: Path, *, default_owner: str) -> ImportSummary:
    """Import one ``.csv`` or ``.json`` file. Existing books keep their edits."""
    if path.suffix.lower() == ".csv":
        fieldnames, rows = read_csv(path)
        to_record = goodreads_record if is_goodreads(fieldnames) else generic_record
        records: list[Any] = [to_record(row, default_owner) for row in rows]
        first_row = 2  # line 1 is the header
        unit = "row"
    elif path.suffix.lower() == ".json":
        payload = json.loads(path.read_text("utf-8"))
        records = [
            generic_record(item, default_owner) if isinstance(item, dict) else item
            for item in _json_records(payload)
        ]
        first_row = 1
        unit = "book"
    else:
        raise userdata.FormatError(f"unsupported file type: {path.suffix or path.name}")

    summary = ImportSummary()
    for offset, record in enumerate(records):
        part = userdata.import_data(
            session,
            [record],
            default_owner=default_owner,
            overwrite_personal=False,
            source=f"{path.name} {unit} {first_row + offset}",
        )
        part.errors = [error.replace(" #1", "", 1) for error in part.errors]
        summary = summary.merge(part)
    return summary


def import_folder(session: Session, folder: Path, *, default_owner: str, out=None) -> ImportSummary:
    out = out or sys.stdout
    files = sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".csv", ".json"}
    )
    if not files:
        print(f"No .csv or .json files found in {folder}", file=out)
    summary = ImportSummary()
    for path in files:
        try:
            part = import_file(session, path, default_owner=default_owner)
        except (json.JSONDecodeError, UnicodeDecodeError, userdata.FormatError) as exc:
            part = ImportSummary(errors=[f"{path.name}: {exc}"])
        print(f"{path.name}: ", end="", file=out)
        userdata.print_summary(part, out=out)
        summary = summary.merge(part)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m persephone.importer",
        description="Import books from the .csv and .json files of a folder.",
    )
    parser.add_argument("folder", nargs="?", default="info", help="folder to read (default: info)")
    parser.add_argument("--owner", help="owner of the imported copies (default: PERSEPHONE_OWNER)")
    add_common_arguments(parser)
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"No such folder: {folder}", file=sys.stderr)
        return 2
    sessions, settings = open_from_args(args)
    with session_scope(sessions) as session:
        summary = import_folder(session, folder, default_owner=args.owner or settings.default_owner)
    print("Total: ", end="")
    userdata.print_summary(summary, show_errors=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

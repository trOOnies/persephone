"""Back up and restore your library as a JSON file.

The export holds everything Persephone knows about each book (catalog details,
status, rating, favorite, review, tags and copies), so it can rebuild a library
from scratch or be merged into an existing one.

    python -m persephone.userdata export [--owner NAME] [--output FILE]
    python -m persephone.userdata import FILE

With ``--owner`` only that person's copies are written, and the file is
labelled with their name: handy to hand over "your" part of a shared library.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from persephone import __version__, services
from persephone.cli import add_common_arguments, open_from_args
from persephone.db import session_scope
from persephone.models import Book
from persephone.schemas import BookCreate, BookOut, ImportSummary

FORMAT_NAME = "persephone-export"
FORMAT_VERSION = 1
EXPORT_DIR = Path("local")

_BOOK_KEYS = (
    "title",
    "subtitle",
    "authors",
    "isbn",
    "publisher",
    "published_year",
    "pages",
    "language",
    "description",
    "cover_url",
    "series",
    "series_index",
    "original_title",
    "original_language",
    "original_year",
    "translator",
    "tags",
    "status",
    "rating",
    "favorite",
    "started_on",
    "finished_on",
    "review",
)
_COPY_KEYS = ("owner", "format", "location", "acquired_on", "lent_to", "notes")


class FormatError(ValueError):
    """The payload is not a Persephone export."""


def book_record(book: Book, owner: str | None = None) -> dict[str, Any]:
    data = BookOut.model_validate(book).model_dump(mode="json")
    record = {key: data[key] for key in _BOOK_KEYS}
    record["added_at"] = data["created_at"]
    record["copies"] = [
        {key: copy[key] for key in _COPY_KEYS}
        for copy in data["copies"]
        if owner is None or copy["owner"].casefold() == owner.casefold()
    ]
    return record


def export_data(session: Session, owner: str | None = None) -> dict[str, Any]:
    owner = owner.strip() if owner and owner.strip() else None
    books = session.scalars(
        select(Book).options(*services.book_load_options()).order_by(Book.id)
    ).unique()
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "owner": owner,
        "books": [book_record(book, owner) for book in books],
    }


def default_filename(owner: str | None, today: dt.date) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (owner or "").lower()).strip("-")
    return f"persephone-export{'-' + slug if slug else ''}-{today:%Y%m%d}.json"


def _records(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and payload.get("format") == FORMAT_NAME:
        version = payload.get("version")
        if not isinstance(version, int) or version > FORMAT_VERSION:
            raise FormatError(f"unsupported export version {version!r}")
        books = payload.get("books")
        if not isinstance(books, list):
            raise FormatError("the export has no 'books' list")
        return books
    raise FormatError("this is not a Persephone export")


def _parse_added_at(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def import_data(
    session: Session,
    payload: Any,
    *,
    default_owner: str,
    overwrite_personal: bool = True,
    source: str = "book",
) -> ImportSummary:
    """Merge exported books into the library.

    Existing books (same ISBN, or same title and author) get their personal data
    replaced by the file's, missing details filled in, tags added and missing
    copies created. Unknown books are created.
    """
    summary = ImportSummary()
    for index, record in enumerate(_records(payload), start=1):
        label = f"{source} #{index}"
        if isinstance(record, dict) and record.get("title"):
            label += f" ({record['title']})"
        try:
            data = BookCreate.model_validate(record)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'record'}: {err['msg']}"
                for err in exc.errors()
            )
            summary.errors.append(f"{label}: {problems}")
            continue
        book, result = services.upsert_book(
            session, data, default_owner=default_owner, overwrite_personal=overwrite_personal
        )
        if result == "created" and isinstance(record, dict):
            added_at = _parse_added_at(record.get("added_at"))
            if added_at is not None:
                book.created_at = added_at
        setattr(summary, result, getattr(summary, result) + 1)
    session.flush()
    return summary


def print_summary(summary: ImportSummary, out=None, *, show_errors: bool = True) -> None:
    out = out or sys.stdout
    print(
        f"{summary.created} created, {summary.updated} updated, "
        f"{summary.unchanged} unchanged, {len(summary.errors)} errors",
        file=out,
    )
    if show_errors:
        for error in summary.errors:
            print(f"  ! {error}", file=out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m persephone.userdata", description="Back up or restore your library."
    )
    add_common_arguments(parser)
    commands = parser.add_subparsers(dest="command", required=True)

    export_cmd = commands.add_parser("export", help="write your library to a JSON file")
    export_cmd.add_argument("--owner", help="only keep the copies owned by this person")
    export_cmd.add_argument("--output", "-o", help="file to write (default: local/...json)")

    import_cmd = commands.add_parser("import", help="merge a JSON export into your library")
    import_cmd.add_argument("file", help="the JSON file written by 'export'")

    args = parser.parse_args(argv)
    sessions, settings = open_from_args(args)

    if args.command == "export":
        with session_scope(sessions) as session:
            payload = export_data(session, owner=args.owner)
        output = Path(args.output or EXPORT_DIR / default_filename(args.owner, dt.date.today()))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", "utf-8")
        print(f"Exported {len(payload['books'])} books to {output}")
        return 0

    path = Path(args.file)
    if not args.file or not path.is_file():
        print(f"No such file: {args.file!r}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(path.read_text("utf-8"))
        with session_scope(sessions) as session:
            summary = import_data(session, payload, default_owner=settings.default_owner)
    except (json.JSONDecodeError, FormatError) as exc:
        print(f"Cannot import {path}: {exc}", file=sys.stderr)
        return 1
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

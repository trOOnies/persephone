"""Keep a local copy of every book cover, so covers survive broken links.

A book's cover comes from its ``cover_url`` or, when it has none, from Open
Library by ISBN. The image is downloaded once into the covers folder (next to
the database, e.g. ``local/covers/``) as ``<book id>-<content hash>.<ext>``.
``Book.cover_file`` names that file and ``Book.cover_source`` remembers which
URL it came from, so a new ``cover_url`` is downloaded again. A ``cover_source``
without a ``cover_file`` means that URL had no image (Open Library has no cover
for every ISBN); it is only asked again when the source changes or with ``--force``.

    python -m persephone.covers [--db PATH] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session, object_session, sessionmaker
from sqlalchemy.orm.attributes import set_committed_value

from persephone import __version__
from persephone.cli import add_common_arguments, open_from_args
from persephone.config import Settings
from persephone.db import session_scope
from persephone.models import Book

USER_AGENT = f"Persephone/{__version__} (personal library app)"
TIMEOUT_SECONDS = 15
MAX_BYTES = 8 * 1024 * 1024
EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}
URL_PREFIX = "/covers/"

#: ``fetch(url) -> (content type, bytes)``, or ``None`` when there is no image there.
Fetcher = Callable[[str], "tuple[str, bytes] | None"]


class CoverError(RuntimeError):
    """The cover could not be downloaded (network error, not an image, too big...)."""


def fetch_image(url: str) -> tuple[str, bytes] | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type()
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise CoverError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CoverError(f"{url}: {exc}") from exc
    if len(data) > MAX_BYTES:
        raise CoverError(f"{url}: larger than {MAX_BYTES // (1024 * 1024)} MB")
    return content_type, data


def source_url(book: Book, settings: Settings) -> str | None:
    """Where the book's cover comes from, or ``None`` when it has no cover."""
    if book.cover_url:
        return book.cover_url if book.cover_url.startswith(("http://", "https://")) else None
    if book.isbn and settings.openlibrary:
        isbn = urllib.parse.quote(book.isbn)
        return f"https://covers.openlibrary.org/b/isbn/{isbn}-L.jpg?default=false"
    return None


def needs_download(book: Book, settings: Settings) -> bool:
    source = source_url(book, settings)
    if source is None:
        return False
    if book.cover_source != source:
        return True
    if not book.cover_file:
        return False  # already checked: there is no image there
    return not (settings.covers_dir / book.cover_file).is_file()


def unavailable(book: Book, settings: Settings) -> bool:
    """The book has a cover source, but there was no image there when last checked."""
    source = source_url(book, settings)
    return source is not None and book.cover_source == source and not book.cover_file


def _image_type(content_type: str, data: bytes) -> str | None:
    """The image's extension, trusting the bytes over the server's content type."""
    signatures = {
        b"\xff\xd8\xff": ".jpg",
        b"\x89PNG\r\n\x1a\n": ".png",
        b"GIF87a": ".gif",
        b"GIF89a": ".gif",
    }
    for magic, extension in signatures.items():
        if data.startswith(magic):
            return extension
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return ".avif"
    return EXTENSIONS.get(content_type) if content_type.startswith("image/") else None


def _store(book: Book, cover_file: str | None, cover_source: str | None) -> None:
    """Record the local cover without counting it as an edit of the book.

    A plain attribute change would bump ``updated_at``; cover bookkeeping is not a
    change the reader made.
    """
    session = object_session(book)
    if session is not None and book.id is not None:
        session.execute(
            update(Book)
            .where(Book.id == book.id)
            .values(cover_file=cover_file, cover_source=cover_source, updated_at=Book.updated_at)
        )
    set_committed_value(book, "cover_file", cover_file)
    set_committed_value(book, "cover_source", cover_source)


def _remove(folder: Path, name: str | None) -> None:
    if name:
        (folder / name).unlink(missing_ok=True)


def download(book: Book, settings: Settings, fetch: Fetcher = fetch_image) -> bool:
    """Download the book's cover if needed; return whether a new file was saved.

    Clears the local cover when the book no longer has one. Raises
    :class:`CoverError` when the download fails, leaving any old file in place.
    """
    folder = settings.covers_dir
    source = source_url(book, settings)
    if source is None:
        if book.cover_file or book.cover_source:
            _remove(folder, book.cover_file)
            _store(book, None, None)
        return False
    if not needs_download(book, settings):
        return False

    found = fetch(source)
    if found is None:  # e.g. Open Library has no cover for this ISBN: remember that
        _remove(folder, book.cover_file)
        _store(book, None, source)
        return False
    content_type, data = found
    extension = _image_type(content_type, data)
    if extension is None or len(data) < 100:
        raise CoverError(f"{source}: not an image ({content_type})")

    folder.mkdir(parents=True, exist_ok=True)
    name = f"{book.id}-{hashlib.sha256(data).hexdigest()[:16]}{extension}"
    partial = folder / f".{name}.part"
    partial.write_bytes(data)
    os.replace(partial, folder / name)
    if book.cover_file != name:
        _remove(folder, book.cover_file)
    _store(book, name, source)
    return True


@dataclass
class CoverSummary:
    saved: int = 0
    unchanged: int = 0
    without_cover: int = 0
    errors: list[str] = field(default_factory=list)


def download_all(
    sessions: sessionmaker[Session],
    settings: Settings,
    *,
    book_ids: list[int] | None = None,
    force: bool = False,
    fetch: Fetcher = fetch_image,
) -> CoverSummary:
    """Download the covers of ``book_ids`` (default: every book), one commit per book.

    Also removes files in the covers folder that no book uses any more.
    """
    summary = CoverSummary()
    with session_scope(sessions) as session:
        ids = (
            list(book_ids)
            if book_ids is not None
            else list(session.scalars(select(Book.id).order_by(Book.id)))
        )
    for book_id in ids:
        with session_scope(sessions) as session:
            book = session.get(Book, book_id)
            if book is None:
                continue
            if force:
                _store(book, book.cover_file, None)
            try:
                if download(book, settings, fetch):
                    summary.saved += 1
                elif book.cover_file:
                    summary.unchanged += 1
                else:
                    summary.without_cover += 1
            except CoverError as exc:
                summary.errors.append(f"{book.title}: {exc}")
    if book_ids is None:
        _remove_unused(sessions, settings)
    return summary


def _remove_unused(sessions: sessionmaker[Session], settings: Settings) -> None:
    folder = settings.covers_dir
    if not folder.is_dir():
        return
    with session_scope(sessions) as session:
        used = set(session.scalars(select(Book.cover_file).where(Book.cover_file.is_not(None))))
    for path in folder.iterdir():
        if path.is_file() and path.name not in used:
            path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m persephone.covers",
        description="Save a local copy of every book cover.",
    )
    add_common_arguments(parser)
    parser.add_argument("--force", action="store_true", help="download every cover again")
    args = parser.parse_args(argv)
    sessions, settings = open_from_args(args)
    summary = download_all(sessions, settings, force=args.force, fetch=fetch_image)
    print(
        f"Covers in {settings.covers_dir}: {summary.saved} saved, "
        f"{summary.unchanged} already there, {summary.without_cover} books without a cover"
        " (no link, or none found online)."
    )
    for error in summary.errors:
        print(f"  ! {error}")
    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

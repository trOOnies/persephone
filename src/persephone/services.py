"""Business logic shared by the web API, the importers and the CLI tools."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from persephone.models import Author, Book, BookAuthor, Copy, Publisher, Tag, book_tags, utcnow
from persephone.schemas import (
    AuthorDetails,
    AuthorOut,
    BookCreate,
    BookUpdate,
    CopyCreate,
    CopyUpdate,
    Format,
    NameCount,
    PublisherDetails,
    PublisherOut,
    Stats,
    Status,
    YearCount,
)


class NotFoundError(LookupError):
    """The requested object does not exist."""


class ConflictError(ValueError):
    """The change would break a uniqueness rule (e.g. a duplicated ISBN)."""


CATALOG_FIELDS = (
    "subtitle",
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
)
AUTHOR_FIELDS = ("gender", "origin", "year_born", "year_deceased")
PUBLISHER_FIELDS = ("origin", "year_created", "year_closed")
PERSONAL_FIELDS = ("status", "rating", "favorite", "started_on", "finished_on", "review")
COPY_FIELDS = ("owner", "format", "location", "acquired_on", "lent_to", "notes")

SortKey = Literal["title", "author", "added", "updated", "rating", "year", "finished"]
SORT_KEYS: tuple[str, ...] = SortKey.__args__  # type: ignore[attr-defined]


def book_load_options():
    return (
        selectinload(Book.author_links).joinedload(BookAuthor.author),
        selectinload(Book.tags),
        selectinload(Book.copies),
    )


def _value(value):
    """Store enums as their plain string value."""
    return value.value if hasattr(value, "value") else value


# --------------------------------------------------------- authors & tags ---


def _get_or_create_author(session: Session, name: str) -> Author:
    author = session.scalar(select(Author).where(func.lower(Author.name) == name.lower()))
    if author is None:
        author = Author(name=name)
        session.add(author)
        session.flush()
    return author


def _get_or_create_tag(session: Session, name: str) -> Tag:
    tag = session.scalar(select(Tag).where(func.lower(Tag.name) == name.lower()))
    if tag is None:
        tag = Tag(name=name)
        session.add(tag)
        session.flush()
    return tag


def set_authors(session: Session, book: Book, names: Sequence[str]) -> None:
    authors = [_get_or_create_author(session, name) for name in names]
    existing = {link.author_id: link for link in book.author_links}
    links: list[BookAuthor] = []
    for position, author in enumerate(authors):
        link = existing.pop(author.id, None) or BookAuthor(author=author)
        link.position = position
        links.append(link)
    book.author_links = links


def set_tags(session: Session, book: Book, names: Sequence[str]) -> None:
    # Same order as the relationship's ORDER BY, so fresh and reloaded books agree.
    book.tags = sorted((_get_or_create_tag(session, name) for name in names), key=lambda t: t.name)


def prune_orphans(session: Session) -> None:
    """Delete authors and tags that no book references any more."""
    session.flush()
    session.execute(delete(Author).where(~Author.id.in_(select(BookAuthor.author_id).distinct())))
    session.execute(delete(Tag).where(~Tag.id.in_(select(book_tags.c.tag_id).distinct())))


# ------------------------------------------------------------------ books ---


def get_book(session: Session, book_id: int) -> Book:
    book = session.scalar(select(Book).options(*book_load_options()).where(Book.id == book_id))
    if book is None:
        raise NotFoundError(f"book {book_id} not found")
    return book


def _ensure_isbn_free(session: Session, isbn: str | None, book_id: int | None = None) -> None:
    if not isbn:
        return
    query = select(Book.id).where(Book.isbn == isbn)
    if book_id is not None:
        query = query.where(Book.id != book_id)
    if session.scalar(query) is not None:
        raise ConflictError(f"another book already has ISBN {isbn}")


def _new_copy(data: CopyCreate, default_owner: str) -> Copy:
    fields = {name: _value(getattr(data, name)) for name in COPY_FIELDS if name != "owner"}
    return Copy(owner=data.owner or default_owner, **fields)


def create_book(session: Session, data: BookCreate, default_owner: str) -> Book:
    _ensure_isbn_free(session, data.isbn)
    book = Book(
        title=data.title,
        **{name: _value(getattr(data, name)) for name in (*CATALOG_FIELDS, *PERSONAL_FIELDS)},
    )
    session.add(book)
    set_authors(session, book, data.authors)
    set_tags(session, book, data.tags)
    book.copies = [_new_copy(copy, default_owner) for copy in data.copies]
    session.flush()
    return book


def update_book(session: Session, book: Book, data: BookUpdate) -> Book:
    changes = data.model_dump(exclude_unset=True)
    authors = changes.pop("authors", None)
    tags = changes.pop("tags", None)

    if "isbn" in changes and changes["isbn"] != book.isbn:
        _ensure_isbn_free(session, changes["isbn"], book.id)

    started = changes.get("started_on", book.started_on)
    finished = changes.get("finished_on", book.finished_on)
    if started and finished and finished < started:
        raise ValueError("finished_on cannot be before started_on")

    for name, value in changes.items():
        setattr(book, name, _value(value))
    if authors is not None:
        set_authors(session, book, authors)
    if tags is not None:
        set_tags(session, book, tags)
    book.updated_at = utcnow()
    prune_orphans(session)
    session.flush()
    return book


def delete_book(session: Session, book: Book) -> None:
    session.delete(book)
    prune_orphans(session)


@dataclass
class BookFilters:
    q: str | None = None
    status: Status | None = None
    favorite: bool | None = None
    owned: bool | None = None
    owner: str | None = None
    author: str | None = None
    tag: str | None = None
    min_rating: float | None = None
    lent: bool | None = None


def _books_by_author(condition) -> Select:
    return (
        select(BookAuthor.book_id).join(Author, Author.id == BookAuthor.author_id).where(condition)
    )


def _apply_filters(query: Select, f: BookFilters) -> Select:
    if f.q:
        for term in f.q.split():
            like = f"%{term}%"
            query = query.where(
                or_(
                    Book.title.ilike(like),
                    Book.subtitle.ilike(like),
                    Book.series.ilike(like),
                    Book.publisher.ilike(like),
                    Book.isbn.ilike(like),
                    Book.id.in_(_books_by_author(Author.name.ilike(like))),
                )
            )
    if f.status is not None:
        query = query.where(Book.status == _value(f.status))
    if f.favorite is not None:
        query = query.where(Book.favorite.is_(f.favorite))
    if f.owned is not None:
        has_copy = Book.copies.any()
        query = query.where(has_copy if f.owned else ~has_copy)
    if f.owner:
        query = query.where(Book.copies.any(func.lower(Copy.owner) == f.owner.lower()))
    if f.lent is not None:
        lent_copy = Book.copies.any(Copy.lent_to.is_not(None))
        query = query.where(lent_copy if f.lent else ~lent_copy)
    if f.author:
        query = query.where(
            Book.id.in_(_books_by_author(func.lower(Author.name) == f.author.lower()))
        )
    if f.tag:
        query = query.where(Book.tags.any(func.lower(Tag.name) == f.tag.lower()))
    if f.min_rating is not None:
        query = query.where(Book.rating >= f.min_rating)
    return query


def _sort_columns(sort: str, descending: bool):
    first_author = (
        select(func.lower(Author.name))
        .join(BookAuthor, BookAuthor.author_id == Author.id)
        .where(BookAuthor.book_id == Book.id)
        .order_by(BookAuthor.position)
        .limit(1)
        .correlate(Book)
        .scalar_subquery()
    )
    column = {
        "title": func.lower(Book.title),
        "author": first_author,
        "added": Book.created_at,
        "updated": Book.updated_at,
        "rating": Book.rating,
        "year": Book.published_year,
        "finished": Book.finished_on,
    }[sort]
    primary = column.desc() if descending else column.asc()
    return [primary.nulls_last(), func.lower(Book.title).asc(), Book.id.asc()]


def list_books(
    session: Session,
    filters: BookFilters | None = None,
    *,
    sort: str = "title",
    descending: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Book], int]:
    if sort not in SORT_KEYS:
        raise ValueError(f"unknown sort key {sort!r}")
    filters = filters or BookFilters()
    ids = _apply_filters(select(Book.id), filters)
    total = session.scalar(select(func.count()).select_from(ids.subquery())) or 0
    query = (
        _apply_filters(select(Book), filters)
        .options(*book_load_options())
        .order_by(*_sort_columns(sort, descending))
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(query).unique()), total


# ----------------------------------------------------------------- copies ---


def get_copy(session: Session, copy_id: int) -> Copy:
    copy = session.get(Copy, copy_id)
    if copy is None:
        raise NotFoundError(f"copy {copy_id} not found")
    return copy


def add_copy(session: Session, book: Book, data: CopyCreate, default_owner: str) -> Copy:
    copy = _new_copy(data, default_owner)
    book.copies.append(copy)
    book.updated_at = utcnow()
    session.flush()
    return copy


def update_copy(session: Session, copy: Copy, data: CopyUpdate) -> Copy:
    for name, value in data.model_dump(exclude_unset=True).items():
        setattr(copy, name, _value(value))
    copy.book.updated_at = utcnow()
    session.flush()
    return copy


def delete_copy(session: Session, copy: Copy) -> None:
    copy.book.updated_at = utcnow()
    session.delete(copy)
    session.flush()


# ----------------------------------------------------------------- lookup ---


def list_authors(session: Session) -> list[AuthorOut]:
    rows = session.execute(
        select(Author, func.count(BookAuthor.book_id))
        .join(BookAuthor, BookAuthor.author_id == Author.id)
        .group_by(Author.id)
        .order_by(func.lower(Author.name))
    )
    return [
        AuthorOut(name=author.name, count=count, **{f: getattr(author, f) for f in AUTHOR_FIELDS})
        for author, count in rows
    ]


def list_publishers(session: Session) -> list[PublisherOut]:
    """Every publisher named by a book or described in the publishers table."""
    counts = session.execute(
        select(Book.publisher, func.count(Book.id))
        .where(Book.publisher.is_not(None))
        .group_by(func.lower(Book.publisher))
    )
    found: dict[str, PublisherOut] = {
        name.casefold(): PublisherOut(name=name, count=count) for name, count in counts
    }
    for publisher in session.scalars(select(Publisher)):
        details = {f: getattr(publisher, f) for f in PUBLISHER_FIELDS}
        key = publisher.name.casefold()
        count = found[key].count if key in found else 0
        found[key] = PublisherOut(name=publisher.name, count=count, **details)
    return sorted(found.values(), key=lambda item: item.name.casefold())


def list_tags(session: Session) -> list[NameCount]:
    rows = session.execute(
        select(Tag.name, func.count(book_tags.c.book_id))
        .join(book_tags, book_tags.c.tag_id == Tag.id)
        .group_by(Tag.id)
        .order_by(func.lower(Tag.name))
    )
    return [NameCount(name=name, count=count) for name, count in rows]


def list_owners(session: Session) -> list[NameCount]:
    rows = session.execute(
        select(Copy.owner, func.count(Copy.id)).group_by(Copy.owner).order_by(Copy.owner)
    )
    return [NameCount(name=name, count=count) for name, count in rows]


def _counts(session: Session, column, model) -> dict[str, int]:
    rows = session.execute(select(column, func.count()).select_from(model).group_by(column))
    return {str(_value(key)): count for key, count in rows}


def stats(session: Session) -> Stats:
    total = session.scalar(select(func.count(Book.id))) or 0
    owned = session.scalar(select(func.count(Book.id)).where(Book.copies.any())) or 0
    copies = session.scalar(select(func.count(Copy.id))) or 0
    favorites = session.scalar(select(func.count(Book.id)).where(Book.favorite)) or 0
    rated, average = session.execute(select(func.count(Book.rating), func.avg(Book.rating))).one()
    pages_read = (
        session.scalar(
            select(func.coalesce(func.sum(Book.pages), 0)).where(Book.status == Status.READ.value)
        )
        or 0
    )

    by_status = {status.value: 0 for status in Status}
    by_status.update(_counts(session, Book.status, Book))

    ratings = session.scalars(select(Book.rating).where(Book.rating.is_not(None)))
    rating_distribution = {f"{step / 2:g}": 0 for step in range(1, 11)}
    for rating in ratings:
        rating_distribution[f"{rating:g}"] = rating_distribution.get(f"{rating:g}", 0) + 1

    finished = session.scalars(
        select(Book.finished_on).where(
            Book.status == Status.READ.value, Book.finished_on.is_not(None)
        )
    )
    per_year = Counter(day.year for day in finished)

    copies_by_format = {fmt.value: 0 for fmt in Format}
    copies_by_format.update(_counts(session, Copy.format, Copy))
    lent_out = session.scalar(select(func.count(Copy.id)).where(Copy.lent_to.is_not(None))) or 0

    top_authors = [
        NameCount(name=item.name, count=item.count)
        for item in sorted(list_authors(session), key=lambda item: (-item.count, item.name))[:10]
    ]
    top_tags = sorted(list_tags(session), key=lambda item: (-item.count, item.name))[:10]

    return Stats(
        total_books=total,
        owned_books=owned,
        total_copies=copies,
        favorites=favorites,
        rated=rated,
        average_rating=round(average, 2) if average is not None else None,
        pages_read=pages_read,
        by_status=by_status,
        rating_distribution=rating_distribution,
        read_per_year=[YearCount(year=y, count=c) for y, c in sorted(per_year.items())],
        copies_by_owner=list_owners(session),
        copies_by_format=copies_by_format,
        lent_out=lent_out,
        top_authors=top_authors,
        top_tags=top_tags,
    )


# ---------------------------------------------------------------- merging ---


def find_existing(
    session: Session, *, isbn: str | None, title: str, authors: Iterable[str] = ()
) -> Book | None:
    """Find the book that ``(isbn, title, authors)`` describes, if it is already stored.

    An ISBN match always wins. Otherwise the title must match (ignoring case) and,
    when both sides know their authors, at least one author must be shared. A
    stored book with a *different* ISBN is a different edition and never matches.
    """
    if isbn:
        book = session.scalar(select(Book).options(*book_load_options()).where(Book.isbn == isbn))
        if book is not None:
            return book
    wanted = {name.casefold() for name in authors}
    query = (
        select(Book)
        .options(*book_load_options())
        .where(func.lower(Book.title) == title.lower())
        .order_by(Book.id)
    )
    if isbn:
        query = query.where(Book.isbn.is_(None))
    for book in session.scalars(query):
        theirs = {name.casefold() for name in book.authors}
        if not wanted or not theirs or wanted & theirs:
            return book
    return None


def merge_book(
    session: Session,
    book: Book,
    data: BookCreate,
    *,
    default_owner: str,
    overwrite_personal: bool,
) -> bool:
    """Merge ``data`` into an existing ``book``; return whether anything changed.

    Catalog fields are only filled in when empty, tags are added and missing
    copies are created. Personal data (status, rating...) replaces the stored
    values when ``overwrite_personal`` is set (restoring a backup); otherwise it
    only fills gaps, so that re-running an import never undoes edits made in the app.
    """
    changed = False

    def assign(name: str, value) -> None:
        nonlocal changed
        value = _value(value)
        if getattr(book, name) != value:
            setattr(book, name, value)
            changed = True

    for name in CATALOG_FIELDS:
        value = getattr(data, name)
        if value is not None and getattr(book, name) is None:
            if name == "isbn":
                try:
                    _ensure_isbn_free(session, value, book.id)
                except ConflictError:
                    continue
            assign(name, value)

    if data.authors and not book.author_links:
        set_authors(session, book, data.authors)
        changed = True

    known_tags = {name.casefold() for name in book.tag_names}
    new_tags = [name for name in data.tags if name.casefold() not in known_tags]
    if new_tags:
        set_tags(session, book, [*book.tag_names, *new_tags])
        changed = True

    if overwrite_personal:
        for name in PERSONAL_FIELDS:
            assign(name, getattr(data, name))
    else:
        for name in ("rating", "started_on", "finished_on", "review"):
            if getattr(data, name) is not None and getattr(book, name) is None:
                assign(name, getattr(data, name))
        if book.status == Status.TO_READ.value and data.status != Status.TO_READ:
            assign("status", data.status)
        if data.favorite and not book.favorite:
            assign("favorite", True)

    def copy_key(owner: str, fmt: str, location: str | None) -> tuple[str, str, str]:
        return owner.casefold(), fmt, (location or "").casefold()

    have = Counter(copy_key(c.owner, c.format, c.location) for c in book.copies)
    for copy in data.copies:
        key = copy_key(copy.owner or default_owner, _value(copy.format), copy.location)
        if have[key] > 0:
            have[key] -= 1
        else:
            book.copies.append(_new_copy(copy, default_owner))
            changed = True

    if changed:
        book.updated_at = utcnow()
        session.flush()
    return changed


UpsertResult = Literal["created", "updated", "unchanged"]


def upsert_book(
    session: Session,
    data: BookCreate,
    *,
    default_owner: str,
    overwrite_personal: bool = False,
) -> tuple[Book, UpsertResult]:
    """Create the book, or merge it into the matching stored book."""
    existing = find_existing(session, isbn=data.isbn, title=data.title, authors=data.authors)
    if existing is None:
        return create_book(session, data, default_owner), "created"
    changed = merge_book(
        session,
        existing,
        data,
        default_owner=default_owner,
        overwrite_personal=overwrite_personal,
    )
    return existing, "updated" if changed else "unchanged"


# ---------------------------------------------------- authors & publishers ---


def _merge_details(target, data, fields: tuple[str, ...], overwrite: bool) -> bool:
    """Copy ``fields`` from ``data`` onto ``target``: only into empty ones unless ``overwrite``."""
    changed = False
    for name in fields:
        value = getattr(data, name)
        current = getattr(target, name)
        if value is not None and value != current and (overwrite or current is None):
            setattr(target, name, value)
            changed = True
    return changed


def merge_author_details(session: Session, data: AuthorDetails, *, overwrite: bool = False) -> bool:
    """Fill in an author's details; return whether anything changed.

    Authors only exist while a book references them, so details for an author
    with no books are ignored.
    """
    author = session.scalar(select(Author).where(func.lower(Author.name) == data.name.lower()))
    if author is None:
        return False
    return _merge_details(author, data, AUTHOR_FIELDS, overwrite)


def merge_publisher_details(
    session: Session, data: PublisherDetails, *, overwrite: bool = False
) -> bool:
    """Create the publisher or fill in its details; return whether anything changed."""
    publisher = session.scalar(
        select(Publisher).where(func.lower(Publisher.name) == data.name.lower())
    )
    if publisher is None:
        session.add(Publisher(name=data.name, **{f: getattr(data, f) for f in PUBLISHER_FIELDS}))
        session.flush()
        return True
    return _merge_details(publisher, data, PUBLISHER_FIELDS, overwrite)

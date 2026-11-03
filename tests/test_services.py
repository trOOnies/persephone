import pytest
from sqlalchemy import func, select

from persephone import services
from persephone.models import Author, Book, Tag
from persephone.schemas import BookCreate


def make(session, **fields):
    return services.create_book(session, BookCreate(**fields), "Me")


def test_find_existing_by_isbn(session):
    book = make(session, title="Dune", isbn="9780441172719")
    found = services.find_existing(session, isbn="9780441172719", title="Anything")
    assert found is book


def test_find_existing_by_title_and_author(session):
    book = make(session, title="Circe", authors=["Madeline Miller"])
    assert services.find_existing(session, isbn=None, title="CIRCE", authors=["madeline miller"])
    assert services.find_existing(session, isbn=None, title="Circe", authors=[]) is book
    assert services.find_existing(session, isbn=None, title="Circe", authors=["Someone"]) is None


def test_find_existing_ignores_other_editions(session):
    make(session, title="Dune", isbn="9780441172719")
    # Same title, different ISBN: a different edition.
    assert services.find_existing(session, isbn="9780340960196", title="Dune") is None
    # Same title without an ISBN on file: assume it is the same book.
    plain = make(session, title="Emma")
    assert services.find_existing(session, isbn="9780141439587", title="Emma") is plain


def test_merge_fills_isbn_unless_taken(session):
    make(session, title="Dune", isbn="9780441172719")
    other = make(session, title="Dune Messiah")
    changed = services.merge_book(
        session,
        other,
        BookCreate(title="Dune Messiah", isbn="9780441172719"),
        default_owner="Me",
        overwrite_personal=False,
    )
    assert changed is False
    assert other.isbn is None


def test_merge_copies_counts_duplicates(session):
    book = make(session, title="A", copies=[{"format": "ebook"}])
    data = BookCreate(title="A", copies=[{"format": "ebook"}, {"format": "ebook"}])
    assert services.merge_book(session, book, data, default_owner="Me", overwrite_personal=False)
    assert len(book.copies) == 2
    assert not services.merge_book(
        session, book, data, default_owner="Me", overwrite_personal=False
    )


def test_merge_without_overwrite_only_fills_gaps(session):
    book = make(session, title="A", rating=3, status="reading")
    data = BookCreate(title="A", rating=5, status="read", favorite=True, review="Nice")
    services.merge_book(session, book, data, default_owner="Me", overwrite_personal=False)
    assert book.rating == 3
    assert book.status == "reading"
    assert book.favorite is True
    assert book.review == "Nice"


def test_upsert(session):
    book, result = services.upsert_book(session, BookCreate(title="A"), default_owner="Me")
    assert result == "created"
    again, result = services.upsert_book(session, BookCreate(title="A"), default_owner="Me")
    assert (again, result) == (book, "unchanged")
    again, result = services.upsert_book(
        session, BookCreate(title="A", pages=10), default_owner="Me"
    )
    assert result == "updated"


def test_prune_orphans(session):
    book = make(session, title="A", authors=["X", "Y"], tags=["t1", "t2"])
    make(session, title="B", authors=["Y"], tags=["t2"])
    services.delete_book(session, book)
    session.flush()
    assert session.scalars(select(Author.name)).all() == ["Y"]
    assert session.scalars(select(Tag.name)).all() == ["t2"]
    assert session.scalar(select(func.count(Book.id))) == 1


def test_list_books_rejects_unknown_sort(session):
    with pytest.raises(ValueError):
        services.list_books(session, sort="price")

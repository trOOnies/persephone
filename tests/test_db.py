import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.schema import CreateTable

from persephone.config import sqlite_url
from persephone.db import Base, open_database, session_scope
from persephone.models import Book


def make_old_database(path):
    """A database from before books could have no status: ``status`` is NOT NULL."""
    engine = create_engine(sqlite_url(path))
    Base.metadata.create_all(engine)
    old_books = str(CreateTable(Base.metadata.tables["books"]).compile(engine))
    old_books = old_books.replace("status VARCHAR(20),", "status VARCHAR(20) NOT NULL,")
    assert "NOT NULL," in old_books
    engine.dispose()
    db = sqlite3.connect(path)
    db.executescript(
        f"""
        DROP TABLE books;
        {old_books};
        INSERT INTO books (id, title, status, favorite, created_at, updated_at)
            VALUES (1, 'Dune', 'read', 0, '2024-01-01', '2024-01-01');
        INSERT INTO copies (book_id, owner, format) VALUES (1, 'Me', 'paperback');
        """
    )
    db.commit()
    db.close()


def status_is_required(path) -> bool:
    db = sqlite3.connect(path)
    try:
        return next(r[3] for r in db.execute("PRAGMA table_info(books)") if r[1] == "status") == 1
    finally:
        db.close()


def test_old_databases_are_upgraded_keeping_their_rows(tmp_path):
    path = tmp_path / "old.db"
    make_old_database(path)
    assert status_is_required(path)

    sessions = open_database(sqlite_url(path))
    open_database(sqlite_url(path))  # a second start finds nothing to do
    assert not status_is_required(path)
    with session_scope(sessions) as session:
        dune = session.get(Book, 1)
        assert (dune.title, dune.status, len(dune.copies)) == ("Dune", "read", 1)
        session.add(Book(title="Unread"))
    with session_scope(sessions) as session:
        assert session.get(Book, 2).status is None

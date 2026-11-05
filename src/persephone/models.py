"""SQLAlchemy ORM models.

A :class:`Book` holds both the catalog information (title, authors, ISBN...)
and the reader's personal data (status, rating, favorite, review). Physical or
digital copies, each with an owner, live in :class:`Copy`.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from persephone.db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


book_tags = Table(
    "book_tags",
    Base.metadata,
    Column("book_id", ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    gender: Mapped[str | None] = mapped_column(String(50))
    origin: Mapped[str | None] = mapped_column(String(100))
    year_born: Mapped[int | None] = mapped_column(Integer)
    year_deceased: Mapped[int | None] = mapped_column(Integer)

    book_links: Mapped[list[BookAuthor]] = relationship(
        back_populates="author", cascade="all, delete-orphan"
    )


class BookAuthor(Base):
    """Ordered link between a book and its authors."""

    __tablename__ = "book_authors"

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("authors.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)

    book: Mapped[Book] = relationship(back_populates="author_links")
    author: Mapped[Author] = relationship(back_populates="book_links", lazy="joined")


class Publisher(Base):
    """Details about a publisher. Books name theirs in ``Book.publisher``, matched by name."""

    __tablename__ = "publishers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    origin: Mapped[str | None] = mapped_column(String(100))
    year_created: Mapped[int | None] = mapped_column(Integer)
    year_closed: Mapped[int | None] = mapped_column(Integer)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    books: Mapped[list[Book]] = relationship(secondary=book_tags, back_populates="tags")


class Book(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Catalog information
    title: Mapped[str] = mapped_column(String(500), index=True)
    subtitle: Mapped[str | None] = mapped_column(String(500))
    isbn: Mapped[str | None] = mapped_column(String(13), unique=True, index=True)
    publisher: Mapped[str | None] = mapped_column(String(255))
    published_year: Mapped[int | None] = mapped_column(Integer)
    pages: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    cover_url: Mapped[str | None] = mapped_column(String(1000))
    series: Mapped[str | None] = mapped_column(String(255))
    series_index: Mapped[float | None] = mapped_column(Float)
    original_title: Mapped[str | None] = mapped_column(String(500))
    original_language: Mapped[str | None] = mapped_column(String(50))
    original_year: Mapped[int | None] = mapped_column(Integer)
    translator: Mapped[str | None] = mapped_column(String(255))

    # Personal data
    status: Mapped[str] = mapped_column(String(20), default="to_read", index=True)
    rating: Mapped[float | None] = mapped_column(Float)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    started_on: Mapped[dt.date | None] = mapped_column(Date)
    finished_on: Mapped[dt.date | None] = mapped_column(Date)
    review: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    author_links: Mapped[list[BookAuthor]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        order_by="BookAuthor.position",
    )
    tags: Mapped[list[Tag]] = relationship(
        secondary=book_tags, back_populates="books", order_by="Tag.name"
    )
    copies: Mapped[list[Copy]] = relationship(
        back_populates="book", cascade="all, delete-orphan", order_by="Copy.id"
    )

    @property
    def authors(self) -> list[str]:
        return [link.author.name for link in self.author_links]

    @property
    def tag_names(self) -> list[str]:
        return [tag.name for tag in self.tags]

    @property
    def owned(self) -> bool:
        return bool(self.copies)


class Copy(Base):
    """One copy of a book that somebody owns."""

    __tablename__ = "copies"

    id: Mapped[int] = mapped_column(primary_key=True)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    owner: Mapped[str] = mapped_column(String(100), index=True)
    format: Mapped[str] = mapped_column(String(20), default="paperback")
    location: Mapped[str | None] = mapped_column(String(255))
    acquired_on: Mapped[dt.date | None] = mapped_column(Date)
    lent_to: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)

    book: Mapped[Book] = relationship(back_populates="copies")

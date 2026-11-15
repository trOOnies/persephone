"""Pydantic schemas: request validation and response shapes."""

from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from persephone import isbn as isbn_utils


class Ownership(str, Enum):
    """Whether you have the book. ``None`` (the default) is neither."""

    WANTED = "wanted"  # would like to get it
    OWNED = "owned"  # at least one copy


class Status(str, Enum):
    """Reading status. Only owned books have one."""

    TO_READ = "to_read"
    READING = "reading"
    READ = "read"
    ABANDONED = "abandoned"


class Format(str, Enum):
    HARDCOVER = "hardcover"
    PAPERBACK = "paperback"
    EBOOK = "ebook"
    AUDIOBOOK = "audiobook"
    OTHER = "other"


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


OptStr = Annotated[str | None, BeforeValidator(_blank_to_none)]


def _clean_names(values: list[str]) -> list[str]:
    """Strip names, drop blanks and case-insensitive duplicates, keep order."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        name = " ".join(str(raw).split())
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            result.append(name)
    return result


def _validate_rating(value: float | None) -> float | None:
    if value is None:
        return None
    if not 0.5 <= value <= 5 or (value * 2) != int(value * 2):
        raise ValueError("rating must be between 0.5 and 5, in steps of 0.5")
    return float(value)


def _validate_isbn(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = isbn_utils.normalize(value)
    if normalized is None:
        raise ValueError("not a valid ISBN-10 or ISBN-13")
    return normalized


# ------------------------------------------------------------------ copies ---


class CopyBase(BaseModel):
    format: Format = Format.PAPERBACK
    location: OptStr = Field(default=None, max_length=255)
    acquired_on: dt.date | None = None
    lent_to: OptStr = Field(default=None, max_length=100)
    notes: OptStr = None


class CopyCreate(CopyBase):
    owner: OptStr = Field(default=None, max_length=100, description="Defaults to the app owner")


class CopyUpdate(BaseModel):
    owner: OptStr = Field(default=None, max_length=100)
    format: Format | None = None
    location: OptStr = Field(default=None, max_length=255)
    acquired_on: dt.date | None = None
    lent_to: OptStr = Field(default=None, max_length=100)
    notes: OptStr = None

    @field_validator("owner", "format")
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("cannot be empty")
        return value


class CopyOut(CopyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    book_id: int
    owner: str


# ------------------------------------------------------------------- books ---


class _BookFields(BaseModel):
    """Fields shared by creation and partial updates (all optional here)."""

    subtitle: OptStr = Field(default=None, max_length=500)
    isbn: OptStr = None
    publisher: OptStr = Field(default=None, max_length=255)
    published_year: int | None = Field(default=None, ge=-3000, le=3000)
    pages: int | None = Field(default=None, ge=1, le=100_000)
    language: OptStr = Field(default=None, max_length=50)
    description: OptStr = None
    cover_url: OptStr = Field(default=None, max_length=1000)
    series: OptStr = Field(default=None, max_length=255)
    series_index: float | None = Field(default=None, ge=0)
    original_title: OptStr = Field(default=None, max_length=500)
    original_language: OptStr = Field(default=None, max_length=50)
    original_year: int | None = Field(default=None, ge=-3000, le=3000)
    translator: OptStr = Field(default=None, max_length=255)
    rating: float | None = None
    started_on: dt.date | None = None
    finished_on: dt.date | None = None
    review: OptStr = None

    @field_validator("rating")
    @classmethod
    def _check_rating(cls, value: float | None) -> float | None:
        return _validate_rating(value)

    @field_validator("isbn")
    @classmethod
    def _check_isbn(cls, value: str | None) -> str | None:
        return _validate_isbn(value)

    @field_validator("cover_url")
    @classmethod
    def _check_cover_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://", "/")):
            raise ValueError("cover_url must be an http(s) URL")
        return value

    @model_validator(mode="after")
    def _check_dates(self):
        if self.started_on and self.finished_on and self.finished_on < self.started_on:
            raise ValueError("finished_on cannot be before started_on")
        return self


class BookCreate(_BookFields):
    title: str = Field(min_length=1, max_length=500)
    authors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: Status | None = None
    favorite: bool = False
    wanted: bool = Field(default=False, description="Ignored when the book has copies")
    copies: list[CopyCreate] = Field(default_factory=list)

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("authors", "tags")
    @classmethod
    def _names(cls, value: list[str]) -> list[str]:
        return _clean_names(value)


class BookUpdate(_BookFields):
    """Partial update: only the fields that are sent are changed."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    authors: list[str] | None = None
    tags: list[str] | None = None
    status: Status | None = None
    favorite: bool | None = None
    ownership: Ownership | None = Field(
        default=None,
        description=(
            "owned adds a copy (with the default owner) if there is none; wanted or null "
            "removes every copy and clears the reading status"
        ),
    )

    @field_validator("title", mode="before")
    @classmethod
    def _strip_title(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("title", "favorite", "authors", "tags")
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("cannot be null")
        return value

    @field_validator("authors", "tags")
    @classmethod
    def _names(cls, value: list[str]) -> list[str]:
        return _clean_names(value)


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    subtitle: str | None
    authors: list[str]
    tags: list[str] = Field(validation_alias="tag_names")
    isbn: str | None
    publisher: str | None
    published_year: int | None
    pages: int | None
    language: str | None
    description: str | None
    cover_url: str | None
    local_cover: str | None = Field(
        default=None, description="The downloaded copy of the cover, once it has been saved"
    )
    series: str | None
    series_index: float | None
    original_title: str | None
    original_language: str | None
    original_year: int | None
    translator: str | None
    status: Status | None
    rating: float | None
    favorite: bool
    started_on: dt.date | None
    finished_on: dt.date | None
    review: str | None
    ownership: Ownership | None
    owned: bool
    wanted: bool
    copies: list[CopyOut]
    created_at: dt.datetime
    updated_at: dt.datetime


class BookPage(BaseModel):
    items: list[BookOut]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------------ others ---


class NameCount(BaseModel):
    name: str
    count: int


Year = Annotated[int | None, Field(default=None, ge=-3000, le=3000)]


def _check_years(first: int | None, last: int | None, what: str) -> None:
    if first is not None and last is not None and last < first:
        raise ValueError(what)


class AuthorDetails(BaseModel):
    """What Persephone knows about an author, besides their books."""

    name: str = Field(min_length=1, max_length=255)
    gender: OptStr = Field(default=None, max_length=50)
    origin: OptStr = Field(default=None, max_length=100, description="Country or region")
    year_born: Year = None
    year_deceased: Year = None

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _check_dates(self):
        _check_years(self.year_born, self.year_deceased, "year_deceased cannot be before year_born")
        return self


class PublisherDetails(BaseModel):
    """What Persephone knows about a publisher."""

    name: str = Field(min_length=1, max_length=255)
    origin: OptStr = Field(default=None, max_length=100, description="Country or region")
    year_created: Year = None
    year_closed: Year = None

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: Any) -> Any:
        return " ".join(value.split()) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _check_dates(self):
        _check_years(
            self.year_created, self.year_closed, "year_closed cannot be before year_created"
        )
        return self


class AuthorOut(AuthorDetails):
    count: int


class PublisherOut(PublisherDetails):
    count: int


class YearCount(BaseModel):
    year: int
    count: int


class Stats(BaseModel):
    total_books: int
    owned_books: int
    total_copies: int
    favorites: int
    wanted_books: int
    rated: int
    average_rating: float | None
    pages_read: int
    by_status: dict[str, int]
    rating_distribution: dict[str, int]
    read_per_year: list[YearCount]
    copies_by_owner: list[NameCount]
    copies_by_format: dict[str, int]
    lent_out: int
    top_authors: list[NameCount]
    top_tags: list[NameCount]


class CoverStatus(BaseModel):
    folder: str = Field(description="Where the downloaded covers are kept")
    saved: int = Field(description="Books whose cover has a local copy")
    pending: int = Field(description="Books with a cover that still has to be downloaded")
    unavailable: int = Field(description="Books whose cover link or ISBN had no image online")


class AppConfig(BaseModel):
    version: str
    default_owner: str
    openlibrary: bool
    currency: str


# ------------------------------------------------------------------ budget ---

Money = Annotated[float, Field(ge=0, le=1_000_000)]


class CartStatus(str, Enum):
    OPEN = "open"
    SAVED = "saved"
    BOUGHT = "bought"


class CartItemCreate(BaseModel):
    book_id: int
    price: Money
    discount: Money = 0

    @model_validator(mode="after")
    def _check_discount(self):
        if self.discount > self.price:
            raise ValueError("the discount cannot be more than the price")
        return self


class CartItemUpdate(BaseModel):
    price: Money | None = None
    discount: Money | None = None


class CartUpdate(BaseModel):
    shipping: Money


class CartItemOut(BaseModel):
    id: int
    book_id: int
    title: str
    authors: list[str]
    owned: bool = Field(description="Whether the book has copies right now")
    price: float
    discount: float
    total: float


class CartOut(BaseModel):
    id: int
    status: CartStatus
    items: list[CartItemOut]
    subtotal: float = Field(description="Sum of the prices")
    discount: float = Field(description="Sum of the discounts")
    shipping: float
    total: float = Field(description="subtotal - discount + shipping")
    created_at: dt.datetime
    updated_at: dt.datetime
    saved_at: dt.datetime | None
    bought_on: dt.date | None


class ImportSummary(BaseModel):
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = Field(default_factory=list)

    def merge(self, other: ImportSummary) -> ImportSummary:
        return ImportSummary(
            created=self.created + other.created,
            updated=self.updated + other.updated,
            unchanged=self.unchanged + other.unchanged,
            errors=[*self.errors, *other.errors],
        )

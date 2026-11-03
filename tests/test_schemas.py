import datetime as dt

import pytest
from pydantic import ValidationError

from persephone.schemas import BookCreate, BookUpdate, CopyUpdate, ImportSummary, Status


def test_book_create_cleans_input():
    book = BookCreate(
        title="  The   Odyssey ",
        subtitle="   ",
        authors=["Homer", " homer ", "", "Emily  Wilson"],
        tags=["Epic", "epic", "Classics"],
        isbn="0-14-026886-3",
    )
    assert book.title == "The Odyssey"
    assert book.subtitle is None
    assert book.authors == ["Homer", "Emily Wilson"]
    assert book.tags == ["Epic", "Classics"]
    assert book.isbn == "9780140268867"
    assert book.status is Status.TO_READ
    assert book.favorite is False


@pytest.mark.parametrize("rating", [0.5, 1, 3.5, 5])
def test_valid_ratings(rating):
    assert BookCreate(title="x", rating=rating).rating == rating


@pytest.mark.parametrize("rating", [0, 0.3, 4.75, 5.5, -1])
def test_invalid_ratings(rating):
    with pytest.raises(ValidationError, match="rating"):
        BookCreate(title="x", rating=rating)


def test_invalid_isbn():
    with pytest.raises(ValidationError, match="ISBN"):
        BookCreate(title="x", isbn="12345")


def test_empty_title_rejected():
    with pytest.raises(ValidationError):
        BookCreate(title="   ")


def test_dates_must_be_in_order():
    with pytest.raises(ValidationError, match="finished_on"):
        BookCreate(title="x", started_on=dt.date(2024, 5, 2), finished_on=dt.date(2024, 5, 1))


def test_cover_url_must_be_http():
    assert BookCreate(title="x", cover_url="https://example.org/c.jpg").cover_url
    with pytest.raises(ValidationError, match="cover_url"):
        BookCreate(title="x", cover_url="javascript:alert(1)")


def test_update_only_tracks_sent_fields():
    update = BookUpdate(rating=4)
    assert update.model_dump(exclude_unset=True) == {"rating": 4}


@pytest.mark.parametrize("field", ["title", "status", "favorite", "authors", "tags"])
def test_update_rejects_null_for_required_fields(field):
    with pytest.raises(ValidationError):
        BookUpdate(**{field: None})


def test_update_allows_clearing_optional_fields():
    assert BookUpdate(rating=None, isbn=None).model_dump(exclude_unset=True) == {
        "rating": None,
        "isbn": None,
    }


def test_copy_update_rejects_null_owner():
    with pytest.raises(ValidationError):
        CopyUpdate(owner=None)
    with pytest.raises(ValidationError):
        CopyUpdate(owner="  ")


def test_import_summary_merge():
    total = ImportSummary(created=1, errors=["a"]).merge(ImportSummary(updated=2, errors=["b"]))
    assert total == ImportSummary(created=1, updated=2, unchanged=0, errors=["a", "b"])

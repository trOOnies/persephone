import datetime as dt
import json

import pytest
from sqlalchemy import select

from persephone import userdata
from persephone.db import open_database, session_scope
from persephone.models import Book


def test_export_format(client, add_book, session):
    add_book(
        "Circe",
        authors=["Madeline Miller"],
        isbn="9780316556347",
        tags=["mythology"],
        status="read",
        rating=4.5,
        favorite=True,
        finished_on="2025-02-20",
        review="Loved it",
        copies=[{"format": "hardcover", "location": "Study"}, {"owner": "Demeter"}],
    )
    payload = userdata.export_data(session)
    assert payload["format"] == userdata.FORMAT_NAME
    assert payload["version"] == userdata.FORMAT_VERSION
    assert payload["owner"] is None
    json.dumps(payload)  # serializable as is

    (book,) = payload["books"]
    assert book["title"] == "Circe"
    assert book["finished_on"] == "2025-02-20"
    assert book["favorite"] is True
    assert "id" not in book
    assert book["copies"] == [
        {
            "owner": "Me",
            "format": "hardcover",
            "location": "Study",
            "acquired_on": None,
            "lent_to": None,
            "notes": None,
        },
        {
            "owner": "Demeter",
            "format": "paperback",
            "location": None,
            "acquired_on": None,
            "lent_to": None,
            "notes": None,
        },
    ]


def test_export_for_one_owner(add_book, session):
    add_book("A", copies=[{"owner": "Demeter"}, {"owner": "Me"}])
    add_book("B")
    payload = userdata.export_data(session, owner=" demeter ")
    assert payload["owner"] == "demeter"
    assert [len(b["copies"]) for b in payload["books"]] == [1, 0]


def test_round_trip_into_empty_library(add_book, session, tmp_path):
    add_book("A", authors=["X"], rating=3, tags=["t"], copies=[{"lent_to": "Hecate"}])
    add_book("B", status="reading", started_on="2026-01-01", copies=[{}])
    add_book("C", wanted=True)
    payload = userdata.export_data(session)
    original_added = {b["title"]: b["added_at"] for b in payload["books"]}

    other = tmp_path / "other.db"
    with session_scope(open_database(f"sqlite:///{other}")) as fresh:
        summary = userdata.import_data(fresh, payload, default_owner="Me")
        assert (summary.created, summary.updated, summary.errors) == (3, 0, [])
        assert userdata.export_data(fresh)["books"] == payload["books"]
        books = {b.title: b for b in fresh.scalars(select(Book)).unique()}
        assert books["A"].created_at.isoformat() == original_added["A"]


def test_import_overwrites_personal_data_but_keeps_catalog(add_book, session):
    add_book("Circe", authors=["Madeline Miller"], publisher="Little, Brown", rating=4)
    payload = {
        "format": userdata.FORMAT_NAME,
        "version": 1,
        "books": [
            {
                "title": "circe",
                "authors": ["Madeline Miller"],
                "publisher": "Other publisher",
                "pages": 393,
                "rating": 5,
                "favorite": True,
                "tags": ["myth"],
                "copies": [{"owner": "Me", "format": "paperback"}],
            }
        ],
    }
    summary = userdata.import_data(session, payload, default_owner="Me")
    assert summary.updated == 1
    book = session.scalars(select(Book)).one()
    assert book.title == "Circe"
    assert book.publisher == "Little, Brown"  # existing catalog data wins
    assert book.pages == 393  # but gaps are filled
    assert book.rating == 5  # personal data comes from the file
    assert book.favorite is True
    assert book.tag_names == ["myth"]
    assert len(book.copies) == 1

    # Importing the same thing again changes nothing.
    assert userdata.import_data(session, payload, default_owner="Me").unchanged == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"format": "something-else"},
        {"format": userdata.FORMAT_NAME, "version": 99, "books": []},
        {"format": userdata.FORMAT_NAME, "version": 1, "books": "nope"},
        "a string",
    ],
)
def test_import_rejects_bad_payloads(session, payload):
    with pytest.raises(userdata.FormatError):
        userdata.import_data(session, payload, default_owner="Me")


def test_import_reports_invalid_records(session):
    summary = userdata.import_data(
        session,
        [{"title": "Fine"}, {"title": "Bad rating", "rating": 9}, 42],
        default_owner="Me",
    )
    assert summary.created == 1
    assert len(summary.errors) == 2
    assert "book #2 (Bad rating): rating" in summary.errors[0]
    assert summary.errors[1].startswith("book #3: ")


def test_default_filename():
    day = dt.date(2026, 11, 3)
    assert userdata.default_filename(None, day) == "persephone-export-20261103.json"
    assert userdata.default_filename("Kore Demeter!", day) == (
        "persephone-export-kore-demeter-20261103.json"
    )


def test_cli_export_and_import(tmp_path, add_book, db_path, capsys, monkeypatch):
    add_book("A", copies=[{"owner": "Demeter"}])
    out = tmp_path / "backup.json"
    assert userdata.main(["--db", str(db_path), "export", "--output", str(out)]) == 0
    assert "Exported 1 books" in capsys.readouterr().out
    assert json.loads(out.read_text())["books"][0]["title"] == "A"

    other = tmp_path / "other.db"
    assert userdata.main(["--db", str(other), "import", str(out)]) == 0
    assert "1 created" in capsys.readouterr().out

    # Default output goes to local/, named after the owner.
    monkeypatch.chdir(tmp_path)
    assert userdata.main(["--db", str(db_path), "export", "--owner", "Demeter"]) == 0
    assert list((tmp_path / "local").glob("persephone-export-demeter-*.json"))


def test_cli_import_errors(tmp_path, capsys):
    db = str(tmp_path / "x.db")
    assert userdata.main(["--db", db, "import", str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert userdata.main(["--db", db, "import", str(bad)]) == 1
    assert "not a Persephone export" in capsys.readouterr().err


def test_author_and_publisher_details_round_trip(add_book, session, tmp_path):
    from persephone import services
    from persephone.schemas import AuthorDetails, PublisherDetails

    add_book("Ficciones", authors=["Jorge Luis Borges"], publisher="Sur")
    add_book(
        "Metamorphoses",
        authors=["Ovid"],
        original_title="Metamorphoseon libri",
        original_language="Latin",
        original_year=8,
        translator="David Raeburn",
    )
    session.expire_all()
    assert services.merge_author_details(
        session,
        AuthorDetails(
            name="jorge luis borges",
            gender="male",
            origin="Argentina",
            year_born=1899,
            year_deceased=1986,
        ),
    )
    assert not services.merge_author_details(session, AuthorDetails(name="Nobody", gender="x"))
    assert services.merge_publisher_details(
        session,
        PublisherDetails(name="Sur", origin="Argentina", year_created=1931, year_closed=1992),
    )
    session.commit()

    payload = userdata.export_data(session)
    assert payload["authors"] == [
        {
            "name": "Jorge Luis Borges",
            "gender": "male",
            "origin": "Argentina",
            "year_born": 1899,
            "year_deceased": 1986,
        },
    ]  # Ovid has no details, so is left out
    assert payload["publishers"] == [
        {"name": "Sur", "origin": "Argentina", "year_created": 1931, "year_closed": 1992},
    ]
    ovid = next(book for book in payload["books"] if book["title"] == "Metamorphoses")
    assert (ovid["original_title"], ovid["original_year"], ovid["translator"]) == (
        "Metamorphoseon libri",
        8,
        "David Raeburn",
    )

    other = open_database(f"sqlite:///{tmp_path / 'other.db'}")
    with session_scope(other) as fresh:
        payload["authors"].append({"name": "Ovid", "year_born": 1, "year_deceased": -5})
        summary = userdata.import_data(fresh, payload, default_owner="Me")
        assert summary.created == 2
        assert summary.errors == [
            "author #2: record: Value error, year_deceased cannot be before year_born"
        ]
        assert userdata.export_data(fresh)["authors"] == payload["authors"][:1]
        assert userdata.export_data(fresh)["publishers"] == payload["publishers"]


def test_details_fill_gaps_unless_overwriting(add_book, session):
    from persephone import services
    from persephone.schemas import AuthorDetails, PublisherDetails

    add_book("A", authors=["Ovid"])
    session.expire_all()
    services.merge_author_details(session, AuthorDetails(name="Ovid", origin="Rome"))
    assert not services.merge_author_details(session, AuthorDetails(name="Ovid", origin="Sulmo"))
    assert services.merge_author_details(
        session, AuthorDetails(name="Ovid", origin="Sulmo", year_born=-43), overwrite=True
    )
    services.merge_publisher_details(session, PublisherDetails(name="Penguin"))
    assert services.merge_publisher_details(
        session, PublisherDetails(name="penguin", year_created=1935)
    )
    (ovid,) = services.list_authors(session)
    assert (ovid.origin, ovid.year_born) == ("Sulmo", -43)
    (penguin,) = services.list_publishers(session)
    assert (penguin.name, penguin.year_created, penguin.count) == ("Penguin", 1935, 0)

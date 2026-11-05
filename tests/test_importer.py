import csv
import datetime as dt
import json

import pytest
from sqlalchemy import select

from persephone import importer, userdata
from persephone.models import Book

GOODREADS_HEADER = [
    "Book Id", "Title", "Author", "Author l-f", "Additional Authors", "ISBN", "ISBN13",
    "My Rating", "Average Rating", "Publisher", "Binding", "Number of Pages", "Year Published",
    "Original Publication Year", "Date Read", "Date Added", "Bookshelves",
    "Bookshelves with positions", "Exclusive Shelf", "My Review", "Spoiler", "Private Notes",
    "Read Count", "Owned Copies",
]  # fmt: skip


def goodreads_row(**values):
    row = dict.fromkeys(GOODREADS_HEADER, "")
    row.update(values)
    return row


def write_csv(path, header, rows, delimiter=","):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def info(tmp_path):
    folder = tmp_path / "info"
    folder.mkdir()
    return folder


def by_title(session):
    return {book.title: book for book in session.scalars(select(Book)).unique()}


# ----------------------------------------------------------------- parsing --


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Dune (Dune Chronicles, #1)", ("Dune", "Dune Chronicles", 1.0)),
        ("The Hobbit (Middle-earth #0.5)", ("The Hobbit", "Middle-earth", 0.5)),
        ("Frankenstein (Penguin Classics)", ("Frankenstein (Penguin Classics)", None, None)),
        ("  Plain  ", ("Plain", None, None)),
    ],
)
def test_split_series(title, expected):
    assert importer.split_series(title) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0", None), ("", None), ("4", 4.0), ("3.7", 3.5), ("4,5", 4.5), ("8/10", 4.0), ("10", 5.0)],
)
def test_rating_parsing(value, expected):
    assert importer._rating(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024/05/01", dt.date(2024, 5, 1)),
        ("2024-05-01", dt.date(2024, 5, 1)),
        ("2024-05-01T10:00:00", dt.date(2024, 5, 1)),
        ("2024/05/01 10:00", dt.date(2024, 5, 1)),
        ("01/05/2024", None),
        ("", None),
        (None, None),
    ],
)
def test_date_parsing(value, expected):
    assert importer._date(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Hardcover", "hardcover"),
        ("Mass Market Paperback", "paperback"),
        ("Kindle Edition", "ebook"),
        ("Audible Audio", "audiobook"),
        ("", "paperback"),
        ("Leather Bound", "other"),
    ],
)
def test_format_parsing(value, expected):
    assert importer._format(value).value == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("yes", True), ("Y", True), ("1", True), ("2", True), ("no", False), ("", False), ("?", None)],
)
def test_bool_parsing(value, expected):
    assert importer._bool(value) is expected


# --------------------------------------------------------------- goodreads --


def test_goodreads_import(session, info):
    write_csv(
        info / "goodreads_library_export.csv",
        GOODREADS_HEADER,
        [
            goodreads_row(
                Title="Dune (Dune Chronicles, #1)",
                Author="Frank Herbert",
                ISBN='="0441172717"',
                ISBN13='="9780441172719"',
                **{
                    "My Rating": "5",
                    "Publisher": "Ace",
                    "Binding": "Mass Market Paperback",
                    "Number of Pages": "535",
                    "Year Published": "1990",
                    "Original Publication Year": "1965",
                    "Date Read": "2025/08/09",
                    "Date Added": "2025/07/01",
                    "Bookshelves": "favorites, science-fiction",
                    "Exclusive Shelf": "read",
                    "My Review": "Fear is the mind-killer.<br/><br/>Spice!",
                    "Owned Copies": "2",
                },
            ),
            goodreads_row(
                Title="Good Omens",
                Author="Terry Pratchett",
                **{
                    "Additional Authors": "Neil Gaiman",
                    "My Rating": "0",
                    "Exclusive Shelf": "to-read",
                    "Bookshelves": "to-read",
                    "Owned Copies": "0",
                },
            ),
            goodreads_row(
                Title="Ulysses",
                Author="James Joyce",
                **{"Exclusive Shelf": "did-not-finish", "Bookshelves": "did-not-finish"},
            ),
        ],
    )
    summary = importer.import_file(
        session, info / "goodreads_library_export.csv", default_owner="Kore"
    )
    assert summary.created == 3
    assert summary.errors == []

    books = by_title(session)
    dune = books["Dune"]
    assert dune.series == "Dune Chronicles"
    assert dune.series_index == 1
    assert dune.isbn == "9780441172719"
    assert dune.rating == 5
    assert dune.status == "read"
    assert dune.favorite is True
    assert dune.tag_names == ["science-fiction"]
    assert dune.finished_on == dt.date(2025, 8, 9)
    assert dune.created_at.date() == dt.date(2025, 7, 1)
    assert dune.published_year == 1990
    assert dune.pages == 535
    assert dune.review == "Fear is the mind-killer.\n\nSpice!"
    assert [(c.owner, c.format) for c in dune.copies] == [("Kore", "paperback")] * 2

    omens = books["Good Omens"]
    assert omens.authors == ["Terry Pratchett", "Neil Gaiman"]
    assert omens.rating is None
    assert omens.status == "to_read"
    assert omens.copies == []
    assert omens.tag_names == []

    assert books["Ulysses"].status == "abandoned"


def test_goodreads_reimport_keeps_app_edits(session, info):
    path = write_csv(
        info / "goodreads.csv",
        GOODREADS_HEADER,
        [
            goodreads_row(
                Title="Circe",
                Author="Madeline Miller",
                **{"My Rating": "4", "Exclusive Shelf": "read", "Owned Copies": "1"},
            )
        ],
    )
    assert importer.import_file(session, path, default_owner="Me").created == 1
    circe = by_title(session)["Circe"]
    circe.rating = 5  # edited in the app afterwards
    session.flush()

    again = importer.import_file(session, path, default_owner="Me")
    assert (again.created, again.updated, again.unchanged) == (0, 0, 1)
    circe = by_title(session)["Circe"]
    assert circe.rating == 5
    assert len(circe.copies) == 1


# ----------------------------------------------------------------- generic --


def test_generic_csv_import(session, info):
    header = ["Título", "Autor", "ISBN", "Year", "Genres", "Rating", "Status", "Favorite",
              "Owner", "Format", "Location", "Date finished"]  # fmt: skip
    write_csv(
        info / "mis-libros.csv",
        header,
        [
            {
                "Título": "Rayuela",
                "Autor": "Julio Cortázar",
                "ISBN": "",
                "Year": "1963",
                "Genres": "novela; argentina",
                "Rating": "4.5",
                "Status": "leído",
                "Favorite": "sí",
                "Owner": "Demeter",
                "Format": "tapa dura",
                "Location": "Biblioteca",
                "Date finished": "2023-12-01",
            },
            {"Título": "Ficciones", "Autor": "Jorge Luis Borges", "Date finished": "2022-01-02"},
            {"Título": "", "Autor": "Nobody"},
        ],
        delimiter=";",
    )
    summary = importer.import_file(session, info / "mis-libros.csv", default_owner="Me")
    assert summary.created == 2
    assert len(summary.errors) == 1
    assert "mis-libros.csv row 4" in summary.errors[0]

    books = by_title(session)
    rayuela = books["Rayuela"]
    assert rayuela.authors == ["Julio Cortázar"]
    assert rayuela.published_year == 1963
    assert rayuela.tag_names == ["argentina", "novela"]
    assert rayuela.rating == 4.5
    assert rayuela.status == "read"
    assert rayuela.favorite is True
    assert [(c.owner, c.format, c.location) for c in rayuela.copies] == [
        ("Demeter", "hardcover", "Biblioteca")
    ]
    # A finished date without a status means the book was read.
    assert books["Ficciones"].status == "read"
    assert books["Ficciones"].copies == []


def test_generic_owned_column(session, info):
    write_csv(
        info / "owned.csv",
        ["title", "authors", "owned"],
        [
            {"title": "Two copies", "authors": "A", "owned": "2"},
            {"title": "One copy", "authors": "B", "owned": "yes"},
            {"title": "No copy", "authors": "C", "owned": "no"},
        ],
    )
    importer.import_file(session, info / "owned.csv", default_owner="Me")
    books = by_title(session)
    assert len(books["Two copies"].copies) == 2
    assert len(books["One copy"].copies) == 1
    assert books["No copy"].copies == []


def test_json_import(session, info):
    (info / "books.json").write_text(
        json.dumps(
            [
                {
                    "title": "Metamorphoses",
                    "authors": ["Ovid"],
                    "tags": ["myth"],
                    "rating": 5,
                    "copies": [{"owner": "Demeter", "format": "hardcover"}],
                },
                {"title": "Hymns", "author": "Homer; Anonymous"},
                "not a book",
            ]
        )
    )
    summary = importer.import_file(session, info / "books.json", default_owner="Me")
    assert summary.created == 2
    assert len(summary.errors) == 1
    books = by_title(session)
    assert books["Metamorphoses"].copies[0].owner == "Demeter"
    assert books["Hymns"].authors == ["Homer", "Anonymous"]


def test_json_import_of_a_persephone_export(session, sessions, info, add_book, client):
    add_book("Circe", authors=["Madeline Miller"], rating=4.5)
    (info / "backup.json").write_text(json.dumps(client.get("/api/export").json()))
    # A different, empty database.
    session.execute(Book.__table__.delete())
    summary = importer.import_file(session, info / "backup.json", default_owner="Me")
    assert summary.created == 1
    assert by_title(session)["Circe"].rating == 4.5


def test_unsupported_file(session, info):
    path = info / "notes.txt"
    path.write_text("hello")
    with pytest.raises(userdata.FormatError):
        importer.import_file(session, path, default_owner="Me")


def test_import_folder_reports_each_file(session, info, capsys):
    write_csv(info / "a.csv", ["title"], [{"title": "A"}])
    (info / "broken.json").write_text("{nope")
    (info / "ignored.txt").write_text("skip me")
    summary = importer.import_folder(session, info, default_owner="Me")
    out = capsys.readouterr().out
    assert "a.csv: 1 created" in out
    assert "broken.json: 0 created" in out
    assert "ignored.txt" not in out
    assert summary.created == 1
    assert len(summary.errors) == 1


def test_import_empty_folder(session, info, capsys):
    importer.import_folder(session, info, default_owner="Me")
    assert "No .csv or .json files" in capsys.readouterr().out


def test_cli(tmp_path, info, capsys):
    write_csv(info / "a.csv", ["title", "owned"], [{"title": "A", "owned": "1"}])
    db = tmp_path / "cli.db"
    assert importer.main([str(info), "--db", str(db), "--owner", "Kore"]) == 0
    assert "Total: 1 created" in capsys.readouterr().out
    assert importer.main([str(info), "--db", str(db), "--owner", "Kore"]) == 0
    assert "Total: 0 created, 0 updated, 1 unchanged" in capsys.readouterr().out
    assert importer.main([str(tmp_path / "missing"), "--db", str(db)]) == 2


def test_original_fields_from_csv_and_goodreads(session, info):
    write_csv(
        info / "es.csv",
        ["Título", "Autor", "Título original", "Idioma original", "Año original", "Traductor"],
        [
            {
                "Título": "El nombre de la rosa",
                "Autor": "Umberto Eco",
                "Título original": "Il nome della rosa",
                "Idioma original": "italiano",
                "Año original": "1980",
                "Traductor": "Ricardo Pochtar",
            }
        ],
    )
    write_csv(
        info / "goodreads_library_export.csv",
        GOODREADS_HEADER,
        [
            goodreads_row(
                Title="Dune",
                Author="Frank Herbert",
                **{
                    "Exclusive Shelf": "read",
                    "Year Published": "1990",
                    "Original Publication Year": "1965",
                },
            )
        ],
    )
    importer.import_folder(session, info, default_owner="Me")
    books = by_title(session)
    rosa = books["El nombre de la rosa"]
    assert (rosa.original_title, rosa.original_language, rosa.original_year, rosa.translator) == (
        "Il nome della rosa",
        "italiano",
        1980,
        "Ricardo Pochtar",
    )
    assert (books["Dune"].published_year, books["Dune"].original_year) == (1990, 1965)

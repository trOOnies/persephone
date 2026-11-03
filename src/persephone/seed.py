"""Fill the library with a handful of demo books.

    python -m persephone.seed [--db PATH]

Running it twice does not duplicate anything: books already present are left alone.
"""

from __future__ import annotations

import argparse
import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from persephone import services
from persephone.cli import add_common_arguments, open_from_args
from persephone.db import session_scope
from persephone.schemas import BookCreate, ImportSummary

D = dt.date

DEMO_BOOKS: list[dict[str, Any]] = [
    {
        "title": "Metamorphoses",
        "authors": ["Ovid"],
        "published_year": 8,
        "language": "Latin",
        "pages": 723,
        "description": "Fifteen books of transformations, from the creation of the world "
        "to the deification of Julius Caesar, including the rape of Proserpina.",
        "tags": ["mythology", "poetry", "classics"],
        "status": "read",
        "rating": 5,
        "favorite": True,
        "started_on": D(2024, 1, 3),
        "finished_on": D(2024, 3, 12),
        "review": "Book V, with Ceres searching for her daughter, never gets old.",
        "copies": [{"format": "hardcover", "location": "Living room, top shelf"}],
    },
    {
        "title": "The Homeric Hymns",
        "authors": ["Homer"],
        "language": "English",
        "pages": 160,
        "description": "Thirty-three hymns to the gods; the Hymn to Demeter tells "
        "of Persephone's abduction and the founding of the Eleusinian mysteries.",
        "tags": ["mythology", "poetry", "classics"],
        "status": "read",
        "rating": 4.5,
        "favorite": True,
        "finished_on": D(2023, 10, 30),
        "copies": [{"format": "paperback", "location": "Living room, top shelf"}],
    },
    {
        "title": "The Odyssey",
        "authors": ["Homer"],
        "language": "English",
        "pages": 541,
        "tags": ["mythology", "epic", "classics"],
        "status": "reading",
        "started_on": D(2026, 10, 2),
        "copies": [
            {"format": "paperback", "location": "Bedside table"},
            {"format": "audiobook", "owner": "Demeter"},
        ],
    },
    {
        "title": "Mythology",
        "subtitle": "Timeless Tales of Gods and Heroes",
        "authors": ["Edith Hamilton"],
        "published_year": 1942,
        "pages": 497,
        "tags": ["mythology", "non-fiction"],
        "status": "read",
        "rating": 4,
        "finished_on": D(2022, 6, 18),
        "copies": [{"format": "paperback", "owner": "Demeter", "lent_to": "Hecate"}],
    },
    {
        "title": "Circe",
        "authors": ["Madeline Miller"],
        "isbn": "9780316556347",
        "publisher": "Little, Brown and Company",
        "published_year": 2018,
        "pages": 393,
        "tags": ["mythology", "fantasy", "retelling"],
        "status": "read",
        "rating": 4.5,
        "favorite": True,
        "started_on": D(2025, 2, 1),
        "finished_on": D(2025, 2, 20),
        "review": "Witchcraft, exile and herbs. Pomegranates would have fit right in.",
        "copies": [{"format": "hardcover", "location": "Study"}],
    },
    {
        "title": "The Song of Achilles",
        "authors": ["Madeline Miller"],
        "isbn": "9780062060624",
        "publisher": "Ecco",
        "published_year": 2012,
        "pages": 378,
        "tags": ["mythology", "retelling"],
        "status": "to_read",
    },
    {
        "title": "Dune",
        "authors": ["Frank Herbert"],
        "isbn": "9780441172719",
        "publisher": "Ace",
        "published_year": 1965,
        "pages": 658,
        "series": "Dune",
        "series_index": 1,
        "tags": ["science fiction"],
        "status": "read",
        "rating": 5,
        "finished_on": D(2025, 8, 9),
        "copies": [{"format": "paperback", "location": "Study"}, {"format": "ebook"}],
    },
    {
        "title": "Frankenstein",
        "subtitle": "or, The Modern Prometheus",
        "authors": ["Mary Shelley"],
        "published_year": 1818,
        "pages": 280,
        "tags": ["gothic", "classics", "science fiction"],
        "status": "read",
        "rating": 4,
        "finished_on": D(2024, 10, 31),
        "copies": [{"format": "ebook"}],
    },
    {
        "title": "Pride and Prejudice",
        "authors": ["Jane Austen"],
        "published_year": 1813,
        "pages": 432,
        "tags": ["classics", "romance"],
        "status": "abandoned",
        "rating": 2.5,
        "review": "Not the right season for it. Will try again in spring.",
    },
    {
        "title": "The Secret Garden",
        "authors": ["Frances Hodgson Burnett"],
        "published_year": 1911,
        "pages": 331,
        "tags": ["classics", "gardens"],
        "status": "to_read",
        "copies": [{"format": "hardcover", "location": "Greenhouse bench"}],
    },
    {
        "title": "Braiding Sweetgrass",
        "authors": ["Robin Wall Kimmerer"],
        "published_year": 2013,
        "pages": 391,
        "tags": ["nature", "non-fiction", "gardens"],
        "status": "read",
        "rating": 5,
        "favorite": True,
        "finished_on": D(2026, 4, 22),
    },
    {
        "title": "The Name of the Rose",
        "authors": ["Umberto Eco"],
        "published_year": 1980,
        "pages": 536,
        "language": "English",
        "tags": ["mystery", "historical"],
        "status": "to_read",
        "copies": [{"format": "paperback", "owner": "Demeter", "location": "Hallway"}],
    },
]


def seed(session: Session, default_owner: str) -> ImportSummary:
    summary = ImportSummary()
    for record in DEMO_BOOKS:
        data = BookCreate.model_validate(record)
        found = services.find_existing(
            session, isbn=data.isbn, title=data.title, authors=data.authors
        )
        if found is None:
            services.create_book(session, data, default_owner)
            summary.created += 1
        else:
            summary.unchanged += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m persephone.seed", description="Add demo books to the library."
    )
    add_common_arguments(parser)
    args = parser.parse_args(argv)
    sessions, settings = open_from_args(args)
    with session_scope(sessions) as session:
        summary = seed(session, settings.default_owner)
    print(f"Seeded {summary.created} books ({summary.unchanged} were already there).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

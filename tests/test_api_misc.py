import pytest
from fastapi.testclient import TestClient

from persephone import __version__
from persephone.config import Settings
from persephone.openlibrary import OpenLibraryError
from persephone.web import create_app


def test_health_and_config(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/config").json() == {
        "version": __version__,
        "default_owner": "Me",
        "openlibrary": True,
        "currency": "$",
    }


def test_frontend_is_served(client):
    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert "Persephone" in index.text
    for asset in ("/static/app.js", "/static/app.css", "/static/favicon.svg", "/favicon.ico"):
        assert client.get(asset).status_code == 200, asset


def test_frontend_is_revalidated_on_every_load(client):
    for path in ("/", "/static/app.js", "/static/app.css"):
        assert client.get(path).headers["cache-control"] == "no-cache", path
    assert "cache-control" not in client.get("/api/health").headers


def test_openapi_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/books" in paths
    assert "/api/books/{book_id}/copies" in paths


def test_facets(client, add_book):
    add_book("A", authors=["Ovid", "Homer"], tags=["poetry", "myth"])
    add_book("B", authors=["Homer"], tags=["myth"])
    no_details = {"gender": None, "origin": None, "year_born": None, "year_deceased": None}
    assert client.get("/api/authors").json() == [
        {"name": "Homer", "count": 2, **no_details},
        {"name": "Ovid", "count": 1, **no_details},
    ]
    assert client.get("/api/tags").json() == [
        {"name": "myth", "count": 2},
        {"name": "poetry", "count": 1},
    ]


def test_authors_and_tags_are_shared_case_insensitively(client, add_book):
    add_book("A", authors=["Homer"], tags=["Myth"])
    book = add_book("B", authors=["HOMER"], tags=["myth"])
    assert book["authors"] == ["Homer"]
    assert book["tags"] == ["Myth"]


def test_stats(client, add_book):
    empty = client.get("/api/stats").json()
    assert empty["total_books"] == 0
    assert empty["average_rating"] is None
    assert empty["by_status"] == {"to_read": 0, "reading": 0, "read": 0, "abandoned": 0}

    add_book(
        "A",
        authors=["Ovid"],
        status="read",
        rating=5,
        pages=700,
        favorite=True,
        finished_on="2024-03-01",
        tags=["myth"],
        copies=[{"format": "hardcover"}, {"owner": "Demeter", "lent_to": "Hecate"}],
    )
    add_book("B", authors=["Ovid"], status="read", rating=4, pages=300, finished_on="2025-01-01")
    add_book("C", status="read", rating=4.5, pages=100)
    add_book("D", pages=999)

    stats = client.get("/api/stats").json()
    assert stats["total_books"] == 4
    assert stats["owned_books"] == 1
    assert stats["total_copies"] == 2
    assert stats["favorites"] == 1
    assert stats["rated"] == 3
    assert stats["average_rating"] == 4.5
    assert stats["pages_read"] == 1100
    assert stats["by_status"]["read"] == 3
    assert stats["by_status"]["to_read"] == 0  # "D" has no status, which is not counted
    assert stats["rating_distribution"]["5"] == 1
    assert stats["rating_distribution"]["4.5"] == 1
    assert stats["rating_distribution"]["0.5"] == 0
    assert len(stats["rating_distribution"]) == 10
    assert stats["read_per_year"] == [{"year": 2024, "count": 1}, {"year": 2025, "count": 1}]
    assert stats["copies_by_owner"] == [
        {"name": "Demeter", "count": 1},
        {"name": "Me", "count": 1},
    ]
    assert stats["copies_by_format"]["hardcover"] == 1
    assert stats["copies_by_format"]["paperback"] == 1
    assert stats["copies_by_format"]["ebook"] == 0
    assert stats["lent_out"] == 1
    assert stats["top_authors"] == [{"name": "Ovid", "count": 2}]
    assert stats["top_tags"] == [{"name": "myth", "count": 1}]


OPEN_LIBRARY_RECORD = {
    "ISBN:9780441172719": {
        "title": "Dune",
        "authors": [{"name": "Frank Herbert"}],
        "publishers": [{"name": "Ace"}],
        "publish_date": "1990",
        "number_of_pages": 535,
        "cover": {"medium": "https://covers.openlibrary.org/b/id/1-M.jpg"},
    }
}


def test_isbn_lookup(app, client):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return OPEN_LIBRARY_RECORD

    app.state.openlibrary_fetch = fake_fetch
    response = client.get("/api/lookup/isbn/0-441-17271-7")
    assert response.status_code == 200, response.text
    found = response.json()
    assert found["title"] == "Dune"
    assert found["authors"] == ["Frank Herbert"]
    assert found["isbn"] == "9780441172719"
    assert found["publisher"] == "Ace"
    assert found["published_year"] == 1990
    assert found["pages"] == 535
    assert found["cover_url"].endswith("1-M.jpg")
    assert "ISBN%3A9780441172719" in calls[0]


@pytest.mark.parametrize(
    ("fetch_result", "status"),
    [({}, 404), (OpenLibraryError("down"), 502), ([], 502)],
)
def test_isbn_lookup_failures(app, client, fetch_result, status):
    def fake_fetch(url):
        if isinstance(fetch_result, Exception):
            raise fetch_result
        return fetch_result

    app.state.openlibrary_fetch = fake_fetch
    assert client.get("/api/lookup/isbn/9780441172719").status_code == status


def test_isbn_lookup_rejects_bad_isbn(client):
    assert client.get("/api/lookup/isbn/12345").status_code == 422


def test_isbn_lookup_can_be_disabled(db_path):
    settings = Settings(database_url=f"sqlite:///{db_path}", default_owner="Me", openlibrary=False)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/lookup/isbn/9780441172719").status_code == 403
        assert client.get("/api/config").json()["openlibrary"] is False


def test_export_and_import_endpoints(client, add_book):
    add_book("Circe", authors=["Madeline Miller"], rating=4.5, copies=[{"owner": "Demeter"}, {}])

    response = client.get("/api/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["format"] == "persephone-export"
    assert len(payload["books"][0]["copies"]) == 2

    only_demeter = client.get("/api/export", params={"owner": "Demeter"}).json()
    assert only_demeter["owner"] == "Demeter"
    assert [c["owner"] for c in only_demeter["books"][0]["copies"]] == ["Demeter"]

    payload["books"][0]["rating"] = 2
    payload["books"].append({"title": "New one"})
    payload["books"].append({"title": ""})
    summary = client.post("/api/import", json=payload).json()
    assert summary["created"] == 1
    assert summary["updated"] == 1
    assert len(summary["errors"]) == 1
    books = client.get("/api/books").json()["items"]
    assert {b["title"]: b["rating"] for b in books} == {"Circe": 2, "New one": None}


def test_import_endpoint_rejects_foreign_json(client):
    response = client.post("/api/import", json={"hello": "world"})
    assert response.status_code == 422
    assert "Persephone export" in response.json()["detail"]


def test_publishers_facet(client, add_book):
    add_book("A", publisher="Ace")
    add_book("B", publisher="ace")
    add_book("C", publisher="Tor")
    assert client.get("/api/publishers").json() == [
        {"name": "Ace", "count": 2, "origin": None, "year_created": None, "year_closed": None},
        {"name": "Tor", "count": 1, "origin": None, "year_created": None, "year_closed": None},
    ]


def test_shutdown_stops_the_server_after_answering(app, client):
    calls = []
    app.state.shutdown = lambda: calls.append("stop")
    response = client.post("/api/shutdown")
    assert (response.status_code, response.json()) == (202, {"status": "stopping"})
    assert calls == ["stop"]


def test_stop_server_signals_itself_or_the_reloader(monkeypatch):
    import os
    import signal

    from persephone import api

    sent = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.delenv(api.SUPERVISOR_PID_ENV, raising=False)
    api.stop_server()
    monkeypatch.setenv(api.SUPERVISOR_PID_ENV, "4242")
    api.stop_server()
    assert sent == [(os.getpid(), signal.SIGINT), (4242, signal.SIGINT)]

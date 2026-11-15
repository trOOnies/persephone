import pytest


def titles(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [book["title"] for book in response.json()["items"]]


def test_create_and_get_book(client):
    response = client.post(
        "/api/books",
        json={
            "title": "Circe",
            "authors": ["Madeline Miller"],
            "isbn": "978-0-316-55634-7",
            "tags": ["mythology"],
            "status": "read",
            "rating": 4.5,
            "favorite": True,
            "finished_on": "2025-02-20",
            "copies": [{"format": "hardcover", "location": "Study"}],
        },
    )
    assert response.status_code == 201, response.text
    book = response.json()
    assert book["isbn"] == "9780316556347"
    assert book["authors"] == ["Madeline Miller"]
    assert book["tags"] == ["mythology"]
    assert book["owned"] is True
    assert book["copies"][0]["owner"] == "Me"  # the default owner
    assert book["copies"][0]["format"] == "hardcover"

    fetched = client.get(f"/api/books/{book['id']}").json()
    assert fetched == book


def test_get_missing_book(client):
    assert client.get("/api/books/999").status_code == 404
    assert client.patch("/api/books/999", json={"rating": 3}).status_code == 404
    assert client.delete("/api/books/999").status_code == 404


def test_create_requires_title(client):
    response = client.post("/api/books", json={"title": ""})
    assert response.status_code == 422


def test_duplicate_isbn_conflicts(client, add_book):
    add_book("Dune", isbn="9780441172719")
    response = client.post("/api/books", json={"title": "Dune again", "isbn": "0441172717"})
    assert response.status_code == 409
    assert "9780441172719" in response.json()["detail"]


def test_update_book(client, add_book):
    book = add_book("Odyssey", authors=["Homer"], tags=["epic"], copies=[{}])
    response = client.patch(
        f"/api/books/{book['id']}",
        json={
            "title": "The Odyssey",
            "authors": ["Homer", "Emily Wilson"],
            "tags": ["epic", "classics"],
            "rating": 5,
            "status": "reading",
        },
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["title"] == "The Odyssey"
    assert updated["authors"] == ["Homer", "Emily Wilson"]
    assert updated["tags"] == ["classics", "epic"]  # tags are listed alphabetically
    assert updated["rating"] == 5
    assert updated["status"] == "reading"
    assert updated["updated_at"] >= book["updated_at"]


def test_update_reorders_and_removes_authors(client, add_book):
    book = add_book("Good Omens", authors=["Terry Pratchett", "Neil Gaiman"])
    response = client.patch(f"/api/books/{book['id']}", json={"authors": ["Neil Gaiman"]})
    assert response.json()["authors"] == ["Neil Gaiman"]
    # The orphaned author disappears from the facet list.
    assert [a["name"] for a in client.get("/api/authors").json()] == ["Neil Gaiman"]


def test_update_can_clear_rating(client, add_book):
    book = add_book(rating=3)
    response = client.patch(f"/api/books/{book['id']}", json={"rating": None})
    assert response.json()["rating"] is None


def test_status_starts_empty_and_can_be_cleared(client, add_book):
    book = add_book(copies=[{}])
    assert book["status"] is None
    planned = client.patch(f"/api/books/{book['id']}", json={"status": "to_read"}).json()
    assert planned["status"] == "to_read"
    cleared = client.patch(f"/api/books/{book['id']}", json={"status": None}).json()
    assert cleared["status"] is None


def test_update_validation(client, add_book):
    book = add_book(started_on="2024-05-10")
    url = f"/api/books/{book['id']}"
    assert client.patch(url, json={"rating": 7}).status_code == 422
    assert client.patch(url, json={"title": None}).status_code == 422
    assert client.patch(url, json={"status": "lost"}).status_code == 422
    # The finished date is checked against the stored start date.
    response = client.patch(url, json={"finished_on": "2024-05-01"})
    assert response.status_code == 422
    assert "finished_on" in response.json()["detail"]


def test_update_isbn_conflict(client, add_book):
    add_book("Dune", isbn="9780441172719")
    other = add_book("Other")
    response = client.patch(f"/api/books/{other['id']}", json={"isbn": "9780441172719"})
    assert response.status_code == 409


def test_update_keeping_same_isbn(client, add_book):
    book = add_book("Dune", isbn="9780441172719")
    response = client.patch(f"/api/books/{book['id']}", json={"isbn": "9780441172719"})
    assert response.status_code == 200


def test_delete_book(client, add_book):
    book = add_book("Gone", authors=["Nobody"], tags=["temp"], copies=[{}])
    assert client.delete(f"/api/books/{book['id']}").status_code == 204
    assert client.get(f"/api/books/{book['id']}").status_code == 404
    assert client.get("/api/authors").json() == []
    assert client.get("/api/tags").json() == []
    assert client.get("/api/owners").json() == []


@pytest.fixture
def library(add_book):
    add_book(
        "Metamorphoses",
        authors=["Ovid"],
        tags=["mythology", "poetry"],
        status="read",
        rating=5,
        favorite=True,
        published_year=8,
        finished_on="2024-03-12",
        copies=[{"owner": "Me"}],
    )
    add_book(
        "Circe",
        authors=["Madeline Miller"],
        series="Greek retellings",
        tags=["mythology"],
        status="read",
        rating=4.5,
        published_year=2018,
        finished_on="2025-02-20",
        copies=[{"owner": "Demeter", "lent_to": "Hecate"}],
    )
    add_book(
        "Dune",
        authors=["Frank Herbert"],
        isbn="9780441172719",
        publisher="Ace",
        wanted=True,
        published_year=1965,
    )
    add_book("Anonymous Ballads", rating=2)


def test_list_default_sort_is_title(client, library):
    response = client.get("/api/books")
    assert titles(response) == ["Anonymous Ballads", "Circe", "Dune", "Metamorphoses"]
    assert response.json()["total"] == 4


def test_shelf_sort_puts_favorites_then_owned_first(client, library, add_book):
    # Metamorphoses: favorite and owned; Circe: owned; the others neither.
    add_book("Beowulf", favorite=True)  # a favorite that is not owned
    add_book("Aeneid", copies=[{}])  # owned, not a favorite
    shelf = client.get("/api/books", params={"sort": "shelf"})
    assert titles(shelf) == [
        "Beowulf",
        "Metamorphoses",
        "Aeneid",
        "Circe",
        "Anonymous Ballads",
        "Dune",
    ]
    reverse = client.get("/api/books", params={"sort": "shelf", "order": "desc"})
    assert titles(reverse) == [
        "Metamorphoses",
        "Beowulf",
        "Circe",
        "Aeneid",
        "Dune",
        "Anonymous Ballads",
    ]
    page = client.get("/api/books", params={"sort": "shelf", "limit": 2, "offset": 2})
    assert titles(page) == ["Aeneid", "Circe"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({"q": "circe"}, ["Circe"]),
        ({"q": "miller"}, ["Circe"]),  # author
        ({"q": "retellings"}, ["Circe"]),  # series
        ({"q": "ace"}, ["Dune"]),  # publisher
        ({"q": "9780441"}, ["Dune"]),  # isbn
        ({"q": "ovid metamorph"}, ["Metamorphoses"]),  # every word must match
        ({"q": "nothing-like-this"}, []),
        ({"status": "read"}, ["Circe", "Metamorphoses"]),
        ({"favorite": True}, ["Metamorphoses"]),
        ({"favorite": False}, ["Anonymous Ballads", "Circe", "Dune"]),
        ({"owned": True}, ["Circe", "Metamorphoses"]),
        ({"owned": False}, ["Anonymous Ballads", "Dune"]),
        ({"series": "GREEK RETELLINGS"}, ["Circe"]),
        ({"series": "Greek"}, []),  # the whole name, not a part of it
        ({"wanted": True}, ["Dune"]),
        ({"wanted": False}, ["Anonymous Ballads", "Circe", "Metamorphoses"]),
        ({"owned": False, "wanted": False}, ["Anonymous Ballads"]),
        ({"owner": "demeter"}, ["Circe"]),
        ({"lent": True}, ["Circe"]),
        ({"lent": False}, ["Anonymous Ballads", "Dune", "Metamorphoses"]),
        ({"author": "OVID"}, ["Metamorphoses"]),
        ({"tag": "Mythology"}, ["Circe", "Metamorphoses"]),
        ({"min_rating": 4.5}, ["Circe", "Metamorphoses"]),
        ({"status": "read", "tag": "poetry"}, ["Metamorphoses"]),
    ],
)
def test_list_filters(client, library, query, expected):
    assert titles(client.get("/api/books", params=query)) == expected


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        ("rating", "desc", ["Metamorphoses", "Circe", "Anonymous Ballads", "Dune"]),
        ("rating", "asc", ["Anonymous Ballads", "Circe", "Metamorphoses", "Dune"]),  # nulls last
        ("year", "desc", ["Circe", "Dune", "Metamorphoses", "Anonymous Ballads"]),
        ("author", "asc", ["Frank Herbert", "Madeline Miller", "Ovid", None]),
        ("finished", "desc", ["Circe", "Metamorphoses", "Anonymous Ballads", "Dune"]),
        ("added", "asc", ["Metamorphoses", "Circe", "Dune", "Anonymous Ballads"]),
        ("title", "desc", ["Metamorphoses", "Dune", "Circe", "Anonymous Ballads"]),
    ],
)
def test_list_sorting(client, library, sort, order, expected):
    response = client.get("/api/books", params={"sort": sort, "order": order})
    if sort == "author":
        books = response.json()["items"]
        assert [(b["authors"] or [None])[0] for b in books] == expected
    else:
        assert titles(response) == expected


def test_list_rejects_unknown_sort(client):
    assert client.get("/api/books", params={"sort": "price"}).status_code == 422
    assert client.get("/api/books", params={"order": "sideways"}).status_code == 422


def test_list_pagination(client, library):
    first = client.get("/api/books", params={"limit": 3}).json()
    second = client.get("/api/books", params={"limit": 3, "offset": 3}).json()
    assert first["total"] == second["total"] == 4
    assert len(first["items"]) == 3
    assert [b["title"] for b in second["items"]] == ["Metamorphoses"]
    assert client.get("/api/books", params={"limit": 0}).status_code == 422


def test_ownership_starts_empty(add_book):
    book = add_book()
    assert (book["ownership"], book["owned"], book["wanted"]) == (None, False, False)
    assert add_book("Iliad", wanted=True)["ownership"] == "wanted"
    # Copies make it owned, whatever "wanted" says.
    owned = add_book("Aeneid", wanted=True, copies=[{}])
    assert (owned["ownership"], owned["wanted"]) == ("owned", False)


def test_reading_status_needs_a_copy(client, add_book):
    response = client.post("/api/books", json={"title": "Dune", "status": "read"})
    assert response.status_code == 422
    book = add_book()
    response = client.patch(f"/api/books/{book['id']}", json={"status": "reading"})
    assert response.status_code == 422
    assert "owned" in response.json()["detail"]
    # Owning it and reading it in the same request is fine.
    both = client.patch(
        f"/api/books/{book['id']}", json={"ownership": "owned", "status": "reading"}
    ).json()
    assert (both["ownership"], both["status"]) == ("owned", "reading")


def test_setting_ownership(client, add_book):
    book = add_book()
    url = f"/api/books/{book['id']}"
    wanted = client.patch(url, json={"ownership": "wanted"}).json()
    assert (wanted["ownership"], wanted["copies"]) == ("wanted", [])
    owned = client.patch(url, json={"ownership": "owned"}).json()
    assert (owned["ownership"], owned["wanted"]) == ("owned", False)
    assert [c["owner"] for c in owned["copies"]] == ["Me"]  # the default owner
    again = client.patch(url, json={"ownership": "owned", "status": "read"}).json()
    assert len(again["copies"]) == 1  # no second copy
    # Leaving "owned" removes the copies and the reading status.
    cleared = client.patch(url, json={"ownership": None}).json()
    assert (cleared["ownership"], cleared["status"], cleared["copies"]) == (None, None, [])


def test_copies_drive_ownership(client, add_book):
    book = add_book(wanted=True)
    copy = client.post(f"/api/books/{book['id']}/copies", json={}).json()
    client.patch(f"/api/books/{book['id']}", json={"status": "read"})
    owned = client.get(f"/api/books/{book['id']}").json()
    assert (owned["ownership"], owned["status"]) == ("owned", "read")
    client.delete(f"/api/copies/{copy['id']}")
    gone = client.get(f"/api/books/{book['id']}").json()
    assert (gone["ownership"], gone["status"]) == (None, None)

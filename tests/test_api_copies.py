def test_add_update_delete_copy(client, add_book):
    book = add_book("Dune")
    assert book["owned"] is False

    response = client.post(
        f"/api/books/{book['id']}/copies",
        json={"format": "ebook", "acquired_on": "2025-01-05", "notes": "Kindle"},
    )
    assert response.status_code == 201, response.text
    copy = response.json()
    assert copy == {
        "id": copy["id"],
        "book_id": book["id"],
        "owner": "Me",
        "format": "ebook",
        "location": None,
        "acquired_on": "2025-01-05",
        "lent_to": None,
        "notes": "Kindle",
    }
    assert client.get(f"/api/books/{book['id']}").json()["owned"] is True

    response = client.patch(
        f"/api/copies/{copy['id']}", json={"lent_to": "Hecate", "owner": "Kore"}
    )
    assert response.status_code == 200
    assert response.json()["lent_to"] == "Hecate"
    assert response.json()["owner"] == "Kore"

    response = client.patch(f"/api/copies/{copy['id']}", json={"lent_to": None})
    assert response.json()["lent_to"] is None
    assert response.json()["owner"] == "Kore"  # untouched

    assert client.delete(f"/api/copies/{copy['id']}").status_code == 204
    assert client.get(f"/api/books/{book['id']}").json()["copies"] == []


def test_copy_validation(client, add_book):
    book = add_book()
    url = f"/api/books/{book['id']}/copies"
    assert client.post(url, json={"format": "scroll"}).status_code == 422
    copy = client.post(url, json={"owner": "Demeter"}).json()
    assert copy["format"] == "paperback"
    assert client.patch(f"/api/copies/{copy['id']}", json={"owner": ""}).status_code == 422
    assert client.patch(f"/api/copies/{copy['id']}", json={"format": None}).status_code == 422


def test_missing_copy(client):
    assert client.post("/api/books/42/copies", json={}).status_code == 404
    assert client.patch("/api/copies/42", json={"notes": "x"}).status_code == 404
    assert client.delete("/api/copies/42").status_code == 404


def test_copies_bump_book_updated_at(client, add_book):
    book = add_book()
    client.post(f"/api/books/{book['id']}/copies", json={})
    assert client.get(f"/api/books/{book['id']}").json()["updated_at"] >= book["updated_at"]


def test_owners_facet(client, add_book):
    add_book("A", copies=[{"owner": "Demeter"}, {}])
    add_book("B", copies=[{"owner": "Demeter"}])
    assert client.get("/api/owners").json() == [
        {"name": "Demeter", "count": 2},
        {"name": "Me", "count": 1},
    ]

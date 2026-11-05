import pytest


@pytest.fixture
def books(add_book):
    """A wishlist book and an owned one."""
    return {
        "wish": add_book("Circe", authors=["Madeline Miller"]),
        "owned": add_book("Dune", authors=["Frank Herbert"], copies=[{"location": "Study"}]),
    }


def add(client, book, price, discount=None):
    body = {"book_id": book["id"], "price": price}
    if discount is not None:
        body["discount"] = discount
    return client.post("/api/carts/active/items", json=body)


def owned(client, book) -> bool:
    return client.get(f"/api/books/{book['id']}").json()["owned"]


def test_no_active_cart_at_first(client):
    assert client.get("/api/carts/active").json() is None
    assert client.get("/api/carts").json() == []


def test_add_items_and_totals(client, books):
    first = add(client, books["wish"], 20, discount=5)
    assert first.status_code == 201
    cart = add(client, books["owned"], 12.99).json()
    assert cart["status"] == "open"
    assert [(i["title"], i["owned"], i["total"]) for i in cart["items"]] == [
        ("Circe", False, 15.0),
        ("Dune", True, 12.99),
    ]
    cart = client.patch(f"/api/carts/{cart['id']}", json={"shipping": 4.5}).json()
    assert (cart["subtotal"], cart["discount"], cart["shipping"], cart["total"]) == (
        32.99,
        5.0,
        4.5,
        32.49,
    )
    assert client.get("/api/carts/active").json()["id"] == cart["id"]


def test_item_validation(client, books):
    assert add(client, books["wish"], 10, discount=11).status_code == 422
    assert add(client, books["wish"], -1).status_code == 422
    assert add(client, {"id": 999}, 10).status_code == 404
    assert add(client, books["wish"], 10).status_code == 201
    duplicate = add(client, books["wish"], 10)
    assert duplicate.status_code == 409
    assert "already in the cart" in duplicate.json()["detail"]


def test_edit_and_remove_items(client, books):
    cart = add(client, books["wish"], 10).json()
    item = cart["items"][0]["id"]
    changed = client.patch(f"/api/cart-items/{item}", json={"price": 8, "discount": 1}).json()
    assert changed["total"] == 7.0
    too_much = client.patch(f"/api/cart-items/{item}", json={"discount": 9})
    assert too_much.status_code == 422
    assert client.delete(f"/api/cart-items/{item}").json()["items"] == []
    assert client.patch("/api/cart-items/999", json={"price": 1}).status_code == 404


def test_save_marks_books_not_owned(client, books):
    add(client, books["wish"], 10)
    cart = add(client, books["owned"], 10).json()
    saved = client.post(f"/api/carts/{cart['id']}/save").json()
    assert saved["status"] == "saved"
    assert saved["saved_at"] is not None
    assert not owned(client, books["owned"])
    assert client.get("/api/carts/active").json() is None
    assert [c["id"] for c in client.get("/api/carts").json()] == [cart["id"]]
    # Saved carts are read-only until reopened.
    item = saved["items"][0]["id"]
    assert client.patch(f"/api/cart-items/{item}", json={"price": 1}).status_code == 409
    assert client.patch(f"/api/carts/{cart['id']}", json={"shipping": 1}).status_code == 409


def test_empty_cart_cannot_be_saved_or_bought(client, books):
    cart = add(client, books["wish"], 10).json()
    client.delete(f"/api/cart-items/{cart['items'][0]['id']}")
    assert client.post(f"/api/carts/{cart['id']}/save").status_code == 409
    assert client.post(f"/api/carts/{cart['id']}/buy").status_code == 409


def test_buy_marks_books_owned_and_keeps_the_cart(client, books):
    cart = add(client, books["wish"], 10).json()
    bought = client.post(f"/api/carts/{cart['id']}/buy").json()
    assert bought["status"] == "bought"
    assert bought["bought_on"] is not None
    book = client.get(f"/api/books/{books['wish']['id']}").json()
    assert book["owned"]
    assert [(c["owner"], c["notes"]) for c in book["copies"]] == [("Me", f"Cart #{cart['id']}")]
    assert [c["status"] for c in client.get("/api/carts").json()] == ["bought"]
    assert client.post(f"/api/carts/{cart['id']}/buy").status_code == 409
    assert client.post(f"/api/carts/{cart['id']}/edit").status_code == 409


def test_buy_a_saved_cart(client, books):
    cart = add(client, books["owned"], 10).json()
    client.post(f"/api/carts/{cart['id']}/save")
    assert not owned(client, books["owned"])
    client.post(f"/api/carts/{cart['id']}/buy")
    assert owned(client, books["owned"])


def test_edit_reopens_a_saved_cart(client, books):
    first = add(client, books["wish"], 10).json()
    client.post(f"/api/carts/{first['id']}/save")
    second = add(client, books["owned"], 5).json()
    assert second["id"] != first["id"]
    blocked = client.post(f"/api/carts/{first['id']}/edit")
    assert blocked.status_code == 409
    assert "active cart" in blocked.json()["detail"]

    client.delete(f"/api/cart-items/{second['items'][0]['id']}")  # now empty: discarded
    reopened = client.post(f"/api/carts/{first['id']}/edit").json()
    assert reopened["status"] == "open"
    assert client.get("/api/carts/active").json()["id"] == first["id"]
    assert client.get(f"/api/carts/{second['id']}").status_code == 404
    item = reopened["items"][0]["id"]
    assert client.patch(f"/api/cart-items/{item}", json={"price": 9}).json()["total"] == 9.0


def test_delete_a_never_saved_cart_leaves_books_alone(client, books):
    cart = add(client, books["owned"], 10).json()
    assert client.delete(f"/api/carts/{cart['id']}").status_code == 204
    assert owned(client, books["owned"])
    assert client.get("/api/carts/active").json() is None


def test_delete_a_bought_cart_marks_books_not_owned(client, books):
    cart = add(client, books["wish"], 10).json()
    client.post(f"/api/carts/{cart['id']}/buy")
    assert owned(client, books["wish"])
    assert client.delete(f"/api/carts/{cart['id']}").status_code == 204
    assert not owned(client, books["wish"])
    assert client.get("/api/carts").json() == []
    assert client.delete(f"/api/carts/{cart['id']}").status_code == 404


def test_deleting_a_book_removes_it_from_carts(client, books):
    cart = add(client, books["wish"], 10).json()
    client.delete(f"/api/books/{books['wish']['id']}")
    assert client.get(f"/api/carts/{cart['id']}").json()["items"] == []


def test_currency_setting(monkeypatch):
    from persephone import config

    monkeypatch.setenv("PERSEPHONE_CURRENCY", " € ")
    assert config.get_settings().currency == "€"
    monkeypatch.delenv("PERSEPHONE_CURRENCY")
    assert config.get_settings().currency == "$"

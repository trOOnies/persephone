import io
import sqlite3
import urllib.error

import pytest

from persephone import covers
from persephone.config import Settings, sqlite_url
from persephone.db import open_database, session_scope
from persephone.models import Book

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 200
JPEG = b"\xff\xd8\xff\xe0" + b"\1" * 200


class Images(dict):
    """Fake images by URL, remembering every URL asked for."""

    calls: list[str]


@pytest.fixture
def images(app):
    """Serve fake images by URL; anything else is a 404."""
    served = Images()
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return served.get(url)

    app.state.cover_fetch = fetch
    served.calls = calls
    return served


def cover_path(settings, book):
    return settings.covers_dir / book["local_cover"].removeprefix("/covers/")


def test_cover_url_is_downloaded_and_served(client, add_book, images, settings):
    images["https://shop.example/dune.png"] = ("image/png", PNG)
    created = add_book("Dune", cover_url="https://shop.example/dune.png")
    book = client.get("/api/books").json()["items"][0]
    assert book["updated_at"] == created["updated_at"]  # saving the cover is not an edit
    assert book["local_cover"].startswith(f"/covers/{book['id']}-")
    assert book["local_cover"].endswith(".png")
    assert cover_path(settings, book).read_bytes() == PNG

    served = client.get(book["local_cover"])
    assert served.content == PNG
    assert "immutable" in served.headers["cache-control"]


def test_open_library_cover_by_isbn(client, add_book, images):
    url = "https://covers.openlibrary.org/b/isbn/9780441172719-L.jpg?default=false"
    images[url] = ("image/jpeg", JPEG)
    book = add_book("Dune", isbn="0-441-17271-7")
    assert client.get(f"/api/books/{book['id']}").json()["local_cover"].endswith(".jpg")


def test_no_cover_anywhere(client, add_book, images):
    book = add_book("Unknown", isbn="0-441-17271-7")
    assert client.get(f"/api/books/{book['id']}").json()["local_cover"] is None
    assert len(images.calls) == 1
    status = client.get("/api/covers").json()
    assert (status["pending"], status["unavailable"]) == (0, 1)
    client.post("/api/covers/download")
    assert len(images.calls) == 1  # a known miss is not asked again
    assert add_book("No ISBN")["local_cover"] is None
    assert len(images.calls) == 1  # nothing to ask for


def test_changing_the_cover_replaces_the_file(client, add_book, images, settings):
    images["https://a.example/1.png"] = ("image/png", PNG)
    images["https://a.example/2.jpg"] = ("image/jpeg", JPEG)
    book = add_book("Dune", cover_url="https://a.example/1.png")
    first = cover_path(settings, client.get(f"/api/books/{book['id']}").json())
    client.patch(f"/api/books/{book['id']}", json={"cover_url": "https://a.example/2.jpg"})
    second = cover_path(settings, client.get(f"/api/books/{book['id']}").json())
    assert second.suffix == ".jpg"
    assert second.is_file() and not first.exists()

    client.patch(f"/api/books/{book['id']}", json={"cover_url": None})
    assert client.get(f"/api/books/{book['id']}").json()["local_cover"] is None
    assert not second.exists()


def test_other_edits_do_not_download(client, add_book, images):
    book = add_book("Dune", cover_url="https://a.example/1.png")
    client.patch(f"/api/books/{book['id']}", json={"rating": 4})
    assert images.calls == ["https://a.example/1.png"]


def test_deleting_a_book_deletes_its_cover(client, add_book, images, settings):
    images["https://a.example/1.png"] = ("image/png", PNG)
    book = add_book("Dune", cover_url="https://a.example/1.png")
    path = cover_path(settings, client.get(f"/api/books/{book['id']}").json())
    client.delete(f"/api/books/{book['id']}")
    assert not path.exists()


def test_status_and_download_endpoints(client, add_book, images, app):
    def offline(url):
        raise covers.CoverError("offline")

    app.state.cover_fetch = offline  # the covers can only be fetched later
    add_book("A", cover_url="https://a.example/a.png")
    add_book("B")
    assert client.get("/api/covers").json()["saved"] == 0
    assert client.get("/api/covers").json()["pending"] == 1

    app.state.cover_fetch = lambda url: ("image/png", PNG)
    assert client.post("/api/covers/download").status_code == 202
    status = client.get("/api/covers").json()
    assert (status["saved"], status["pending"]) == (1, 0)


def test_download_all_reports_errors_and_cleans_up(sessions, tmp_path):
    settings = Settings(
        database_url="sqlite://",
        default_owner="Me",
        openlibrary=False,
        covers=str(tmp_path / "covers"),
    )
    with session_scope(sessions) as session:
        session.add_all(
            [
                Book(title="Good", cover_url="https://a.example/good.png"),
                Book(title="Html", cover_url="https://a.example/page.html"),
            ]
        )
    pages = {
        "https://a.example/good.png": ("image/png", PNG),
        "https://a.example/page.html": ("text/html", b"<html>" + b" " * 200),
    }
    (tmp_path / "covers").mkdir()
    (tmp_path / "covers" / "stale.jpg").write_bytes(JPEG)

    summary = covers.download_all(sessions, settings, fetch=pages.get)
    assert summary.saved == 1
    assert summary.errors == ["Html: https://a.example/page.html: not an image (text/html)"]
    assert [p.name.endswith(".png") for p in (tmp_path / "covers").iterdir()] == [True]

    again = covers.download_all(sessions, settings, fetch=pages.get)
    assert (again.saved, again.unchanged) == (0, 1)
    forced = covers.download_all(sessions, settings, fetch=pages.get, force=True)
    assert forced.saved == 1


def test_image_type_trusts_the_bytes():
    assert covers._image_type("text/plain", PNG) == ".png"
    assert covers._image_type("application/octet-stream", b"RIFF1234WEBPVP8 ") == ".webp"
    assert covers._image_type("image/avif", b"\0\0\0\x1cftypavif") == ".avif"
    assert covers._image_type("image/webp", b"????") == ".webp"
    assert covers._image_type("text/html", b"<html>") is None


class FakeResponse(io.BytesIO):
    def __init__(self, data, content_type):
        super().__init__(data)
        import email.message

        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_image(monkeypatch):
    monkeypatch.setattr(
        covers.urllib.request,
        "urlopen",
        lambda req, timeout: FakeResponse(PNG, "image/png; charset=binary"),
    )
    assert covers.fetch_image("https://a.example/x.png") == ("image/png", PNG)

    monkeypatch.setattr(covers, "MAX_BYTES", 10)
    with pytest.raises(covers.CoverError, match="larger than"):
        covers.fetch_image("https://a.example/x.png")


@pytest.mark.parametrize(("error", "expected"), [(404, None), (500, covers.CoverError)])
def test_fetch_image_http_errors(monkeypatch, error, expected):
    def fail(req, timeout):
        raise urllib.error.HTTPError(req.full_url, error, "nope", {}, None)

    monkeypatch.setattr(covers.urllib.request, "urlopen", fail)
    if expected is None:
        assert covers.fetch_image("https://a.example/x") is None
    else:
        with pytest.raises(expected):
            covers.fetch_image("https://a.example/x")


def test_fetch_image_network_error(monkeypatch):
    def fail(req, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(covers.urllib.request, "urlopen", fail)
    with pytest.raises(covers.CoverError, match="offline"):
        covers.fetch_image("https://a.example/x")


def test_cli(tmp_path, monkeypatch, capsys):
    db = tmp_path / "lib.db"
    sessions = open_database(sqlite_url(db))
    with session_scope(sessions) as session:
        session.add(Book(title="Dune", cover_url="https://a.example/d.png"))
    monkeypatch.delenv("PERSEPHONE_COVERS", raising=False)
    monkeypatch.setattr(covers, "fetch_image", lambda url: ("image/png", PNG))
    assert covers.main(["--db", str(db)]) == 0
    assert "1 saved" in capsys.readouterr().out
    assert len(list((tmp_path / "covers").iterdir())) == 1

    monkeypatch.setattr(covers, "fetch_image", lambda url: ("text/html", b"x" * 200))
    assert covers.main(["--db", str(db), "--force"]) == 1
    assert "not an image" in capsys.readouterr().out


def test_covers_dir_setting(tmp_path):
    def settings(url, covers=None):
        return Settings(database_url=url, default_owner="Me", openlibrary=True, covers=covers)

    assert settings(sqlite_url(tmp_path / "x.db")).covers_dir == tmp_path / "covers"
    assert settings("sqlite://").covers_dir.parts[-2:] == ("local", "covers")
    assert settings("sqlite://", covers="/srv/c").covers_dir.as_posix() == "/srv/c"


def test_new_columns_are_added_to_old_databases(tmp_path):
    path = tmp_path / "old.db"
    open_database(sqlite_url(path))
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE books DROP COLUMN cover_file")
    db.execute("ALTER TABLE books DROP COLUMN cover_source")
    db.commit()
    db.close()
    open_database(sqlite_url(path))
    db = sqlite3.connect(path)
    columns = {row[1] for row in db.execute("PRAGMA table_info(books)")}
    db.close()
    assert {"cover_file", "cover_source"} <= columns

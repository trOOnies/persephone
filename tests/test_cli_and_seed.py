import json
import urllib.error

import pytest

from persephone import config, openlibrary, seed, web
from persephone.db import open_database, session_scope
from persephone.schemas import BookCreate


def test_demo_books_are_valid():
    for record in seed.DEMO_BOOKS:
        BookCreate.model_validate(record)


def test_seed_is_idempotent(sessions):
    with session_scope(sessions) as session:
        first = seed.seed(session, "Me")
    with session_scope(sessions) as session:
        second = seed.seed(session, "Me")
    assert first.created == len(seed.DEMO_BOOKS)
    assert second.created == 0
    assert second.unchanged == len(seed.DEMO_BOOKS)


def test_seed_cli(tmp_path, capsys):
    db = tmp_path / "seed.db"
    assert seed.main(["--db", str(db)]) == 0
    assert f"Seeded {len(seed.DEMO_BOOKS)} books" in capsys.readouterr().out
    assert db.exists()


def test_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PERSEPHONE_DB", str(tmp_path / "a.db"))
    monkeypatch.setenv("PERSEPHONE_OWNER", "  Kore ")
    monkeypatch.setenv("PERSEPHONE_OPENLIBRARY", "off")
    settings = config.get_settings()
    assert settings.database_url == f"sqlite:///{tmp_path / 'a.db'}"
    assert settings.default_owner == "Kore"
    assert settings.openlibrary is False

    monkeypatch.setenv("PERSEPHONE_DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("PERSEPHONE_OWNER", "")
    monkeypatch.setenv("PERSEPHONE_OPENLIBRARY", "1")
    settings = config.get_settings()
    assert settings.database_url == "sqlite:///:memory:"
    assert settings.default_owner == config.DEFAULT_OWNER
    assert settings.openlibrary is True


def test_default_database_location(monkeypatch):
    monkeypatch.delenv("PERSEPHONE_DB")
    assert config.get_settings().database_url == "sqlite:///local/persephone.db"


def test_open_database_creates_folder(tmp_path):
    db = tmp_path / "nested" / "deeper" / "p.db"
    open_database(f"sqlite:///{db}")
    assert db.exists()


def test_session_scope_rolls_back(sessions):
    from persephone.models import Book

    with pytest.raises(RuntimeError), session_scope(sessions) as session:
        session.add(Book(title="Never saved"))
        session.flush()
        raise RuntimeError("boom")
    with session_scope(sessions) as session:
        assert session.query(Book).count() == 0


def test_web_main_runs_uvicorn(monkeypatch, tmp_path):
    calls = {}

    def fake_run(target, **kwargs):
        calls["target"] = target
        calls.update(kwargs)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    db = tmp_path / "web.db"
    web.main(["--reload", "--port", "9001", "--db", str(db)])
    assert calls["target"] == "persephone.web:create_app"
    assert calls["factory"] is True
    assert calls["reload"] is True
    assert calls["port"] == 9001
    assert calls["reload_dirs"]
    assert config.get_settings().database_url == f"sqlite:///{db}"

    web.main([])
    assert calls["reload"] is False
    assert calls["reload_dirs"] is None
    assert calls["host"] == "127.0.0.1"


# ------------------------------------------------------------ open library --


def test_parse_open_library_record():
    record = {
        "title": "Mythology",
        "subtitle": "Timeless Tales",
        "authors": [{"name": "Edith Hamilton"}, {"url": "no name"}],
        "publish_date": "May 1, 1998",
        "cover": {"small": "s.jpg", "large": "l.jpg"},
    }
    parsed = openlibrary.parse_book("9780316341516", record)
    assert parsed["authors"] == ["Edith Hamilton"]
    assert parsed["published_year"] == 1998
    assert parsed["cover_url"] == "l.jpg"
    assert parsed["publisher"] is None
    assert parsed["pages"] is None


def test_lookup_unknown_isbn():
    assert openlibrary.lookup_isbn("9780316341516", fetch=lambda url: {}) is None


def test_fetch_json_wraps_network_errors(monkeypatch):
    def fail(request, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(openlibrary.urllib.request, "urlopen", fail)
    with pytest.raises(openlibrary.OpenLibraryError, match="offline"):
        openlibrary._fetch_json("https://openlibrary.org/api/books")


def test_fetch_json_sends_user_agent(monkeypatch):
    import io

    seen = {}

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        seen["agent"] = request.get_header("User-agent")
        seen["timeout"] = timeout
        return FakeResponse(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(openlibrary.urllib.request, "urlopen", fake_urlopen)
    assert openlibrary._fetch_json("https://openlibrary.org/x") == {"ok": True}
    assert seen["agent"].startswith("Persephone/")
    assert seen["timeout"] == openlibrary.TIMEOUT_SECONDS

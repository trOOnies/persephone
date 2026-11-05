from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from persephone.config import Settings, sqlite_url
from persephone.db import open_database
from persephone.web import create_app


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Never touch the real library, and never call Open Library from tests."""
    monkeypatch.setenv("PERSEPHONE_DB", str(tmp_path / "env.db"))
    monkeypatch.delenv("PERSEPHONE_DATABASE_URL", raising=False)
    monkeypatch.delenv("PERSEPHONE_OWNER", raising=False)
    monkeypatch.delenv("PERSEPHONE_OPENLIBRARY", raising=False)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "library.db"


@pytest.fixture
def settings(db_path) -> Settings:
    return Settings(database_url=sqlite_url(db_path), default_owner="Me", openlibrary=True)


@pytest.fixture
def app(settings):
    app = create_app(settings)
    app.state.openlibrary_fetch = _no_network
    app.state.cover_fetch = lambda url: None  # no covers anywhere, unless a test says so
    return app


def _no_network(url: str) -> Any:  # pragma: no cover - only hit when a test forgets to stub
    raise AssertionError(f"unexpected network call to {url}")


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sessions(settings) -> sessionmaker[Session]:
    return open_database(settings.database_url)


@pytest.fixture
def session(sessions) -> Iterator[Session]:
    with sessions() as session:
        yield session
        session.rollback()


@pytest.fixture
def add_book(client):
    """Create a book through the API and return its JSON."""

    def _add(title: str = "Metamorphoses", **fields: Any) -> dict[str, Any]:
        response = client.post("/api/books", json={"title": title, **fields})
        assert response.status_code == 201, response.text
        return response.json()

    return _add

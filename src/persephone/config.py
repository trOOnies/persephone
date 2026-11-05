"""Runtime settings, read from environment variables.

``PERSEPHONE_DATABASE_URL``
    Full SQLAlchemy URL. Takes precedence over ``PERSEPHONE_DB``.
``PERSEPHONE_DB``
    Path to the SQLite file. Defaults to ``local/persephone.db`` (git-ignored).
``PERSEPHONE_OWNER``
    Name used for new copies when no owner is given. Defaults to ``Me``.
``PERSEPHONE_OPENLIBRARY``
    Set to ``0``/``false`` to never contact Open Library (covers and ISBN lookup).
``PERSEPHONE_CURRENCY``
    Symbol shown next to prices in the Budget tab. Defaults to ``$``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB_PATH = Path("local") / "persephone.db"
DEFAULT_OWNER = "Me"
DEFAULT_CURRENCY = "$"

_FALSY = {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class Settings:
    database_url: str
    default_owner: str
    openlibrary: bool
    currency: str = DEFAULT_CURRENCY


def sqlite_url(path: str | os.PathLike[str]) -> str:
    return f"sqlite:///{Path(path)}"


def get_settings() -> Settings:
    url = os.environ.get("PERSEPHONE_DATABASE_URL")
    if not url:
        url = sqlite_url(os.environ.get("PERSEPHONE_DB") or DEFAULT_DB_PATH)
    owner = os.environ.get("PERSEPHONE_OWNER", "").strip() or DEFAULT_OWNER
    openlibrary = os.environ.get("PERSEPHONE_OPENLIBRARY", "1").strip().lower() not in _FALSY
    currency = os.environ.get("PERSEPHONE_CURRENCY", "").strip() or DEFAULT_CURRENCY
    return Settings(
        database_url=url, default_owner=owner, openlibrary=openlibrary, currency=currency
    )

"""Small helpers shared by the command line tools."""

from __future__ import annotations

import argparse
import os

from sqlalchemy.orm import Session, sessionmaker

from persephone.config import Settings, get_settings
from persephone.db import open_database


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", help="SQLite file to use (default: local/persephone.db)")


def open_from_args(args: argparse.Namespace) -> tuple[sessionmaker[Session], Settings]:
    if getattr(args, "db", None):
        os.environ["PERSEPHONE_DB"] = args.db
        os.environ.pop("PERSEPHONE_DATABASE_URL", None)
    settings = get_settings()
    return open_database(settings.database_url), settings

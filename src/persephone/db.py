"""Database engine and session plumbing."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    """Create an engine; for SQLite, create the parent folder and enforce foreign keys."""
    url = make_url(database_url)
    connect_args: dict[str, object] = {}
    if url.get_backend_name() == "sqlite":
        connect_args["check_same_thread"] = False
        if url.database and url.database != ":memory:":
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(database_url, connect_args=connect_args)

    if url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    from persephone import models  # noqa: F401  (registers the tables)

    Base.metadata.create_all(engine)
    _upgrade(engine)


def _upgrade(engine: Engine) -> None:
    """Bring SQLite databases made by older versions up to date."""
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as connection:
        columns = {row[1]: row for row in connection.exec_driver_sql("PRAGMA table_info(books)")}
    if columns["status"][3]:  # NOT NULL: books had to start as "to_read"
        _rebuild_table(engine, "books")
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    """Add new columns to existing tables (``create_all`` only makes tables).

    Nullable columns, or NOT NULL ones with a server default to fill old rows.
    """
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            info = connection.exec_driver_sql(f"PRAGMA table_info({table.name})")
            have = {row[1] for row in info}
            for column in table.columns:
                if column.name in have:
                    continue
                kind = column.type.compile(engine.dialect)
                if column.nullable:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table.name} ADD COLUMN {column.name} {kind}"
                    )
                elif column.server_default is not None:
                    default = column.server_default.arg
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table.name} ADD COLUMN {column.name} {kind} "
                        f"NOT NULL DEFAULT {default}"
                    )


def _rebuild_table(engine: Engine, name: str) -> None:
    """Recreate a table from its current model, keeping its rows.

    SQLite cannot change a column's constraints in place. Foreign keys are off
    while the old table is dropped, so rows that point to it are left alone.
    """
    table = Base.metadata.tables[name]
    create = str(CreateTable(table).compile(engine)).strip()
    create = create.replace(f"CREATE TABLE {name} ", f"CREATE TABLE {name}_new ", 1)
    raw = engine.raw_connection()
    try:
        db = raw.driver_connection
        old = {row[1] for row in db.execute(f"PRAGMA table_info({name})")}
        shared = ", ".join(c.name for c in table.columns if c.name in old)
        db.execute("PRAGMA foreign_keys=OFF")
        try:
            db.execute("BEGIN")
            db.execute(create)
            db.execute(f"INSERT INTO {name}_new ({shared}) SELECT {shared} FROM {name}")
            db.execute(f"DROP TABLE {name}")
            db.execute(f"ALTER TABLE {name}_new RENAME TO {name}")
            for index in table.indexes:
                db.execute(str(CreateIndex(index).compile(engine)))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")
    finally:
        raw.close()


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def open_database(database_url: str) -> sessionmaker[Session]:
    """Create the engine, make sure the schema exists and return a session factory."""
    engine = make_engine(database_url)
    init_db(engine)
    return make_sessionmaker(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

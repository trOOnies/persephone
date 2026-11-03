"""FastAPI application factory and development server entry point.

Run it with ``python -m persephone.web [--reload] [--host H] [--port P] [--db PATH]``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from persephone import __version__
from persephone.api import router
from persephone.config import Settings, get_settings
from persephone.db import open_database

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="Persephone",
        version=__version__,
        description="A personal library: books, copies, favorites and ratings.",
    )
    app.state.settings = settings
    app.state.sessions = open_database(settings.database_url)
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="persephone", description="Run the Persephone web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="restart on code changes")
    parser.add_argument("--db", help="SQLite file to use (default: local/persephone.db)")
    args = parser.parse_args(argv)

    if args.db:
        # Passed through the environment so that reloaded workers see it too.
        os.environ["PERSEPHONE_DB"] = args.db
        os.environ.pop("PERSEPHONE_DATABASE_URL", None)

    uvicorn.run(
        "persephone.web:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(Path(__file__).parent)] if args.reload else None,
    )


if __name__ == "__main__":
    main()

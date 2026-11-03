"""JSON API, mounted under ``/api``."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from persephone import __version__, openlibrary, services, userdata
from persephone import isbn as isbn_utils
from persephone.config import Settings
from persephone.schemas import (
    AppConfig,
    BookCreate,
    BookOut,
    BookPage,
    BookUpdate,
    CopyCreate,
    CopyOut,
    CopyUpdate,
    ImportSummary,
    NameCount,
    Stats,
    Status,
)

router = APIRouter(prefix="/api")


def get_session(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.sessions()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _book_or_404(session: Session, book_id: int):
    try:
        return services.get_book(session, book_id)
    except services.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Book not found") from exc


def _copy_or_404(session: Session, copy_id: int):
    try:
        return services.get_copy(session, copy_id)
    except services.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Copy not found") from exc


def _book_out(session: Session, book_id: int) -> BookOut:
    session.expire_all()
    return BookOut.model_validate(services.get_book(session, book_id))


# ------------------------------------------------------------------ misc ---


@router.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/config", response_model=AppConfig, tags=["meta"])
def app_config(settings: SettingsDep) -> AppConfig:
    return AppConfig(
        version=__version__,
        default_owner=settings.default_owner,
        openlibrary=settings.openlibrary,
    )


# ----------------------------------------------------------------- books ---


@router.get("/books", response_model=BookPage, tags=["books"])
def list_books(
    session: SessionDep,
    q: str | None = None,
    status: Status | None = None,
    favorite: bool | None = None,
    owned: bool | None = None,
    owner: str | None = None,
    author: str | None = None,
    tag: str | None = None,
    lent: bool | None = None,
    min_rating: Annotated[float | None, Query(ge=0, le=5)] = None,
    sort: Annotated[str, Query(pattern="^(" + "|".join(services.SORT_KEYS) + ")$")] = "title",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    limit: Annotated[int, Query(ge=1, le=500)] = 60,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookPage:
    filters = services.BookFilters(
        q=q,
        status=status,
        favorite=favorite,
        owned=owned,
        owner=owner,
        author=author,
        tag=tag,
        min_rating=min_rating,
        lent=lent,
    )
    books, total = services.list_books(
        session, filters, sort=sort, descending=order == "desc", limit=limit, offset=offset
    )
    return BookPage(
        items=[BookOut.model_validate(book) for book in books],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/books", response_model=BookOut, status_code=201, tags=["books"])
def create_book(data: BookCreate, session: SessionDep, settings: SettingsDep) -> BookOut:
    try:
        book = services.create_book(session, data, settings.default_owner)
    except services.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _book_out(session, book.id)


@router.get("/books/{book_id}", response_model=BookOut, tags=["books"])
def get_book(book_id: int, session: SessionDep) -> BookOut:
    return BookOut.model_validate(_book_or_404(session, book_id))


@router.patch("/books/{book_id}", response_model=BookOut, tags=["books"])
def update_book(book_id: int, data: BookUpdate, session: SessionDep) -> BookOut:
    book = _book_or_404(session, book_id)
    try:
        services.update_book(session, book, data)
    except services.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _book_out(session, book_id)


@router.delete("/books/{book_id}", status_code=204, tags=["books"])
def delete_book(book_id: int, session: SessionDep) -> Response:
    services.delete_book(session, _book_or_404(session, book_id))
    return Response(status_code=204)


# ---------------------------------------------------------------- copies ---


@router.post("/books/{book_id}/copies", response_model=CopyOut, status_code=201, tags=["copies"])
def add_copy(book_id: int, data: CopyCreate, session: SessionDep, settings: SettingsDep) -> CopyOut:
    book = _book_or_404(session, book_id)
    return CopyOut.model_validate(services.add_copy(session, book, data, settings.default_owner))


@router.patch("/copies/{copy_id}", response_model=CopyOut, tags=["copies"])
def update_copy(copy_id: int, data: CopyUpdate, session: SessionDep) -> CopyOut:
    copy = _copy_or_404(session, copy_id)
    return CopyOut.model_validate(services.update_copy(session, copy, data))


@router.delete("/copies/{copy_id}", status_code=204, tags=["copies"])
def delete_copy(copy_id: int, session: SessionDep) -> Response:
    services.delete_copy(session, _copy_or_404(session, copy_id))
    return Response(status_code=204)


# ---------------------------------------------------------------- facets ---


@router.get("/authors", response_model=list[NameCount], tags=["facets"])
def list_authors(session: SessionDep) -> list[NameCount]:
    return services.list_authors(session)


@router.get("/tags", response_model=list[NameCount], tags=["facets"])
def list_tags(session: SessionDep) -> list[NameCount]:
    return services.list_tags(session)


@router.get("/owners", response_model=list[NameCount], tags=["facets"])
def list_owners(session: SessionDep) -> list[NameCount]:
    return services.list_owners(session)


@router.get("/stats", response_model=Stats, tags=["facets"])
def get_stats(session: SessionDep) -> Stats:
    return services.stats(session)


# ---------------------------------------------------------------- lookup ---


@router.get("/lookup/isbn/{isbn}", response_model=BookCreate, tags=["lookup"])
def lookup_isbn(isbn: str, request: Request, settings: SettingsDep) -> BookCreate:
    if not settings.openlibrary:
        raise HTTPException(status_code=403, detail="Open Library lookups are disabled")
    normalized = isbn_utils.normalize(isbn)
    if normalized is None:
        raise HTTPException(status_code=422, detail="Not a valid ISBN")
    fetch = getattr(request.app.state, "openlibrary_fetch", None) or openlibrary._fetch_json
    try:
        found = openlibrary.lookup_isbn(normalized, fetch=fetch)
    except openlibrary.OpenLibraryError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not found or not found.get("title"):
        raise HTTPException(status_code=404, detail="ISBN not found on Open Library")
    return BookCreate.model_validate(found)


# ---------------------------------------------------------------- backup ---


@router.get("/export", tags=["backup"])
def export_data(session: SessionDep, owner: str | None = None) -> JSONResponse:
    payload = userdata.export_data(session, owner=owner)
    filename = userdata.default_filename(owner, dt.date.today())
    return JSONResponse(
        payload, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/import", response_model=ImportSummary, tags=["backup"])
def import_data(
    payload: Annotated[dict[str, Any] | list[Any], Body()],
    session: SessionDep,
    settings: SettingsDep,
) -> ImportSummary:
    """Restore a file made by ``/api/export`` (or ``make export-data``)."""
    try:
        return userdata.import_data(session, payload, default_owner=settings.default_owner)
    except userdata.FormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

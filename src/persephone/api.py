"""JSON API, mounted under ``/api``."""

from __future__ import annotations

import datetime as dt
import os
import signal
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from persephone import __version__, budget, covers, openlibrary, services, userdata
from persephone import isbn as isbn_utils
from persephone.config import Settings
from persephone.models import Book
from persephone.schemas import (
    AppConfig,
    AuthorOut,
    BookCreate,
    BookOut,
    BookPage,
    BookUpdate,
    CartItemCreate,
    CartItemUpdate,
    CartOut,
    CartUpdate,
    CopyCreate,
    CopyOut,
    CopyUpdate,
    CoverStatus,
    ImportSummary,
    NameCount,
    PublisherOut,
    Stats,
    Status,
)

router = APIRouter(prefix="/api")

#: Set by ``persephone.web.main`` to the reloader's PID when running with ``--reload``.
SUPERVISOR_PID_ENV = "PERSEPHONE_SUPERVISOR_PID"


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


def _save_covers_later(
    request: Request,
    background: BackgroundTasks,
    session: Session,
    book_ids: list[int] | None = None,
) -> None:
    """Download covers once the response is sent (all books when ``book_ids`` is None)."""
    session.commit()  # the task reads the books with a session of its own
    background.add_task(
        covers.download_all,
        request.app.state.sessions,
        request.app.state.settings,
        book_ids=book_ids,
        fetch=getattr(request.app.state, "cover_fetch", None) or covers.fetch_image,
    )


def _book_out(session: Session, book_id: int) -> BookOut:
    session.expire_all()
    return BookOut.model_validate(services.get_book(session, book_id))


# ------------------------------------------------------------------ misc ---


@router.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


def stop_server() -> None:
    """Stop the server as Ctrl+C would: uvicorn finishes the open requests, then exits.

    With ``--reload`` the signal goes to the reloader, which stops its worker too.
    """
    pid = int(os.environ.get(SUPERVISOR_PID_ENV) or os.getpid())
    os.kill(pid, signal.SIGINT)


@router.post("/shutdown", status_code=202, tags=["meta"])
def shutdown(request: Request, background: BackgroundTasks) -> dict[str, str]:
    """Save and Exit: stop the Persephone server once this response is sent.

    Every change is committed when its request finishes, so nothing is left to save.
    """
    background.add_task(getattr(request.app.state, "shutdown", None) or stop_server)
    return {"status": "stopping"}


@router.get("/config", response_model=AppConfig, tags=["meta"])
def app_config(settings: SettingsDep) -> AppConfig:
    return AppConfig(
        version=__version__,
        default_owner=settings.default_owner,
        openlibrary=settings.openlibrary,
        currency=settings.currency,
    )


# ----------------------------------------------------------------- books ---


@router.get("/books", response_model=BookPage, tags=["books"])
def list_books(
    session: SessionDep,
    q: str | None = None,
    status: Status | None = None,
    favorite: bool | None = None,
    owned: bool | None = None,
    wanted: bool | None = None,
    series: Annotated[str | None, Query(description="Exact series name, any case")] = None,
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
        wanted=wanted,
        series=series,
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
def create_book(
    data: BookCreate,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
    background: BackgroundTasks,
) -> BookOut:
    try:
        book = services.create_book(session, data, settings.default_owner)
    except services.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _save_covers_later(request, background, session, [book.id])
    return _book_out(session, book.id)


@router.get("/books/{book_id}", response_model=BookOut, tags=["books"])
def get_book(book_id: int, session: SessionDep) -> BookOut:
    return BookOut.model_validate(_book_or_404(session, book_id))


@router.patch("/books/{book_id}", response_model=BookOut, tags=["books"])
def update_book(
    book_id: int,
    data: BookUpdate,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
    background: BackgroundTasks,
) -> BookOut:
    book = _book_or_404(session, book_id)
    try:
        services.update_book(session, book, data, settings.default_owner)
    except services.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if {"cover_url", "isbn"} & data.model_fields_set:
        _save_covers_later(request, background, session, [book_id])
    return _book_out(session, book_id)


@router.delete("/books/{book_id}", status_code=204, tags=["books"])
def delete_book(book_id: int, session: SessionDep, settings: SettingsDep) -> Response:
    book = _book_or_404(session, book_id)
    cover_file = book.cover_file
    services.delete_book(session, book)
    session.commit()
    if cover_file:
        (settings.covers_dir / cover_file).unlink(missing_ok=True)
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


@router.get("/authors", response_model=list[AuthorOut], tags=["facets"])
def list_authors(session: SessionDep) -> list[AuthorOut]:
    """Authors with their number of books and details (gender, origin, years)."""
    return services.list_authors(session)


@router.get("/publishers", response_model=list[PublisherOut], tags=["facets"])
def list_publishers(session: SessionDep) -> list[PublisherOut]:
    """Publishers named by books or described by an import, with their book counts."""
    return services.list_publishers(session)


@router.get("/tags", response_model=list[NameCount], tags=["facets"])
def list_tags(session: SessionDep) -> list[NameCount]:
    return services.list_tags(session)


@router.get("/owners", response_model=list[NameCount], tags=["facets"])
def list_owners(session: SessionDep) -> list[NameCount]:
    return services.list_owners(session)


@router.get("/stats", response_model=Stats, tags=["facets"])
def get_stats(session: SessionDep) -> Stats:
    return services.stats(session)


# ---------------------------------------------------------------- budget ---


def _cart_or_404(session: Session, cart_id: int):
    try:
        return budget.get_cart(session, cart_id)
    except services.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Cart not found") from exc


def _cart_item_or_404(session: Session, item_id: int):
    try:
        return budget.get_item(session, item_id)
    except services.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Cart item not found") from exc


def _cart_change(action, *args, **kwargs) -> CartOut:
    """Run a budget change, turning its errors into 404 / 409 / 422 answers."""
    try:
        return budget.cart_out(action(*args, **kwargs))
    except services.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except services.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/carts", response_model=list[CartOut], tags=["budget"])
def list_carts(session: SessionDep) -> list[CartOut]:
    """Cart history: saved and bought carts, newest first."""
    return [budget.cart_out(cart) for cart in budget.history(session)]


@router.get("/carts/active", response_model=CartOut | None, tags=["budget"])
def get_active_cart(session: SessionDep) -> CartOut | None:
    """The cart being filled, or ``null`` when there is none."""
    cart = budget.active_cart(session)
    return budget.cart_out(cart) if cart else None


@router.post("/carts/active/items", response_model=CartOut, status_code=201, tags=["budget"])
def add_to_cart(data: CartItemCreate, session: SessionDep) -> CartOut:
    """Add a book to the active cart (created if needed). Owned books are allowed."""
    return _cart_change(budget.add_item, session, data)


@router.get("/carts/{cart_id}", response_model=CartOut, tags=["budget"])
def get_cart(cart_id: int, session: SessionDep) -> CartOut:
    return budget.cart_out(_cart_or_404(session, cart_id))


@router.patch("/carts/{cart_id}", response_model=CartOut, tags=["budget"])
def update_cart(cart_id: int, data: CartUpdate, session: SessionDep) -> CartOut:
    return _cart_change(budget.update_cart, session, _cart_or_404(session, cart_id), data)


@router.post("/carts/{cart_id}/save", response_model=CartOut, tags=["budget"])
def save_cart(cart_id: int, session: SessionDep) -> CartOut:
    """Keep the active cart; its books become not owned (wishlist)."""
    return _cart_change(budget.save_cart, session, _cart_or_404(session, cart_id))


@router.post("/carts/{cart_id}/buy", response_model=CartOut, tags=["budget"])
def buy_cart(cart_id: int, session: SessionDep, settings: SettingsDep) -> CartOut:
    """Mark the cart as bought (it stays in the history); its books become owned."""
    cart = _cart_or_404(session, cart_id)
    return _cart_change(budget.buy_cart, session, cart, owner=settings.default_owner)


@router.post("/carts/{cart_id}/edit", response_model=CartOut, tags=["budget"])
def edit_cart(cart_id: int, session: SessionDep) -> CartOut:
    """Reopen a saved cart as the active one."""
    return _cart_change(budget.edit_cart, session, _cart_or_404(session, cart_id))


@router.delete("/carts/{cart_id}", status_code=204, tags=["budget"])
def delete_cart(cart_id: int, session: SessionDep) -> Response:
    """Delete a cart. If it was ever saved, its books become not owned (wishlist)."""
    budget.delete_cart(session, _cart_or_404(session, cart_id))
    return Response(status_code=204)


@router.patch("/cart-items/{item_id}", response_model=CartOut, tags=["budget"])
def update_cart_item(item_id: int, data: CartItemUpdate, session: SessionDep) -> CartOut:
    return _cart_change(budget.update_item, session, _cart_item_or_404(session, item_id), data)


@router.delete("/cart-items/{item_id}", response_model=CartOut, tags=["budget"])
def remove_cart_item(item_id: int, session: SessionDep) -> CartOut:
    return _cart_change(budget.remove_item, session, _cart_item_or_404(session, item_id))


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


# ---------------------------------------------------------------- covers ---


@router.get("/covers", response_model=CoverStatus, tags=["covers"])
def cover_status(session: SessionDep, settings: SettingsDep) -> CoverStatus:
    """How many book covers have a local copy."""
    books = session.scalars(select(Book)).unique().all()
    saved = sum(1 for book in books if book.cover_file)
    pending = sum(1 for book in books if covers.needs_download(book, settings))
    missing = sum(1 for book in books if covers.unavailable(book, settings))
    return CoverStatus(
        folder=str(settings.covers_dir), saved=saved, pending=pending, unavailable=missing
    )


@router.post("/covers/download", status_code=202, tags=["covers"])
def download_covers(
    request: Request, background: BackgroundTasks, session: SessionDep
) -> dict[str, str]:
    """Download every missing cover in the background."""
    _save_covers_later(request, background, session)
    return {"status": "started"}


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
    request: Request,
    background: BackgroundTasks,
) -> ImportSummary:
    """Restore a file made by ``/api/export`` (or ``make export-data``)."""
    try:
        summary = userdata.import_data(session, payload, default_owner=settings.default_owner)
    except userdata.FormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _save_covers_later(request, background, session)
    return summary

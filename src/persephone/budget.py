"""Budget tab: shopping carts of books with prices.

There is at most one ``open`` cart, the active one being filled. Saving it
keeps it for later (``saved``) and marks its books as not owned (wishlist);
a saved cart can be reopened for editing. Buying it (``bought``, from open or
saved) marks its books as owned, and the cart stays in the history. Deleting a
cart that was ever saved marks its books as not owned again; deleting an
active cart that was never saved just discards it.

Prices are stored in cents to keep totals exact.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from persephone.models import Book, Cart, CartItem, Copy, utcnow
from persephone.schemas import (
    CartItemCreate,
    CartItemOut,
    CartItemUpdate,
    CartOut,
    CartStatus,
    CartUpdate,
)
from persephone.services import ConflictError, NotFoundError, sync_ownership

OPEN, SAVED, BOUGHT = CartStatus.OPEN.value, CartStatus.SAVED.value, CartStatus.BOUGHT.value


def to_cents(amount: float) -> int:
    return round(amount * 100)


def _money(cents: int) -> float:
    return cents / 100


# ------------------------------------------------------------------ reading --


def cart_out(cart: Cart) -> CartOut:
    items = [
        CartItemOut(
            id=item.id,
            book_id=item.book_id,
            title=item.book.title,
            authors=item.book.authors,
            owned=bool(item.book.copies),
            price=_money(item.price_cents),
            discount=_money(item.discount_cents),
            total=_money(item.price_cents - item.discount_cents),
        )
        for item in cart.items
    ]
    subtotal = sum(item.price_cents for item in cart.items)
    discount = sum(item.discount_cents for item in cart.items)
    return CartOut(
        id=cart.id,
        status=cart.status,
        items=items,
        subtotal=_money(subtotal),
        discount=_money(discount),
        shipping=_money(cart.shipping_cents),
        total=_money(subtotal - discount + cart.shipping_cents),
        created_at=cart.created_at,
        updated_at=cart.updated_at,
        saved_at=cart.saved_at,
        bought_on=cart.bought_on,
    )


def get_cart(session: Session, cart_id: int) -> Cart:
    cart = session.get(Cart, cart_id)
    if cart is None:
        raise NotFoundError(f"cart {cart_id} not found")
    return cart


def get_item(session: Session, item_id: int) -> CartItem:
    item = session.get(CartItem, item_id)
    if item is None:
        raise NotFoundError(f"cart item {item_id} not found")
    return item


def active_cart(session: Session) -> Cart | None:
    return session.scalar(select(Cart).where(Cart.status == OPEN).order_by(Cart.id).limit(1))


def history(session: Session) -> list[Cart]:
    """Saved and bought carts, newest first."""
    return list(session.scalars(select(Cart).where(Cart.status != OPEN).order_by(Cart.id.desc())))


# ------------------------------------------------------------------ editing --


def _require_open(cart: Cart) -> None:
    if cart.status == BOUGHT:
        raise ConflictError("a bought cart cannot be changed")
    if cart.status != OPEN:
        raise ConflictError("edit the cart first to change it")


def _touch(session: Session, cart: Cart) -> Cart:
    cart.updated_at = utcnow()
    session.flush()
    return cart


def add_item(session: Session, data: CartItemCreate) -> Cart:
    """Add a book to the active cart, creating the cart when there is none."""
    book = session.get(Book, data.book_id)
    if book is None:
        raise NotFoundError(f"book {data.book_id} not found")
    cart = active_cart(session)
    if cart is None:
        cart = Cart(status=OPEN, shipping_cents=0)
        session.add(cart)
        session.flush()
    if any(item.book_id == book.id for item in cart.items):
        raise ConflictError(f"“{book.title}” is already in the cart")
    cart.items.append(
        CartItem(
            book=book,
            price_cents=to_cents(data.price),
            discount_cents=to_cents(data.discount),
        )
    )
    return _touch(session, cart)


def update_item(session: Session, item: CartItem, data: CartItemUpdate) -> Cart:
    _require_open(item.cart)
    price = to_cents(data.price) if data.price is not None else item.price_cents
    discount = to_cents(data.discount) if data.discount is not None else item.discount_cents
    if discount > price:
        raise ValueError("the discount cannot be more than the price")
    item.price_cents, item.discount_cents = price, discount
    return _touch(session, item.cart)


def remove_item(session: Session, item: CartItem) -> Cart:
    cart = item.cart
    _require_open(cart)
    cart.items.remove(item)
    return _touch(session, cart)


def update_cart(session: Session, cart: Cart, data: CartUpdate) -> Cart:
    _require_open(cart)
    cart.shipping_cents = to_cents(data.shipping)
    return _touch(session, cart)


# ---------------------------------------------------------------- lifecycle --


def _mark_not_owned(session: Session, cart: Cart) -> None:
    for item in cart.items:
        item.book.copies.clear()  # delete-orphan removes the copies
        sync_ownership(item.book)
        item.book.updated_at = utcnow()


def _mark_owned(session: Session, cart: Cart, owner: str, today: dt.date) -> None:
    for item in cart.items:
        if not item.book.copies:
            item.book.copies.append(
                Copy(owner=owner, format="paperback", acquired_on=today, notes=f"Cart #{cart.id}")
            )
            sync_ownership(item.book)
            item.book.updated_at = utcnow()


def save_cart(session: Session, cart: Cart) -> Cart:
    """Keep the active cart for later; its books become not owned (wishlist)."""
    _require_open(cart)
    if not cart.items:
        raise ConflictError("the cart is empty")
    cart.status = SAVED
    cart.saved_at = utcnow()
    _mark_not_owned(session, cart)
    return _touch(session, cart)


def buy_cart(session: Session, cart: Cart, *, owner: str, today: dt.date | None = None) -> Cart:
    """Mark an open or saved cart as bought; its books become owned.

    Books that have no copy get one for ``owner``, acquired today.
    """
    if cart.status == BOUGHT:
        raise ConflictError("the cart is already bought")
    if not cart.items:
        raise ConflictError("the cart is empty")
    today = today or dt.date.today()
    cart.status = BOUGHT
    cart.bought_on = today
    cart.saved_at = cart.saved_at or utcnow()
    _mark_owned(session, cart, owner, today)
    return _touch(session, cart)


def edit_cart(session: Session, cart: Cart) -> Cart:
    """Reopen a saved cart as the active one.

    An empty active cart is discarded to make room; one with books is a conflict.
    """
    if cart.status != SAVED:
        raise ConflictError("only saved carts can be edited")
    current = active_cart(session)
    if current is not None:
        if current.items:
            raise ConflictError("save or delete the active cart first")
        session.delete(current)
    cart.status = OPEN
    return _touch(session, cart)


def delete_cart(session: Session, cart: Cart) -> None:
    """Delete a cart. Books in a cart that was ever saved become not owned."""
    if cart.saved_at is not None:
        _mark_not_owned(session, cart)
    session.delete(cart)
    session.flush()

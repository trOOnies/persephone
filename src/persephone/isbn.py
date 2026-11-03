"""ISBN helpers: cleaning, validation and ISBN-10 -> ISBN-13 conversion.

Persephone stores every ISBN as a 13-digit string so that the same edition is
always recognized, whichever form it was typed in.
"""

from __future__ import annotations

import re

_NON_ISBN = re.compile(r"[^0-9Xx]")


def clean(value: str) -> str:
    """Drop everything that is not a digit or an X (hyphens, spaces, quotes, ``=``)."""
    return _NON_ISBN.sub("", value).upper()


def is_valid_isbn10(value: str) -> bool:
    if len(value) != 10 or not value[:9].isdigit():
        return False
    if not (value[9].isdigit() or value[9] == "X"):
        return False
    digits = [int(c) for c in value[:9]] + [10 if value[9] == "X" else int(value[9])]
    return sum((10 - i) * d for i, d in enumerate(digits)) % 11 == 0


def _isbn13_check_digit(first12: str) -> int:
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(first12))
    return (10 - total % 10) % 10


def is_valid_isbn13(value: str) -> bool:
    if len(value) != 13 or not value.isdigit():
        return False
    return _isbn13_check_digit(value[:12]) == int(value[12])


def to_isbn13(isbn10: str) -> str:
    """Convert a *valid* ISBN-10 to its ISBN-13 (978 prefix) form."""
    first12 = "978" + isbn10[:9]
    return first12 + str(_isbn13_check_digit(first12))


def normalize(value: str | None) -> str | None:
    """Return the ISBN-13 for ``value``, or ``None`` if it is empty or invalid."""
    if value is None:
        return None
    cleaned = clean(value)
    if is_valid_isbn13(cleaned):
        return cleaned
    if is_valid_isbn10(cleaned):
        return to_isbn13(cleaned)
    return None

import pytest

from persephone import isbn


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9780441172719", "9780441172719"),
        ("978-0-441-17271-9", "9780441172719"),
        ("0441172717", "9780441172719"),  # ISBN-10 is converted
        ("0-441-17271-7", "9780441172719"),
        ('="0441172717"', "9780441172719"),  # Goodreads quoting
        ("080442957X", "9780804429573"),  # X check digit
        ("080442957x", "9780804429573"),
    ],
)
def test_normalize_valid(raw, expected):
    assert isbn.normalize(raw) == expected


@pytest.mark.parametrize(
    "raw", [None, "", "   ", "123", "9780441172710", "0441172718", "abcdefghij", "X441172717"]
)
def test_normalize_invalid(raw):
    assert isbn.normalize(raw) is None


def test_validators():
    assert isbn.is_valid_isbn13("9780316556347")
    assert not isbn.is_valid_isbn13("978031655634")
    assert isbn.is_valid_isbn10("0316556343")
    assert not isbn.is_valid_isbn10("03165563X3")
    assert isbn.to_isbn13("0316556343") == "9780316556347"

"""ISBN normalisation for book editions (#265 3.2, C15 2.5).

An edition's identity is its ISBN-13. Spaces and hyphens are removed and a lowercase ``x`` is
read as ``X``; the check digit must be right. An ISBN-10 becomes ``978`` + its first nine digits +
a recomputed check digit. A ``979`` ISBN-13 has no ISBN-10. Nothing is ever guessed: a value with
a wrong check digit is refused, not corrected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEPARATORS = re.compile(r"[\s-]+")
_ISBN10 = re.compile(r"^\d{9}[\dX]$")
_ISBN13 = re.compile(r"^97[89]\d{10}$")


class InvalidIsbn(ValueError):
    """The text is not a valid ISBN-10 or ISBN-13."""


@dataclass(frozen=True)
class Isbn:
    isbn_13: str
    isbn_10: str | None


def _clean(value: str) -> str:
    return _SEPARATORS.sub("", value.strip()).upper()


def _isbn10_check(first_nine: str) -> str:
    total = sum((10 - index) * int(digit) for index, digit in enumerate(first_nine))
    check = (11 - total % 11) % 11
    return "X" if check == 10 else str(check)


def _isbn13_check(first_twelve: str) -> str:
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(first_twelve))
    return str((10 - total % 10) % 10)


def isbn10_to_13(isbn_10: str) -> str:
    body = "978" + isbn_10[:9]
    return body + _isbn13_check(body)


def isbn13_to_10(isbn_13: str) -> str | None:
    if not isbn_13.startswith("978"):
        return None
    body = isbn_13[3:12]
    return body + _isbn10_check(body)


def parse_isbn(value: str) -> Isbn:
    """Either form, validated, as the pair stored on an edition."""
    text = _clean(value)
    if _ISBN10.match(text):
        if _isbn10_check(text[:9]) != text[9]:
            raise InvalidIsbn(f"{value!r} is not a valid ISBN-10: the check digit is wrong")
        return Isbn(isbn_13=isbn10_to_13(text), isbn_10=text)
    if _ISBN13.match(text):
        if _isbn13_check(text[:12]) != text[12]:
            raise InvalidIsbn(f"{value!r} is not a valid ISBN-13: the check digit is wrong")
        return Isbn(isbn_13=text, isbn_10=isbn13_to_10(text))
    raise InvalidIsbn(f"{value!r} is not an ISBN: use 10 or 13 digits (978 or 979)")


def normalise_pair(isbn_13: str | None, isbn_10: str | None) -> Isbn | None:
    """What an edition stores for the ISBNs a person typed; both must name one book."""
    given = [value for value in (isbn_13, isbn_10) if value is not None and value.strip()]
    if not given:
        return None
    parsed = [parse_isbn(value) for value in given]
    if len({item.isbn_13 for item in parsed}) > 1:
        raise InvalidIsbn("The ISBN-10 and ISBN-13 name different books")
    if isbn_13 is not None and isbn_13.strip() and len(_clean(isbn_13)) != 13:
        raise InvalidIsbn(f"{isbn_13!r} is not an ISBN-13")
    if isbn_10 is not None and isbn_10.strip() and len(_clean(isbn_10)) != 10:
        raise InvalidIsbn(f"{isbn_10!r} is not an ISBN-10")
    return parsed[0]

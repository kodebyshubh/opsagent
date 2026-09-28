"""The only place that converts money (DEC-018).

Storage uses integer paise. Everything else (tool args, evidence, prompts, UI) uses rupees
as a Decimal with exactly two decimal places. Floats are rejected.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

TWO_PLACES = Decimal("0.01")


class MoneyError(ValueError):
    pass


def to_decimal(value: Decimal | str | int) -> Decimal:
    if isinstance(value, bool) or isinstance(value, float):
        raise MoneyError("Money must not be a float; pass a string like '450.50' or a Decimal.")
    try:
        d = value if isinstance(value, Decimal) else Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise MoneyError(f"Not a valid amount: {value!r}") from exc
    if not d.is_finite():
        raise MoneyError(f"Not a valid amount: {value!r}")
    if d.as_tuple().exponent < -2 and d != d.quantize(TWO_PLACES):
        raise MoneyError(f"Amount {value} has more than 2 decimal places.")
    return d.quantize(TWO_PLACES)


def to_paise(value: Decimal | str | int) -> int:
    return int(to_decimal(value) * 100)


def to_rupees(paise: int) -> Decimal:
    return (Decimal(int(paise)) / 100).quantize(TWO_PLACES)


def rupee_str(paise: int) -> str:
    """Plain 2-decimal string used in evidence and JSON, e.g. '6499.00'."""
    return f"{to_rupees(paise):.2f}"


def format_inr(value: Decimal | int, *, paise: bool = False) -> str:
    """Human display, e.g. '₹6,499.00'."""
    d = to_rupees(value) if paise else to_decimal(value)
    return f"₹{d:,.2f}"

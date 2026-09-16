from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def compact_decimal(value, *, max_places: int | None = None, grouping: bool = True) -> str:
    """Render a Decimal for people without changing the stored value.

    Insignificant trailing zeroes are removed, scientific notation is avoided,
    and optional rounding is explicit. This function is presentation-only.
    """
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    if not number.is_finite():
        return str(value)

    if max_places is not None:
        places = max(0, min(int(max_places), 12))
        number = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if number == 0:
        number = abs(number)

    rendered = format(number, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if not grouping:
        return rendered

    sign = ""
    if rendered.startswith("-"):
        sign, rendered = "-", rendered[1:]
    integer, separator, fraction = rendered.partition(".")
    grouped = f"{int(integer or '0'):,}"
    return f"{sign}{grouped}{separator}{fraction}" if separator else f"{sign}{grouped}"


def format_quantity(value) -> str:
    """Human-facing quantity: grouped, with up to six meaningful decimals."""
    return compact_decimal(value, max_places=6, grouping=True)

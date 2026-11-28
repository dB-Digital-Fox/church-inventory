"""Asset value and straight-line depreciation."""
from datetime import date, datetime

ACQUISITIONS = ("purchase", "donation", "other")


def parse_money(value):
    """'1 234,50 €' -> 1234.5; '' -> None; invalid -> raises ValueError."""
    text = (value or "").replace("€", "").replace(" ", "").replace(" ", "").replace(",", ".")
    if not text:
        return None
    number = float(text)
    if number < 0:
        raise ValueError("negative")
    return round(number, 2)


def _acquired(item):
    try:
        return datetime.strptime(item["purchase_date"], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def total_value(item):
    if item["unit_value"] is None:
        return None
    return round(item["unit_value"] * item["quantity"], 2)


def book_value(item, on=None):
    """Value on a given day after straight-line depreciation over useful_life_years."""
    total = total_value(item)
    if total is None:
        return None
    on = on or date.today()
    acquired = _acquired(item)
    life = item["useful_life_years"] or 0
    if acquired and on < acquired:
        return 0.0
    if not life or not acquired:
        return total
    elapsed = (on - acquired).days / 365.25
    return round(max(0.0, total * (1 - elapsed / life)), 2)


def depreciation_in_year(item, year):
    start, end = date(year, 1, 1), date(year, 12, 31)
    acquired = _acquired(item)
    if acquired and acquired > end:
        return 0.0
    begin_value = book_value(item, max(start, acquired) if acquired else start)
    end_value = book_value(item, end)
    if begin_value is None or end_value is None:
        return None
    return round(begin_value - end_value, 2)


def money(value):
    if value is None:
        return "—"
    return f"{value:,.2f} €".replace(",", " ").replace(".", ",")

from decimal import Decimal

from django import template

from internetservices.number_display import compact_decimal


register = template.Library()


@register.filter(name="quantity_display")
def quantity_display(value, max_places=6):
    """Display quantities without database-scale trailing zeroes."""
    try:
        places = int(max_places)
    except (TypeError, ValueError):
        places = 6
    return compact_decimal(value, max_places=places, grouping=True)


@register.filter(name="number_display")
def number_display(value, max_places=6):
    """Compact a numeric report cell; leave dates and text unchanged."""
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (Decimal, int, float)):
        return value
    return quantity_display(value, max_places)

"""Read-only serial feedback shared by previews and live entry."""
from .models import StockUnit


def existing_serials(*, organization, serials):
    return set(StockUnit.objects.filter(
        tenant=organization, serial_number__in={serial.strip().upper() for serial in serials},
    ).values_list('serial_number', flat=True))

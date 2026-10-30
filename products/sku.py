"""SKU syntax shared by validation, persistence and offline diagnostics."""
import re
from django.core.exceptions import ValidationError

GENERATED = re.compile(r'PRD-([0-9]+)\Z', re.ASCII)
MAX_NUMBER = 9223372036854775806


def generated_number(sku):
    match = GENERATED.fullmatch((sku or '').strip().upper())
    if not match:
        return None
    digits = match[1].lstrip('0') or '0'
    if len(digits) > 19 or int(digits) > MAX_NUMBER:
        raise ValidationError({'sku': 'Generated SKU suffix exceeds the supported integer range.'})
    return int(digits)


def validate_manual_sku(value, *, existing=''):
    value = (value or '').strip()
    if not value:
        return existing or ''
    if existing and value.upper() == existing.strip().upper():
        return existing
    value = value.upper()
    if len(value) > 64:
        raise ValidationError({'sku': 'SKU must be at most 64 characters.'})
    if GENERATED.fullmatch(value):
        raise ValidationError({'sku': 'PRD-<digits> is reserved for automatic allocation. Leave SKU blank.'})
    return value

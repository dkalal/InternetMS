"""Names entered through the catalog include their brand in one text field."""
import re


def name_with_brand(name, brand=''):
    name, brand = str(name or '').strip(), str(brand or '').strip()
    if not name or not brand or re.search(r'(?<!\w)' + re.escape(brand) + r'(?!\w)', name, re.IGNORECASE):
        return name
    return f'{brand} {name}'

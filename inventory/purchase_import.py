"""Purchase-workspace spreadsheet parsing and tenant-scoped batch validation.

This module never writes business records. The save boundary re-runs validation.
"""

import csv
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator
from django.db.models import Q
from django.db.models.functions import Lower, Upper
from openpyxl import load_workbook

from products.models import Product, ProductCategory, UnitOfMeasure

MAX_ROWS = 200
MAX_TEXT = 100_000
MAX_FILE = 250_000
HEADERS = (
    'sku', 'product_name', 'quantity', 'unit_cost', 'category', 'unit',
    'selling_price', 'brand', 'model_number', 'serialized', 'track_expiry',
    'batch_reference', 'expiry_date', 'serial_numbers', 'staging_reference',
)
LEGACY_HEADERS = ('sku', 'quantity', 'unit_cost', 'batch_reference', 'expiry_date', 'serial_numbers')
DECIMAL_FIELDS = {
    'quantity': (16, 6, True), 'unit_cost': (16, 6, False),
    'selling_price': (10, 2, True),
}
LENGTHS = {'sku': 64, 'product_name': 200, 'category': 120, 'unit': 50,
           'brand': 120, 'model_number': 120, 'batch_reference': 100,
           'serial_numbers': 10000, 'staging_reference': 64}


class ImportInputError(ValueError):
    pass


def _as_text(value):
    if value is None:
        return ''
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return str(value).strip()


def parse_input(*, text='', upload=None):
    if upload:
        if upload.size > MAX_FILE:
            raise ImportInputError('XLSX file exceeds 250 KB.')
        if not upload.name.lower().endswith('.xlsx'):
            raise ImportInputError('Upload an .xlsx template. Macro-enabled files are not accepted.')
        try:
            book = load_workbook(BytesIO(upload.read()), read_only=True, data_only=False)
            if 'Purchase rows' not in book.sheetnames:
                raise ImportInputError('The workbook needs a Purchase rows sheet.')
            sheet = book['Purchase rows']
            if sheet.max_column > len(HEADERS) or sheet.max_row > MAX_ROWS + 10:
                raise ImportInputError('The workbook exceeds the supported column or row limit.')
            raw = []
            for cells in sheet.iter_rows(values_only=True):
                row = [_as_text(cell) for cell in cells]
                if any(row):
                    raw.append(row)
            book.close()
        except ImportInputError:
            raise
        except Exception as exc:
            raise ImportInputError('The XLSX file could not be read.') from exc
    else:
        if len(text) > MAX_TEXT:
            raise ImportInputError('Pasted text exceeds 100,000 characters.')
        try:
            raw = [row for row in csv.reader(StringIO(text), delimiter='\t', strict=True)
                   if any(cell.strip() for cell in row)]
        except csv.Error as exc:
            raise ImportInputError('Check spreadsheet quoting in the pasted rows.') from exc
    if not raw:
        raise ImportInputError('Enter at least one purchase row.')
    first = tuple(cell.strip().lower().replace(' ', '_') for cell in raw[0])
    if first == HEADERS:
        keys = HEADERS
        raw = raw[1:]
    elif first in (LEGACY_HEADERS,
                   ('sku', 'quantity', 'unit_cost', 'batch_reference', 'expiry', 'serial_numbers'),
                   ('sku', 'quantity', 'unit_cost', 'batch', 'expiry', 'serials')):
        keys = LEGACY_HEADERS
        raw = raw[1:]
    elif len(raw[0]) <= 6:
        keys = LEGACY_HEADERS
    else:
        raise ImportInputError('Use the version 2 header for mixed products or six legacy columns.')
    if not raw or len(raw) > MAX_ROWS:
        raise ImportInputError(f'Enter between 1 and {MAX_ROWS} non-empty purchase rows.')
    result = []
    for index, cells in enumerate(raw, 1):
        if len(cells) == 1 and keys == LEGACY_HEADERS:
            # Space input remains supported only for simple SKU, quantity, cost.
            parts = cells[0].split()
            if 2 <= len(parts) <= 6:
                cells = parts
            elif len(parts) > 6:
                cells = parts
        row = {key: _as_text(cells[pos]) if pos < len(cells) else '' for pos, key in enumerate(keys)}
        row['row'] = index
        row['input_errors'] = []
        if len(cells) > len(keys):
            row['input_errors'].append('Use no more than six columns.' if keys == LEGACY_HEADERS
                                       else f'Use no more than {len(keys)} columns.')
        for key, max_length in LENGTHS.items():
            if len(row.get(key, '')) > max_length:
                row['input_errors'].append(f'{key.replace("_", " ").title()} is too long.')
        if any(value.startswith('=') for key, value in row.items()
               if key not in ('row', 'input_errors') and isinstance(value, str)):
            row['input_errors'].append('Formula cells are not accepted. Paste values as text.')
        result.append(row)
    return result


def _decimal(value, key, errors):
    digits, places, positive = DECIMAL_FIELDS[key]
    label = key.replace('_', ' ').capitalize()
    if not value:
        errors.append(f'{label} is required.')
        return None
    try:
        number = Decimal(value)
        if not number.is_finite() or number < 0 or (positive and number <= 0):
            raise InvalidOperation
        DecimalValidator(digits, places)(number)
        return number
    except (InvalidOperation, ValueError, ValidationError):
        errors.append(f'{label} has an invalid value or precision.')
        return None


def _flag(value, label, errors):
    normalized = str(value).strip().lower()
    if normalized in ('', 'no', 'false', '0'):
        return False
    if normalized in ('yes', 'true', '1'):
        return True
    errors.append(f'{label} must be Yes or No.')
    return False


def validate_rows(rows, *, organization, for_save=False):
    """Return display rows; every catalog lookup is scoped to the active tenant."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ROWS:
        raise ImportInputError('The batch must contain 1 to 200 rows.')
    sku_set = {str(r.get('sku', '')).strip().upper() for r in rows if r.get('sku')}
    ids = {int(str(r['product_id'])) for r in rows
           if str(r.get('product_id', '')).isdigit() and len(str(r['product_id'])) <= 18}
    catalog = list(Product.objects.filter(tenant=organization).annotate(normalized_sku=Upper('sku')).filter(
        Q(normalized_sku__in=sku_set) | Q(pk__in=ids)
    ).select_related('sales_unit'))
    by_sku = {p.sku.upper(): p for p in catalog}
    by_id = {p.pk: p for p in catalog}
    candidate_names = {str(r.get('product_name', '')).strip().casefold() for r in rows
                       if r.get('product_name') and not r.get('sku')}
    suggestions_by_name = {}
    if candidate_names:
        suggestions = Product.objects.filter(
            tenant=organization, is_active=True, item_type=Product.ItemType.PHYSICAL, track_stock=True
        ).annotate(normalized_name=Lower('name')).filter(
            normalized_name__in=candidate_names
        ).select_related('sales_unit').order_by('name', 'pk')[:600]
        for candidate in suggestions:
            matches = suggestions_by_name.setdefault(candidate.name.casefold(), [])
            if len(matches) < 10:
                matches.append(candidate)
    categories = {c.name.casefold(): c for c in ProductCategory.objects.filter(tenant=organization, is_active=True).prefetch_related('allowed_units')}
    allowed_units = {c.pk: {unit.pk for unit in c.allowed_units.all()} for c in categories.values()}
    units = {u.name.casefold(): u for u in UnitOfMeasure.objects.filter(tenant=organization, is_active=True)}
    seen_new_sku = {}
    definitions = {}
    seen_serials = set()
    results = []
    for number, supplied in enumerate(rows, 1):
        row = {key: _as_text(supplied.get(key, '')) for key in HEADERS}
        row['row'] = number
        errors = list(supplied.get('input_errors', []))
        for key, length in LENGTHS.items():
            if len(row[key]) > length:
                errors.append(f'{key.replace("_", " ").title()} is too long.')
        if any(row[key].startswith('=') for key in HEADERS):
            errors.append('Formula cells are not accepted.')
        row['sku'] = row['sku'].upper()
        excluded = str(supplied.get('excluded', '')).lower() in ('true', '1', 'yes')
        resolution = str(supplied.get('resolution', '')).strip()
        row['resolution'] = resolution
        selected_id = str(supplied.get('product_id', '')).strip()
        known = by_sku.get(row['sku']) if row['sku'] else None
        selected = by_id.get(int(selected_id)) if selected_id.isdigit() and len(selected_id) <= 18 else None
        if selected and supplied.get('expected_sku') and selected.sku.upper() != str(supplied['expected_sku']).upper():
            errors.append('The selected catalog SKU changed after preview. Search and select the product again.')
        if selected and supplied.get('expected_serialized') is not None and str(supplied['expected_serialized']).lower() in ('true', 'false'):
            if selected.is_serialized != (str(supplied['expected_serialized']).lower() == 'true'):
                errors.append('The selected product tracking settings changed after preview.')
        if selected and supplied.get('expected_expiry') is not None and str(supplied['expected_expiry']).lower() in ('true', 'false'):
            if selected.track_expiry != (str(supplied['expected_expiry']).lower() == 'true'):
                errors.append('The selected product expiry setting changed after preview.')
        if selected and supplied.get('expected_unit_id'):
            expected_unit = str(supplied['expected_unit_id'])
            if not expected_unit.isdigit() or selected.sales_unit_id != int(expected_unit):
                errors.append('The selected product unit changed after preview.')
        product = None
        status = 'unresolved'
        if excluded:
            status = 'excluded'
        elif known and not (known.is_active and known.item_type == Product.ItemType.PHYSICAL and known.track_stock):
            errors.append('SKU belongs to an inactive, service, or non-stock product.')
        elif known and resolution != 'new':
            if selected_id and selected_id != str(known.pk):
                errors.append('Selected product conflicts with the supplied SKU.')
            else:
                product = known
        elif resolution == 'existing':
            if not selected or not (selected.is_active and selected.item_type == Product.ItemType.PHYSICAL and selected.track_stock):
                errors.append('Choose an eligible active stock product from this catalog.')
            else:
                product = selected
        elif resolution == 'new':
            status = 'new'
            if not row['product_name']:
                errors.append('Product name is required for a new product.')
            category = categories.get(row['category'].casefold())
            unit = units.get(row['unit'].casefold())
            if not category:
                errors.append('Choose an active category in this business.')
            if not unit:
                errors.append('Choose an active unit in this business.')
            if category and unit and unit.pk not in allowed_units[category.pk]:
                errors.append('The unit is not allowed for this category.')
            row['selling_price_value'] = _decimal(row['selling_price'], 'selling_price', errors)
            row['serialized_value'] = _flag(row['serialized'], 'Serialized', errors)
            row['track_expiry_value'] = _flag(row['track_expiry'], 'Track expiry', errors)
            if row['sku']:
                if known:
                    errors.append('This SKU already belongs to a catalog product.')
                prior = seen_new_sku.setdefault(row['sku'], number)
                if prior != number and row['staging_reference'] != results[prior - 1].get('staging_reference'):
                    errors.append(f'New-product SKU is already proposed on row {prior}.')
            reference = row['staging_reference'] or f'row-{number}'
            definition = tuple(row.get(k) for k in ('sku', 'product_name', 'category', 'unit', 'selling_price', 'brand', 'model_number', 'serialized', 'track_expiry'))
            if reference in definitions and definitions[reference] != definition:
                errors.append('Rows sharing a staging reference have conflicting product definitions.')
            definitions[reference] = definition
            row['staging_reference'] = reference
            row['category_id'] = category.pk if category else None
            row['unit_id'] = unit.pk if unit else None
        else:
            if row['sku'] and not known:
                errors.append('No active stock product matches this SKU. Choose an existing product or Create new.')
            elif not row['sku']:
                errors.append('SKU is missing. Choose an existing product or Create new.')
        if product:
            status = 'matched'
            row['product'] = {'id': product.pk, 'name': product.name, 'sku': product.sku,
                              'text': str(product), 'unit': product.sales_unit.label if product.sales_unit_id else product.measure_unit,
                              'serialized': product.is_serialized, 'expiry': product.track_expiry}
            row['product_id'] = product.pk
            row['expected_sku'] = product.sku
            row['expected_serialized'] = product.is_serialized
            row['expected_expiry'] = product.track_expiry
            row['expected_unit_id'] = product.sales_unit_id
        else:
            row['product'] = None
            row['product_id'] = selected.pk if selected else None
        row['suggestions'] = [
            {'id': candidate.pk, 'sku': candidate.sku, 'name': candidate.name,
             'unit': candidate.sales_unit.label if candidate.sales_unit_id else candidate.measure_unit,
             'unit_id': candidate.sales_unit_id, 'serialized': candidate.is_serialized,
             'expiry': candidate.track_expiry}
            for candidate in suggestions_by_name.get(row['product_name'].casefold(), [])
            if (not row['brand'] or candidate.brand.casefold() == row['brand'].casefold())
            and (not row['model_number'] or candidate.model_number.casefold() == row['model_number'].casefold())
            and (not row['unit'] or candidate.sales_unit and candidate.sales_unit.name.casefold() == row['unit'].casefold())
        ][:3]
        quantity = _decimal(row['quantity'], 'quantity', errors)
        cost = _decimal(row['unit_cost'], 'unit_cost', errors)
        if row['expiry_date']:
            try:
                date.fromisoformat(row['expiry_date'])
            except ValueError:
                errors.append('Expiry date must use YYYY-MM-DD.')
        serials = [s.strip() for s in row['serial_numbers'].replace('|', ',').replace('\n', ',').split(',') if s.strip()]
        for serial in serials:
            normalized = serial.upper()
            if normalized in seen_serials:
                errors.append(f'Serial {serial} occurs in another row.')
            seen_serials.add(normalized)
        serialized = product.is_serialized if product else row.get('serialized_value', False)
        if serialized and quantity is not None and (quantity != quantity.to_integral_value() or len(serials) != int(quantity)):
            errors.append('Serialized products need one unique serial per whole unit.')
        if (product or status == 'new') and not serialized and serials:
            errors.append('Quantity-based products must not have serial numbers.')
        expiry_tracked = product.track_expiry if product else row.get('track_expiry_value', False)
        if (product or status == 'new') and expiry_tracked and not row['expiry_date']:
            errors.append('An expiry date is required for this product.')
        if not excluded and status == 'unresolved' and not errors:
            errors.append('Choose an existing product or Create new.') if for_save else None
        row['serial_numbers'] = '\n'.join(serials)
        row['quantity'] = str(quantity) if quantity is not None else row['quantity']
        row['unit_cost'] = str(cost) if cost is not None else row['unit_cost']
        row['excluded'] = excluded
        row['status'] = 'excluded' if excluded else ('unresolved' if status == 'unresolved' and len(errors) == 1
                                                    else 'invalid' if errors else status)
        row['valid'] = excluded or (not errors and status in ('matched', 'new'))
        row['errors'] = [] if excluded else errors
        results.append(row)
    return results


def submitted_rows(value):
    if not value:
        return []
    if len(value) > MAX_TEXT:
        raise ImportInputError('Applied batch exceeds 100,000 characters.')
    try:
        rows = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ImportInputError('Applied batch data is invalid. Review the import again.') from exc
    if not isinstance(rows, list):
        raise ImportInputError('Applied batch data is invalid.')
    if not all(isinstance(row, dict) for row in rows):
        raise ImportInputError('Applied batch rows are invalid.')
    return rows

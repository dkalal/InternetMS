"""Explicit versioned commercial purchase import; legacy templates retain same-unit semantics."""
import hashlib
import json
from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import UUID
from django.core.exceptions import PermissionDenied
from django.core.validators import DecimalValidator
from django.db import transaction
from openpyxl import load_workbook
from products import packaging as policy
from products.models import Product
from users.permissions import PermissionCode, has_tenant_permission
from .models import Purchase, PurchaseLine, PurchaseImportSubmission, Supplier
from .numbering import PurchaseReferenceNumberService
from .packaging_receipts import purchase_snapshot
from .services import audit, money

HEADERS = ('sku', 'packaging_id', 'configuration_version', 'quantity', 'unit_cost', 'batch_reference', 'expiry_date')


def parse_commercial_workbook(upload):
    if not upload or upload.size > 250000 or not upload.name.lower().endswith('.xlsx'):
        raise policy.PackagingError('Upload a commercial purchase .xlsx template of at most 250 KB.')
    book = None
    try:
        book = load_workbook(BytesIO(upload.read()), read_only=True, data_only=False)
        sheet = book['Commercial purchase v1']
        if sheet.max_column != len(HEADERS) or sheet.max_row > 10000:
            raise policy.PackagingError('Use exactly the seven template columns and at most 10,000 source rows.')
        source = sheet.iter_rows(values_only=True)
        if tuple(str(value or '').strip() for value in next(source)) != HEADERS:
            raise policy.PackagingError('Use the Commercial purchase v1 column headings unchanged.')
        rows = []
        for number, cells in enumerate(source, 2):
            values = [str(value).strip() if value is not None else '' for value in cells]
            if not any(values):
                continue
            if len(rows) >= 200 or any(value.startswith('=') or len(value) > 200 for value in values):
                raise policy.PackagingError('Import at most 200 rows; formulas and oversized cells are not accepted.')
            rows.append(dict(zip(HEADERS, values)))
        if not rows:
            raise policy.PackagingError('Enter at least one commercial purchase row.')
        return rows
    except policy.PackagingError:
        raise
    except Exception as exc:
        raise policy.PackagingError('The workbook could not be read. Use the commercial purchase template.') from exc
    finally:
        if book is not None:
            book.close()


@transaction.atomic
def import_commercial_purchase(*, organization, actor, supplier_id, purchase_date, rows, request_key, support_session=None):
    membership = policy.authorize(organization=organization, actor=actor, support_session=support_session)
    if not has_tenant_permission(actor, organization, PermissionCode.PURCHASES_CREATE, membership=membership):
        raise PermissionDenied('Commercial purchase creation is unavailable.')
    if not policy.workflows_ready():
        raise policy.PackagingError('Commercial import is unavailable until all release gates pass.')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 200 or any(not isinstance(row, dict) or set(row) != set(HEADERS) for row in rows):
        raise policy.PackagingError('Supply 1 to 200 rows in the commercial purchase v1 format.')
    try:
        key = UUID(str(request_key))
        fingerprint = hashlib.sha256(json.dumps({'format': 'commercial-purchase-v1', 'supplier': str(supplier_id),
            'date': purchase_date.isoformat(), 'rows': rows}, sort_keys=True).encode()).hexdigest()
    except (ValueError, TypeError, AttributeError):
        raise policy.PackagingError('Supply a valid date, row values and retry key.')
    type(organization).objects.select_for_update(no_key=True).get(pk=organization.pk)
    previous = PurchaseImportSubmission.objects.filter(tenant=organization, token=key).first()
    if previous is not None:
        if previous.fingerprint != fingerprint:
            raise policy.PackagingError('This retry key was used for different purchase content.')
        return previous.purchase
    supplier = Supplier.objects.filter(tenant=organization, organization=organization, pk=policy.identifier(supplier_id), is_active=True).first()
    if supplier is None:
        raise policy.PackagingError('Choose an active supplier in this tenant.')
    skus = [row['sku'] for row in rows]
    products = {p.sku: p for p in Product.objects.unscoped().select_for_update().filter(
        tenant=organization, organization=organization, sku__in=skus, is_active=True).order_by('pk')}
    prepared = []
    containers = 0
    for number, row in enumerate(rows, 1):
        product = products.get(row['sku'])
        if product is None or not product.packaging_enabled:
            raise policy.PackagingError(f'Row {number}: choose an existing activated product SKU; this import never creates products.')
        values = purchase_snapshot(organization=organization, product=product, packaging_id=row['packaging_id'] or None,
            quantity=row['quantity'], unit_cost=row['unit_cost'], version=row['configuration_version'])
        containers += int(values['entered_quantity']) if values['packaging'] else 1
        if containers > 2000:
            raise policy.PackagingError('Split this purchase into batches of at most 2,000 physical containers.')
        try:
            expiry = date.fromisoformat(row['expiry_date']) if row['expiry_date'] else None
        except (TypeError, ValueError):
            raise policy.PackagingError(f'Row {number}: use YYYY-MM-DD expiry dates.')
        if product.track_expiry and expiry is None:
            raise policy.PackagingError(f'Row {number}: an expiry date is required.')
        if len(row['batch_reference']) > 100:
            raise policy.PackagingError(f'Row {number}: batch reference is too long.')
        prepared.append((product, values, expiry, row['batch_reference']))
    total = money(sum((values['commercial_total'] for _, values, _, _ in prepared), Decimal('0')))
    DecimalValidator(14, 2)(total)
    purchase = Purchase.objects.create(tenant=organization, organization=organization, supplier=supplier,
        purchase_date=purchase_date, reference_number=PurchaseReferenceNumberService.next_number(
            organization=organization, purchase_date=purchase_date), created_by=actor, total_cost=total)
    for product, values, expiry, batch in prepared:
        PurchaseLine.objects.create(tenant=organization, purchase=purchase, product=product, expiry_date=expiry,
            batch_reference=batch, **values)
    PurchaseImportSubmission.objects.create(tenant=organization, organization=organization, token=key,
        fingerprint=fingerprint, purchase=purchase)
    audit(organization=organization, actor=actor, action='inventory.purchase.commercial_imported', obj=purchase,
        new_value={'line_count': len(prepared), 'commercial_total': str(total)},
        metadata={'request_key': str(key), 'stock_changed': False, 'format': 'commercial-purchase-v1'})
    return purchase

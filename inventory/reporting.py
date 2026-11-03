"""Read-only, tenant-scoped inventory report calculations."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from django import forms
from django.db.models import F, Sum
from django.utils import timezone
from products.models import Product
from .models import (HistoricalInventoryRecord, InventoryBalance, InventorySaleLine,
                     InventorySettings, Purchase, PurchaseLine, StockMovement, StockUnit, Supplier)
from .services import money
from internetservices.tailwind import apply_tailwind

DATED_REPORTS = {'purchases', 'sales', 'gross-profit', 'movements', 'historical-purchases', 'historical-sales'}


class ReportFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=200, label='Search report')
    result = forms.ChoiceField(required=False, choices=[('', 'All results'), ('loss', 'Loss-making lines'), ('profit', 'Profitable lines'), ('break-even', 'Break-even lines')], label='Profit result')
    date_from = forms.DateField(required=False, input_formats=['%Y-%m-%d'], widget=forms.DateInput(attrs={'type': 'date'}), label='From date')
    date_to = forms.DateField(required=False, input_formats=['%Y-%m-%d'], widget=forms.DateInput(attrs={'type': 'date'}), label='To date')
    supplier = forms.ModelChoiceField(required=False, queryset=Supplier.objects.none(), empty_label='All suppliers')

    def __init__(self, *args, organization, report_name, **kwargs):
        super().__init__(*args, **kwargs)
        if report_name != 'purchases':
            del self.fields['supplier']
        else:
            # Include inactive suppliers: deactivation never hides historical receipts.
            self.fields['supplier'].queryset = Supplier.objects.filter(tenant=organization)
        if report_name not in DATED_REPORTS:
            del self.fields['date_from']
            del self.fields['date_to']
        if report_name != 'gross-profit':
            del self.fields['result']
        self.fields['q'].widget.attrs['placeholder'] = 'Name, SKU or reference'
        apply_tailwind(self)

    def clean(self):
        data = super().clean()
        if data.get('date_from') and data.get('date_to') and data['date_from'] > data['date_to']:
            self.add_error('date_to', 'To date must be on or after From date.')
        return data


def date_filter(queryset, field, filters):
    if filters.get('date_from'):
        queryset = queryset.filter(**{field + '__gte': filters['date_from']})
    if filters.get('date_to'):
        queryset = queryset.filter(**{field + '__lte': filters['date_to']})
    return queryset


def report_rows(organization, report_name, filters=None):
    filters = filters or {}
    settings_obj = InventorySettings.objects.filter(tenant=organization).first() or InventorySettings(organization=organization, tenant=organization)
    if report_name in {'stock-valuation', 'low-stock', 'out-of-stock', 'dead-stock'}:
        balances = InventoryBalance.objects.filter(tenant=organization, product__tenant=organization).select_related('product')
        if report_name == 'low-stock':
            balances = balances.filter(quantity__gt=0, quantity__lte=F('product__reorder_threshold'))
        elif report_name == 'out-of-stock':
            balances = balances.filter(quantity=0)
        elif report_name == 'dead-stock':
            cutoff = timezone.now() - timedelta(days=settings_obj.dead_stock_days)
            sold = StockMovement.objects.filter(tenant=organization, product__tenant=organization, movement_type='sale_out', created_at__gte=cutoff).values('product_id')
            balances = balances.filter(quantity__gt=0).exclude(product_id__in=sold)
        if report_name == 'stock-valuation':
            balances = balances.annotate(report_value=F('quantity') * F('average_cost')).order_by('-report_value', 'product__sku')
            return ['SKU', 'Product', 'Quantity', 'Average cost', 'Value', 'Threshold'], [
                [b.product.sku, b.product.name, b.quantity, b.average_cost, money(b.quantity * b.average_cost), b.product.reorder_threshold] for b in balances
            ]
        rows = [[b.product.sku, b.product.name, b.quantity, b.product.reorder_threshold] for b in balances]
        if report_name == 'out-of-stock':
            missing = Product.objects.filter(tenant=organization, track_stock=True, item_type=Product.ItemType.PHYSICAL, inventory_balance__isnull=True).order_by('sku')
            rows.extend([p.sku, p.name, Decimal('0'), p.reorder_threshold] for p in missing)
        return ['SKU', 'Product', 'Quantity', 'Threshold'], rows
    if report_name == 'purchases':
        lines = PurchaseLine.objects.filter(tenant=organization, product__tenant=organization, purchase__supplier__tenant=organization, purchase__tenant=organization, purchase__status=Purchase.Status.CONFIRMED).select_related('purchase__supplier', 'product')
        lines = date_filter(lines, 'purchase__purchase_date', filters)
        if filters.get('supplier'):
            lines = lines.filter(purchase__supplier=filters['supplier'])
        return ['Date', 'Reference', 'Supplier', 'SKU', 'Product', 'Quantity', 'Unit cost', 'Total'], [
            [l.purchase.purchase_date, l.purchase.reference_number, l.purchase.supplier.company_name, l.product.sku, l.product.name, l.quantity, l.unit_cost, l.line_total] for l in lines
        ]
    if report_name in {'sales', 'gross-profit'}:
        lines = InventorySaleLine.objects.filter(tenant=organization, billing_line__tenant=organization, sale__invoice__tenant=organization, sale__tenant=organization, sale__stock_deducted=True, sale__completed_at__isnull=False).select_related('sale__invoice', 'billing_line__product')
        lines = date_filter(lines, 'sale__completed_at__date', filters)
        if report_name == 'sales':
            return ['Date', 'Invoice', 'SKU', 'Item', 'Quantity', 'Net revenue', 'Currency'], [
                [timezone.localdate(l.sale.completed_at), l.sale.invoice.number, l.billing_line.product.sku if l.billing_line.product_id else '', l.billing_line.description or (l.billing_line.product.name if l.billing_line.product_id else ''), l.billing_line.quantity, l.net_revenue, l.sale.invoice.currency]
                for l in lines
            ]
        return ['Date', 'Invoice', 'SKU', 'Item', 'Quantity', 'Net revenue', 'Cost (TZS)', 'Gross profit (TZS)', 'Result', 'Revenue currency'], [
            [timezone.localdate(l.sale.completed_at), l.sale.invoice.number, l.billing_line.product.sku if l.billing_line.product_id else '', l.billing_line.description or (l.billing_line.product.name if l.billing_line.product_id else ''), l.billing_line.quantity, l.net_revenue, l.cost_total, l.gross_profit if l.sale.invoice.currency == 'TZS' else None, profit_result(l.gross_profit) if l.sale.invoice.currency == 'TZS' else 'FX required', l.sale.invoice.currency]
            for l in lines
        ]
    if report_name == 'movements':
        movements = StockMovement.objects.filter(tenant=organization, product__tenant=organization).select_related('product')
        movements = date_filter(movements, 'created_at__date', filters)
        return ['Date', 'SKU', 'Product', 'Type', 'Quantity', 'Balance after', 'Unit cost'], [[timezone.localtime(m.created_at), m.product.sku, m.product.name, m.get_movement_type_display(), m.quantity, m.balance_after, m.unit_cost] for m in movements]
    if report_name == 'serialized':
        units = StockUnit.objects.filter(tenant=organization, product__tenant=organization).select_related('product', 'sold_billing_line__document')
        return ['SKU', 'Product', 'Serial', 'Status', 'Batch', 'Expiry', 'Invoice'], [[u.product.sku, u.product.name, u.serial_number, u.get_status_display(), u.batch_reference, u.expiry_date, u.sold_billing_line.document.number if u.sold_billing_line_id else ''] for u in units]
    if report_name in {'historical-purchases', 'historical-sales'}:
        record_type = HistoricalInventoryRecord.RecordType.PURCHASE if report_name == 'historical-purchases' else HistoricalInventoryRecord.RecordType.SALE
        records = HistoricalInventoryRecord.objects.filter(tenant=organization, record_type=record_type)
        records = date_filter(records, 'record_date', filters)
        return ['Date', 'Reference', 'SKU', 'Description', 'Quantity', 'Unit amount', 'Total', 'Notes'], [
            [r.record_date, r.reference, r.sku, r.description, r.quantity, r.unit_amount, r.total_amount, r.notes] for r in records
        ]
    days = settings_obj.fast_moving_days if report_name in {'best-selling', 'fast-moving'} else settings_obj.slow_moving_days
    cutoff = timezone.now() - timedelta(days=days)
    totals = (
        StockMovement.objects.filter(tenant=organization, product__tenant=organization, movement_type='sale_out', created_at__gte=cutoff)
        .values('product__sku', 'product__name').annotate(raw_units=Sum('quantity')).order_by('raw_units', 'product__sku')
    )
    rows = [[row['product__sku'], row['product__name'], -row['raw_units'], days] for row in totals]
    if report_name == 'fast-moving':
        rows = [row for row in rows if row[2] >= settings_obj.fast_moving_min_units]
    elif report_name == 'slow-moving':
        sold_ids = StockMovement.objects.filter(tenant=organization, product__tenant=organization, movement_type='sale_out', created_at__gte=cutoff).values('product_id')
        unsold = InventoryBalance.objects.filter(tenant=organization, quantity__gt=0).exclude(product_id__in=sold_ids).select_related('product')
        rows = [row for row in rows if row[2] <= settings_obj.slow_moving_max_units]
        rows.extend([b.product.sku, b.product.name, Decimal('0'), days] for b in unsold)
        rows.sort(key=lambda row: (row[2], row[0]))
    return ['SKU', 'Product', 'Units sold', 'Period days'], rows



def report_totals(report_name, rows):
    """Return (label, column, amount); never add money in different currencies."""
    columns = {
        'stock-valuation': [('Total stock value (TZS)', 4)],
        'purchases': [('Total purchases (TZS)', 7)],
        'historical-purchases': [('Historical purchase total (TZS)', 6)],
        'historical-sales': [('Historical sales total (TZS)', 6)],
    }
    totals = [(label, column, sum((row[column] for row in rows), Decimal('0.00')))
              for label, column in columns.get(report_name, [])]
    if report_name in {'sales', 'gross-profit'}:
        for currency in sorted({row[-1] for row in rows}):
            totals.append((f'Net revenue ({currency})', 5,
                           sum((row[5] for row in rows if row[-1] == currency), Decimal('0.00'))))
        if report_name == 'gross-profit':
            totals.append(('Stock cost (TZS)', 6, sum((row[6] for row in rows), Decimal('0.00'))))
            totals.append(('Gross profit on TZS sales (TZS)', 7,
                           sum((row[7] for row in rows if row[7] is not None), Decimal('0.00'))))
    return totals


SEARCH_COLUMNS = {
    'stock-valuation': (0, 1), 'low-stock': (0, 1), 'out-of-stock': (0, 1), 'dead-stock': (0, 1),
    'purchases': (1, 2, 3, 4), 'sales': (1, 2, 3), 'gross-profit': (1, 2, 3),
    'movements': (1, 2, 3), 'serialized': (0, 1, 2, 3, 4, 6),
    'historical-purchases': (1, 2, 3, 7), 'historical-sales': (1, 2, 3, 7),
    'best-selling': (0, 1), 'fast-moving': (0, 1), 'slow-moving': (0, 1),
}


def profit_result(amount):
    return 'Loss' if amount < 0 else 'Profit' if amount > 0 else 'Break-even'


def filter_report_rows(report_name, rows, filters):
    search = filters.get('q', '').casefold()
    if search:
        columns = SEARCH_COLUMNS[report_name]
        rows = [row for row in rows if any(search in str(row[i] if row[i] is not None else '').casefold() for i in columns)]
    result = filters.get('result')
    if report_name == 'gross-profit' and result:
        expected = {'loss': 'Loss', 'profit': 'Profit', 'break-even': 'Break-even'}[result]
        rows = [row for row in rows if row[8] == expected]
    return rows


def valuation_bridge(organization, rows):
    """Explain the selected balances using signed immutable movement values.

    A nonzero residual is disclosed, never silently written into stock history.
    It can arise from historical costing/rounding or unbacked balances.
    """
    movements = StockMovement.objects.filter(
        tenant=organization, product__tenant=organization, product__sku__in=[row[0] for row in rows],
    ).values('movement_type').annotate(value=Sum(F('quantity') * F('unit_cost')))
    amounts = {row['movement_type']: row['value'] for row in movements}
    labels = [('opening', 'Opening stock'), ('purchase_in', 'Confirmed stock receipts'),
              ('adjustment_in', 'Positive adjustments'), ('adjustment_out', 'Negative adjustments'),
              ('sale_out', 'Stock cost removed by sales')]
    entries = [(label, money(amounts.get(kind, 0))) for kind, label in labels]
    current = sum((row[4] for row in rows), Decimal('0.00'))
    residual = current - sum((value for label, value in entries), Decimal('0.00'))
    entries.append(('Recorded valuation difference', residual))
    return {'entries': entries, 'total': current, 'difference': residual}


def report_display(headers, rows):
    """Presentation metadata, keeping canonical Decimal/date values for exports."""
    numeric = [header in {'Quantity', 'Average cost', 'Value', 'Threshold', 'Unit cost', 'Total',
                         'Net revenue', 'Cost (TZS)', 'Gross profit (TZS)', 'Balance after',
                         'Unit amount', 'Units sold', 'Period days'} for header in headers]
    columns = [{'label': label, 'numeric': numeric[index]} for index, label in enumerate(headers)]
    records = []
    for row in rows:
        cells = [{'value': value.strftime('%Y-%m-%d %H:%M') if isinstance(value, datetime) else value.isoformat() if isinstance(value, date) else value, 'numeric': numeric[index],
                  'loss': headers[index] == 'Gross profit (TZS)' and value is not None and value < 0,
                  'status': headers[index] == 'Result'} for index, value in enumerate(row)]
        records.append(cells)
    return columns, records

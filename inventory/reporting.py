"""Read-only, tenant-scoped inventory report calculations."""
from datetime import timedelta
from decimal import Decimal
from django import forms
from django.db.models import F, Sum
from django.utils import timezone
from products.models import Product
from .models import (HistoricalInventoryRecord, InventoryBalance, InventorySaleLine,
                     InventorySettings, Purchase, PurchaseLine, StockMovement, StockUnit, Supplier)
from .services import money

DATED_REPORTS = {'purchases', 'sales', 'gross-profit', 'movements', 'historical-purchases', 'historical-sales'}


class ReportFilterForm(forms.Form):
    q = forms.CharField(required=False, max_length=200, label='Search rows')
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
        for field in self.fields.values():
            field.widget.attrs['class'] = 'mt-1 min-h-10 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm'

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
        return ['Date', 'Invoice', 'SKU', 'Item', 'Quantity', 'Net revenue', 'Cost (TZS)', 'Gross profit (TZS)', 'Revenue currency'], [
            [timezone.localdate(l.sale.completed_at), l.sale.invoice.number, l.billing_line.product.sku if l.billing_line.product_id else '', l.billing_line.description or (l.billing_line.product.name if l.billing_line.product_id else ''), l.billing_line.quantity, l.net_revenue, l.cost_total, l.gross_profit if l.sale.invoice.currency == 'TZS' else None, l.sale.invoice.currency]
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

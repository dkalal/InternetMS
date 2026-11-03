from django.db import migrations, models
from django.db.models import Exists, OuterRef, Subquery


def preserve_existing_walk_in_sales(apps, schema_editor):
    Cart = apps.get_model('inventory', 'Cart')
    Document = apps.get_model('billing', 'BillingDocument')
    db = schema_editor.connection.alias

    # Only a cart with no selected customer establishes walk-in provenance.
    # Keep issued financial amounts untouched; freeze identity separately.
    carts = Cart.objects.using(db).filter(customer__isnull=True).exclude(
        quotation__isnull=True, invoice__isnull=True,
    ).values_list('tenant_id', 'quotation_id', 'invoice_id', 'walk_in_name')
    for tenant_id, quote_id, invoice_id, name in carts.iterator(chunk_size=500):
        for document_id in (quote_id, invoice_id):
            if document_id:
                Document.objects.using(db).filter(pk=document_id, tenant_id=tenant_id).update(
                    is_walk_in_sale=True, walk_in_name_snapshot=name.strip() or 'Walk-in Customer',
                )

    # Quotation versions, converted invoices, reissues and receipts inherit
    # their source's frozen identity. Each pass marks at least one new row.
    while True:
        changed = 0
        for source_field in ('parent_quotation', 'source_quotation', 'original_invoice', 'invoice', 'corrected_invoice'):
            source = Document.objects.using(db).filter(
                pk=OuterRef(f'{source_field}_id'), tenant_id=OuterRef('tenant_id'),
                customer_id=OuterRef('customer_id'),
                is_walk_in_sale=True,
            )
            changed += Document.objects.using(db).filter(is_walk_in_sale=False).annotate(
                source_is_walk_in=Exists(source),
            ).filter(source_is_walk_in=True).update(
                is_walk_in_sale=True,
                walk_in_name_snapshot=Subquery(source.values('walk_in_name_snapshot')[:1]),
            )
        if not changed:
            break


class Migration(migrations.Migration):
    dependencies = [
        ('billing', '0031_reconcile_subscription_paid_through'),
        ('customers', '0023_customer_pos_placeholder'),
        ('inventory', '0011_purchaseline_authoritative_purchase_total_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='billingdocument', name='is_walk_in_sale',
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name='billingdocument', name='walk_in_name_snapshot',
            field=models.CharField(blank=True, default='', editable=False, max_length=200),
        ),
        migrations.RunPython(preserve_existing_walk_in_sales, migrations.RunPython.noop),
    ]

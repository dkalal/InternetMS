"""Conservative repair for unused products created by the old draft importer."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.models import AuditLog
from custom_fields.models import CustomFieldValue
from inventory.models import Purchase
from inventory.purchase_batch import apply_purchase_batch
from products.models import Product
from users.models import Organization
from users.permissions import membership_for


class Command(BaseCommand):
    help = 'Preview conversion of unused imported draft products to proposals; --apply commits the conversion.'

    def add_arguments(self, parser):
        parser.add_argument('--tenant-id', type=int, required=True)
        parser.add_argument('--purchase-id', type=int, required=True)
        parser.add_argument('--apply', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        tenant = Organization.objects.select_for_update().get(pk=options['tenant_id'])
        purchase = Purchase.objects.select_for_update().filter(
            tenant=tenant, pk=options['purchase_id'], status=Purchase.Status.DRAFT,
        ).first()
        if not purchase:
            raise CommandError('A draft purchase in the specified tenant is required.')
        if purchase.product_proposals.exists():
            raise CommandError('This draft already has proposals; review it in the workspace.')
        lines = list(purchase.lines.select_related('product', 'product__sales_unit', 'product__catalog_category'))
        candidates = {}
        for line in lines:
            product = line.product
            imported = AuditLog.objects.filter(tenant=tenant, object_type='Product',
                object_id=str(product.pk), action='inventory.product.created_purchase_import').exists()
            if (not imported or not product.is_active or product.image or product.quantity or product.stock
                    or product.description or product.allow_wholesale or product.technician_price is not None
                    or product.wholesale_price is not None or product.buying_price or product.reorder_threshold):
                continue
            if CustomFieldValue.objects.filter(tenant=tenant, target_model='product', object_id=str(product.pk)).exists():
                continue
            safe = True
            for relation in Product._meta.related_objects:
                records = getattr(product, relation.get_accessor_name(), None)
                if relation.one_to_one:
                    # Even zero-valued stock records are retained for explicit reconciliation.
                    try:
                        if records is not None:
                            safe = False
                    except relation.related_model.DoesNotExist:
                        pass
                elif records is not None:
                    query = records.all()
                    if relation.get_accessor_name() == 'purchase_lines':
                        query = query.exclude(purchase=purchase)
                    if query.exists():
                        safe = False
            if safe:
                candidates[product.pk] = product
        selected = [line for line in lines if line.product_id in candidates]
        self.stdout.write(f'Eligible unused imported products: {len(candidates)}; purchase rows: {len(selected)}')
        for product in candidates.values():
            self.stdout.write(f'  {product.pk}: {product.sku} — {product.name}')
        if not options['apply'] or not selected:
            self.stdout.write('No changes made.' if not options['apply'] else 'No eligible changes.')
            return
        rows = []
        for line in selected:
            product = line.product
            if not product.catalog_category_id or not product.sales_unit_id:
                raise CommandError('Category and unit must be configured before conversion.')
            rows.append(dict(sku=product.sku, product_name=product.name,
                quantity=str(line.quantity), unit_cost=str(line.unit_cost),
                category=product.catalog_category.name, unit=product.sales_unit.name,
                selling_price=str(product.selling_price), brand=product.brand, model_number=product.model_number,
                serialized='Yes' if product.is_serialized else 'No', track_expiry='Yes' if product.track_expiry else 'No',
                batch_reference=line.batch_reference, expiry_date=line.expiry_date.isoformat() if line.expiry_date else '',
                serial_numbers=line.serial_numbers, staging_reference=f'legacy-{product.pk}', resolution='new'))
        actor = purchase.created_by
        purchase.lines.filter(pk__in=[line.pk for line in selected]).delete()
        for product in candidates.values():
            old_id, sku = product.pk, product.sku
            product.delete()
            AuditLog.objects.create(organization=tenant, tenant=tenant, actor=actor,
                action='inventory.product.restaged_proposal', object_type='Purchase', object_id=str(purchase.pk),
                old_value={'product_id': old_id, 'sku': sku}, new_value={'proposal': True})
        apply_purchase_batch(purchase=purchase, rows=rows, organization=tenant, actor=actor,
                             membership=membership_for(actor, tenant))
        self.stdout.write(self.style.SUCCESS('Converted unused draft-only imported products to proposals.'))

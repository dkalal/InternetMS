from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from audit.models import AuditLog
from inventory.models import StockAdjustment
from inventory.services import InventoryService
from products.models import Product, ProductCategory, UnitOfMeasure
from products.services import ProductUnitTransitionService
from users.models import Organization


class Command(BaseCommand):
    help = 'Reconcile one explicitly identified sample product into legacy and successor unit identities.'

    def add_arguments(self, parser):
        parser.add_argument('--product-id', type=int, required=True)
        parser.add_argument('--actor-id', type=int, required=True)
        parser.add_argument('--category-id', type=int, required=True)
        parser.add_argument('--expected-current-quantity', type=Decimal, required=True)
        parser.add_argument('--legacy-unit-id', type=int, required=True)
        parser.add_argument('--legacy-reserve', type=Decimal, required=True)
        parser.add_argument('--legacy-unit-cost', type=Decimal, required=True)
        parser.add_argument('--legacy-selling-price', type=Decimal, required=True)
        parser.add_argument('--successor-unit-id', type=int, required=True)
        parser.add_argument('--successor-name', required=True)
        parser.add_argument('--successor-sku', required=True)
        parser.add_argument('--successor-buying-price', type=Decimal, required=True)
        parser.add_argument('--successor-selling-price', type=Decimal, required=True)
        parser.add_argument('--successor-stock', type=Decimal, required=True)
        parser.add_argument('--reason', required=True)
        parser.add_argument('--apply', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        product = (
            Product.objects.unscoped().select_for_update()
            .filter(pk=options['product_id']).first()
        )
        if product is None:
            raise CommandError('Product was not found.')
        organization = Organization.objects.get(pk=product.tenant_id)
        actor = get_user_model().objects.filter(pk=options['actor_id'], is_active=True).first()
        if actor is None:
            raise CommandError('Active actor was not found.')
        category = ProductCategory.objects.unscoped().filter(
            pk=options['category_id'], tenant=organization, is_active=True,
        ).first()
        if category is None:
            raise CommandError('Active product category was not found in the product tenant.')
        legacy_unit = UnitOfMeasure.objects.unscoped().filter(
            pk=options['legacy_unit_id'], tenant=organization, is_active=True,
        ).first()
        successor_unit = UnitOfMeasure.objects.unscoped().filter(
            pk=options['successor_unit_id'], tenant=organization, is_active=True,
        ).first()
        if legacy_unit is None or successor_unit is None or legacy_unit.pk == successor_unit.pk:
            raise CommandError('Select two different active units belonging to the product tenant.')
        if not category.allowed_units.filter(pk__in=(legacy_unit.pk, successor_unit.pk)).count() == 2:
            raise CommandError('The category must allow both the legacy and successor units.')
        if options['legacy_reserve'] < 0 or options['successor_stock'] < 0:
            raise CommandError('Reconciled quantities cannot be negative.')
        if options['legacy_unit_cost'] >= options['legacy_selling_price']:
            raise CommandError('Legacy selling price must be greater than legacy unit cost.')
        if options['successor_buying_price'] >= options['successor_selling_price']:
            raise CommandError('Successor selling price must be greater than successor buying price.')

        key_source = f"sample-unit-reconciliation:{organization.pk}:{product.pk}:{options['successor_sku']}"
        transition_key = uuid5(NAMESPACE_URL, key_source)
        completed = AuditLog.objects.unscoped().filter(
            tenant=organization,
            action='inventory.product.sample_unit_reconciled',
            object_type='Product',
            object_id=str(product.pk),
            metadata__transition_key=str(transition_key),
        ).first()
        if completed is not None:
            self.stdout.write(self.style.SUCCESS(
                f"Already reconciled; successor_product_id={completed.metadata['successor_product_id']}"
            ))
            return

        current_quantity = product.available_stock
        original_unit = product.get_measure_unit_display()
        if current_quantity != options['expected_current_quantity']:
            raise CommandError(
                f'Current quantity is {current_quantity}, not the expected '
                f"{options['expected_current_quantity']}; refusing to apply a stale plan."
            )
        plan = (
            f'product={product.pk} tenant={organization.pk} current={current_quantity} '
            f'legacy={options["legacy_reserve"]} {legacy_unit.label} '
            f'successor={options["successor_stock"]} {successor_unit.label} '
            f'sku={options["successor_sku"]}'
        )
        if not options['apply']:
            self.stdout.write(f'DRY RUN: {plan}')
            transaction.set_rollback(True)
            return

        Product.objects.unscoped().filter(pk=product.pk, tenant=organization).update(
            catalog_category=category,
            sales_unit=legacy_unit,
            measure_unit=legacy_unit.label,
            buying_price=options['legacy_unit_cost'],
            selling_price=options['legacy_selling_price'],
        )
        product.refresh_from_db()
        if current_quantity:
            InventoryService.adjust_stock(
                organization=organization,
                product_id=product.pk,
                quantity_delta=-current_quantity,
                reason=StockAdjustment.Reason.CORRECTION,
                actor=actor,
                notes=f"Sample-data unit repair: clear mixed-unit balance. {options['reason']}",
            )
        if options['legacy_reserve']:
            InventoryService.adjust_stock(
                organization=organization,
                product_id=product.pk,
                quantity_delta=options['legacy_reserve'],
                reason=StockAdjustment.Reason.CORRECTION,
                actor=actor,
                notes='Sample-data unit repair: reserve legacy units for issued historical invoices.',
            )

        successor, created = ProductUnitTransitionService.create_successor(
            organization=organization,
            source_product_id=product.pk,
            actor=actor,
            transition_key=transition_key,
            values={
                'name': options['successor_name'],
                'sku': options['successor_sku'].strip().upper(),
                'catalog_category': category,
                'sales_unit': successor_unit,
                'buying_price': options['successor_buying_price'],
                'selling_price': options['successor_selling_price'],
                'technician_price': None,
                'allow_wholesale': False,
                'wholesale_price': None,
                'wholesale_min_quantity': Decimal('1'),
                'reason': options['reason'],
            },
        )
        if not created:
            raise CommandError('Transition exists without the final reconciliation audit event.')
        if options['successor_stock']:
            InventoryService.adjust_stock(
                organization=organization,
                product_id=successor.pk,
                quantity_delta=options['successor_stock'],
                reason=StockAdjustment.Reason.OPENING,
                actor=actor,
                notes='Opening sample stock after explicit unit-identity reconciliation.',
            )

        AuditLog.objects.create(
            organization=organization,
            tenant=organization,
            actor=actor,
            performed_by=actor,
            action='inventory.product.sample_unit_reconciled',
            action_type='inventory.product.sample_unit_reconciled',
            object_type='Product',
            object_id=str(product.pk),
            document_id=str(product.pk),
            old_value={'mixed_quantity': str(current_quantity), 'mixed_unit': original_unit},
            new_value={
                'legacy_quantity': str(options['legacy_reserve']),
                'legacy_unit': legacy_unit.label,
                'successor_product_id': successor.pk,
                'successor_quantity': str(options['successor_stock']),
                'successor_unit': successor_unit.label,
            },
            metadata={
                'transition_key': str(transition_key),
                'successor_product_id': successor.pk,
                'reason': options['reason'],
                'sample_data_only': True,
            },
        )
        self.stdout.write(self.style.SUCCESS(f'APPLIED: {plan} successor_product_id={successor.pk}'))

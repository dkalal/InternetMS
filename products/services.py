from __future__ import annotations

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from audit.models import AuditLog
from users.permissions import PermissionCode, has_tenant_permission

from .models import Product, ProductCategory, UnitOfMeasure


class ProductUnitTransitionError(ValueError):
    pass


class ProductUnitTransitionService:
    """Create a successor identity without reinterpreting or moving historical stock."""

    @classmethod
    @transaction.atomic
    def create_successor(
        cls, *, organization, source_product_id: int, actor, transition_key, values: dict,
    ) -> tuple[Product, bool]:
        if not (
            has_tenant_permission(actor, organization, PermissionCode.PRODUCT_MANAGE)
            and has_tenant_permission(actor, organization, PermissionCode.COST_REPORT_VIEW)
        ):
            raise PermissionDenied('Insufficient permissions for a product unit transition.')
        transition_key = str(transition_key)
        prior = AuditLog.objects.unscoped().filter(
            tenant=organization,
            action='inventory.product.unit_successor_created',
            object_type='Product',
            object_id=str(source_product_id),
            metadata__transition_key=transition_key,
        ).first()
        if prior is not None:
            successor = Product.objects.unscoped().filter(
                tenant=organization, pk=prior.metadata.get('successor_product_id'),
            ).first()
            if successor is not None:
                return successor, False

        source = Product.objects.unscoped().select_for_update().filter(pk=source_product_id).first()
        if source is None or source.tenant_id != organization.id:
            raise PermissionDenied('Product is not available in the active business.')
        if source.item_type != Product.ItemType.PHYSICAL or not source.track_stock:
            raise ProductUnitTransitionError('Only a stock-tracked physical product can move to a new unit identity.')
        if not source.has_unit_history():
            raise ProductUnitTransitionError('This product has no transaction history; edit its unit directly instead.')

        unit = UnitOfMeasure.objects.unscoped().filter(pk=values['sales_unit'].pk, tenant=organization, is_active=True).first()
        category = ProductCategory.objects.unscoped().filter(
            pk=values['catalog_category'].pk, tenant=organization, is_active=True,
        ).first()
        if unit is None or category is None:
            raise PermissionDenied('The selected category or unit is not available in the active business.')
        if source.sales_unit_id == unit.pk:
            raise ProductUnitTransitionError('The successor must use a different sales/stock unit.')
        if not category.allowed_units.filter(pk=unit.pk, is_active=True).exists():
            raise ProductUnitTransitionError('The selected unit is not allowed by the chosen category.')
        if Product.objects.unscoped().filter(tenant=organization, sku__iexact=values['sku']).exists():
            raise ProductUnitTransitionError('The successor SKU is already in use.')

        successor = Product(
            organization=organization,
            tenant=organization,
            sku=values['sku'],
            name=values['name'],
            description=source.description,
            item_type=source.item_type,
            catalog_category=category,
            sales_unit=unit,
            measure_unit=unit.label,
            brand=source.brand,
            model_number=source.model_number,
            buying_price=values['buying_price'],
            selling_price=values['selling_price'],
            technician_price=values.get('technician_price'),
            wholesale_price=values.get('wholesale_price'),
            wholesale_min_quantity=values['wholesale_min_quantity'],
            allow_wholesale=values.get('allow_wholesale', False),
            tax_eligible=source.tax_eligible,
            track_stock=True,
            is_serialized=source.is_serialized,
            track_expiry=source.track_expiry,
            reorder_threshold=source.reorder_threshold,
            is_active=True,
            category=source.category,
            quantity=0,
            stock=0,
        )
        try:
            successor.full_clean()
            successor.save()
        except ValidationError as exc:
            raise ProductUnitTransitionError('; '.join(exc.messages)) from exc

        Product.objects.unscoped().filter(pk=source.pk, tenant=organization).update(is_active=False)
        source.is_active = False
        AuditLog.objects.create(
            organization=organization,
            tenant=organization,
            actor=actor,
            performed_by=actor,
            action='inventory.product.unit_successor_created',
            action_type='inventory.product.unit_successor_created',
            object_type='Product',
            object_id=str(source.pk),
            document_id=str(source.pk),
            old_value={
                'source_product_id': source.pk,
                'source_sku': source.sku,
                'source_unit': source.get_measure_unit_display(),
                'source_active': True,
            },
            new_value={
                'source_active': False,
                'successor_product_id': successor.pk,
                'successor_sku': successor.sku,
                'successor_unit': successor.get_measure_unit_display(),
            },
            metadata={
                'transition_key': transition_key,
                'successor_product_id': successor.pk,
                'reason': values['reason'],
                'stock_transferred': False,
                'history_reassigned': False,
            },
        )
        return successor, True

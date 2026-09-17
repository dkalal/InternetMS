from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile

from .models import (
    InventoryBalance,
    Purchase,
    PurchaseLine,
    StockMovement,
    StockUnit,
    Supplier,
)
from .services import InventoryError, InventoryService


User = get_user_model()


class UnifiedPurchasingSameUnitContractTests(TestCase):
    """Protect the same-unit, high-volume receiving contract across UI phases."""

    def setUp(self):
        self.organization = Organization.objects.create(
            name="Unified Purchasing Tenant",
            slug="unified-purchasing-tenant-v2",
        )
        self.other_organization = Organization.objects.create(
            name="Other Purchasing Tenant",
            slug="other-purchasing-tenant-v2",
        )
        self.admin = User.objects.create_user(
            username="unified-purchasing-admin-v2",
            password="pass",
        )
        UserAccessProfile.objects.create(
            user=self.admin,
            tenant=self.organization,
            role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.unit = UnitOfMeasure.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name="Unit",
            symbol="pc",
        )
        self.category = ProductCategory.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name="Network Equipment",
            default_unit=self.unit,
            measure_unit="pc",
        )
        self.router = self.make_product(
            name="Serialized Router",
            sku="RTR-CONTRACT-001",
            serialized=True,
            buying_price="85000.000000",
            selling_price="125000.00",
        )
        self.supplier = Supplier.objects.create(
            organization=self.organization,
            tenant=self.organization,
            company_name="Contract Network Supply Ltd",
            phone="+255700000001",
            created_by=self.admin,
        )

    def make_product(
        self,
        *,
        name,
        sku,
        serialized=False,
        buying_price="100.000000",
        selling_price="150.00",
    ):
        return Product.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name=name,
            sku=sku,
            item_type=Product.ItemType.PHYSICAL,
            catalog_category=self.category,
            sales_unit=self.unit,
            measure_unit="pc",
            quantity=Decimal("0.000000"),
            stock=0,
            track_stock=True,
            is_serialized=serialized,
            buying_price=Decimal(buying_price),
            selling_price=Decimal(selling_price),
        )

    def make_purchase(self, reference):
        return Purchase.objects.create(
            organization=self.organization,
            tenant=self.organization,
            supplier=self.supplier,
            reference_number=reference,
            purchase_date=date.today(),
            created_by=self.admin,
        )

    def test_high_volume_same_unit_purchase_posts_once(self):
        purchase = self.make_purchase("SAME-UNIT-BULK-001")
        PurchaseLine.objects.create(
            purchase=purchase,
            product=self.router,
            quantity=Decimal("5.000000"),
            unit_cost=Decimal("85000.000000"),
            serial_numbers="\n".join(f"CONTRACT-RTR-{number:03d}" for number in range(1, 6)),
        )

        accessories = []
        for number in range(1, 26):
            product = self.make_product(
                name=f"Accessory {number:02d}",
                sku=f"CONTRACT-ACC-{number:03d}",
            )
            accessories.append(product)
            PurchaseLine.objects.create(
                purchase=purchase,
                product=product,
                quantity=Decimal("2.000000"),
                unit_cost=Decimal("100.000000"),
            )

        InventoryService.confirm_purchase(
            organization=self.organization,
            purchase_id=purchase.pk,
            actor=self.admin,
        )

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.CONFIRMED)
        self.assertEqual(purchase.total_cost, Decimal("430000.00"))
        self.assertEqual(InventoryBalance.objects.get(product=self.router).quantity, Decimal("5.000000"))
        self.assertTrue(
            all(
                InventoryBalance.objects.get(product=product).quantity == Decimal("2.000000")
                for product in accessories
            )
        )
        self.assertEqual(StockUnit.objects.filter(product=self.router).count(), 5)
        self.assertEqual(StockMovement.objects.filter(purchase_line__purchase=purchase).count(), 26)

        InventoryService.confirm_purchase(
            organization=self.organization,
            purchase_id=purchase.pk,
            actor=self.admin,
        )
        self.assertEqual(StockMovement.objects.filter(purchase_line__purchase=purchase).count(), 26)
        self.assertEqual(StockUnit.objects.filter(product=self.router).count(), 5)

    def test_invalid_line_rolls_back_the_whole_purchase(self):
        ordinary_product = self.make_product(name="Switch", sku="CONTRACT-SW-001")
        purchase = self.make_purchase("SAME-UNIT-ROLLBACK-001")
        PurchaseLine.objects.create(
            purchase=purchase,
            product=ordinary_product,
            quantity=Decimal("10.000000"),
            unit_cost=Decimal("100.000000"),
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            product=self.router,
            quantity=Decimal("5.000000"),
            unit_cost=Decimal("85000.000000"),
            serial_numbers="RTR-001\nRTR-002\nRTR-003\nRTR-004",
        )

        with self.assertRaisesMessage(InventoryError, "requires exactly 5 serial numbers"):
            InventoryService.confirm_purchase(
                organization=self.organization,
                purchase_id=purchase.pk,
                actor=self.admin,
            )

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertFalse(StockMovement.objects.filter(purchase_line__purchase=purchase).exists())
        self.assertFalse(InventoryBalance.objects.filter(tenant=self.organization).exists())
        self.assertFalse(StockUnit.objects.filter(tenant=self.organization).exists())

    def test_duplicate_serials_across_lines_fail_before_posting(self):
        second_router = self.make_product(
            name="Outdoor Router",
            sku="CONTRACT-RTR-OUT-001",
            serialized=True,
            buying_price="95000.000000",
            selling_price="140000.00",
        )
        purchase = self.make_purchase("SAME-UNIT-SERIAL-001")
        PurchaseLine.objects.create(
            purchase=purchase,
            product=self.router,
            quantity=Decimal("1.000000"),
            unit_cost=Decimal("85000.000000"),
            serial_numbers="DUPLICATE-CONTRACT-SERIAL",
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            product=second_router,
            quantity=Decimal("1.000000"),
            unit_cost=Decimal("95000.000000"),
            serial_numbers="duplicate-contract-serial",
        )

        with self.assertRaisesMessage(
            InventoryError,
            "Duplicate serial numbers were entered across purchase lines",
        ):
            InventoryService.confirm_purchase(
                organization=self.organization,
                purchase_id=purchase.pk,
                actor=self.admin,
            )

        self.assertFalse(StockMovement.objects.filter(purchase_line__purchase=purchase).exists())
        self.assertFalse(StockUnit.objects.filter(tenant=self.organization).exists())

    def test_other_tenant_cannot_confirm_purchase(self):
        purchase = self.make_purchase("SAME-UNIT-TENANT-001")
        PurchaseLine.objects.create(
            purchase=purchase,
            product=self.router,
            quantity=Decimal("1.000000"),
            unit_cost=Decimal("85000.000000"),
            serial_numbers="TENANT-CONTRACT-SERIAL",
        )

        with self.assertRaisesMessage(PermissionDenied, "active tenant"):
            InventoryService.confirm_purchase(
                organization=self.other_organization,
                purchase_id=purchase.pk,
                actor=self.admin,
            )

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertFalse(StockMovement.objects.filter(purchase_line__purchase=purchase).exists())

    def test_legacy_authoritative_total_is_preserved_but_new_lines_use_same_unit_math(self):
        purchase = self.make_purchase("SAME-UNIT-HISTORY-001")
        legacy_line = PurchaseLine.objects.create(
            purchase=purchase,
            product=self.router,
            quantity=Decimal("3.000000"),
            unit_cost=Decimal("655.737705"),
            authoritative_purchase_total=Decimal("200000.00"),
        )
        current_line = PurchaseLine.objects.create(
            purchase=purchase,
            product=self.make_product(name="Current Product", sku="CONTRACT-CURRENT-001"),
            quantity=Decimal("3.500000"),
            unit_cost=Decimal("750.000000"),
        )

        self.assertEqual(legacy_line.line_total, Decimal("200000.00"))
        self.assertEqual(current_line.line_total, Decimal("2625.00"))


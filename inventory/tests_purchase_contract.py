from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile

from inventory.forms import PurchaseLineForm
from inventory.models import (
    InventoryBalance,
    Purchase,
    PurchaseLine,
    StockMovement,
    StockUnit,
    Supplier,
)
from inventory.services import InventoryError, InventoryService


User = get_user_model()


class UnifiedPurchasingBaselineContractTests(TestCase):
    """Freeze purchasing invariants before the unified workspace redesign."""

    def setUp(self):
        self.organization = Organization.objects.create(
            name="Unified Purchasing Tenant",
            slug="unified-purchasing-tenant",
        )
        self.admin = User.objects.create_user(
            username="unified-purchasing-admin",
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
        self.meter = UnitOfMeasure.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name="Meter",
            symbol="m",
        )
        self.networking = ProductCategory.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name="Networking",
            default_unit=self.unit,
            measure_unit="pc",
        )
        self.networking.allowed_units.add(self.meter)
        self.cable = self.make_product(
            name="UTP Cable",
            sku="UTP-305",
            sales_unit=self.meter,
            measure_unit="m",
            buying_price="655.737705",
            selling_price="2000.00",
            default_purchase_unit_label="Box",
            default_purchase_conversion_factor="305.000000",
            default_purchase_unit_cost="200000.00",
        )
        self.router = self.make_product(
            name="Router",
            sku="RTR-001",
            serialized=True,
            buying_price="85000.000000",
            selling_price="125000.00",
        )
        self.supplier = Supplier.objects.create(
            organization=self.organization,
            tenant=self.organization,
            company_name="Network Supply Ltd",
            phone="+255700000001",
            created_by=self.admin,
        )

    def make_product(
        self,
        *,
        name,
        sku,
        sales_unit=None,
        measure_unit="pc",
        serialized=False,
        buying_price="100.000000",
        selling_price="150.00",
        default_purchase_unit_label="Unit",
        default_purchase_conversion_factor="1.000000",
        default_purchase_unit_cost=None,
    ):
        return Product.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name=name,
            sku=sku,
            item_type=Product.ItemType.PHYSICAL,
            catalog_category=self.networking,
            sales_unit=sales_unit or self.unit,
            measure_unit=measure_unit,
            quantity=Decimal("0.000000"),
            stock=0,
            track_stock=True,
            is_serialized=serialized,
            buying_price=Decimal(buying_price),
            selling_price=Decimal(selling_price),
            default_purchase_unit_label=default_purchase_unit_label,
            default_purchase_conversion_factor=Decimal(default_purchase_conversion_factor),
            default_purchase_unit_cost=(
                Decimal(default_purchase_unit_cost)
                if default_purchase_unit_cost is not None
                else None
            ),
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

    def add_pack_line(self, purchase, *, packs="3", pack_cost="200000"):
        form = PurchaseLineForm(
            data={
                "product": self.cable.pk,
                "entry_mode": PurchaseLineForm.ENTRY_PACK,
                "pack_unit_label": "Box",
                "pack_quantity": packs,
                "pack_conversion_factor": "305",
                "pack_unit_cost": pack_cost,
                "quantity": "",
                "unit_cost": "",
                "batch_reference": "",
                "expiry_date": "",
                "serial_numbers": "",
            },
            organization=self.organization,
        )
        self.assertTrue(form.is_valid(), form.errors)
        line = form.save(commit=False)
        line.purchase = purchase
        line.save()
        return line

    def test_mixed_high_volume_purchase_posts_once_with_authoritative_totals(self):
        purchase = self.make_purchase("BULK-MIXED-001")
        cable_line = self.add_pack_line(purchase)
        PurchaseLine.objects.create(
            purchase=purchase,
            product=self.router,
            quantity=Decimal("5.000000"),
            unit_cost=Decimal("85000.000000"),
            serial_numbers="\n".join(f"RTR-SN-{number:03d}" for number in range(1, 6)),
        )

        bulk_products = []
        for number in range(1, 25):
            product = self.make_product(
                name=f"Accessory {number:02d}",
                sku=f"ACC-{number:03d}",
            )
            bulk_products.append(product)
            PurchaseLine.objects.create(
                purchase=purchase,
                product=product,
                quantity=Decimal("2.000000"),
                unit_cost=Decimal("100.000000"),
            )

        confirmed = InventoryService.confirm_purchase(
            organization=self.organization,
            purchase_id=purchase.pk,
            actor=self.admin,
        )

        cable_line.refresh_from_db()
        confirmed.refresh_from_db()
        self.assertEqual(cable_line.quantity, Decimal("915.000000"))
        self.assertEqual(cable_line.authoritative_purchase_total, Decimal("600000.00"))
        self.assertEqual(
            InventoryBalance.objects.get(product=self.cable).quantity,
            Decimal("915.000000"),
        )
        self.assertEqual(
            InventoryBalance.objects.get(product=self.router).quantity,
            Decimal("5.000000"),
        )
        self.assertTrue(
            all(
                InventoryBalance.objects.get(product=product).quantity
                == Decimal("2.000000")
                for product in bulk_products
            )
        )
        self.assertEqual(StockUnit.objects.filter(product=self.router).count(), 5)
        self.assertEqual(
            StockMovement.objects.filter(purchase_line__purchase=purchase).count(),
            26,
        )
        self.assertEqual(confirmed.total_cost, Decimal("1029800.00"))

        InventoryService.confirm_purchase(
            organization=self.organization,
            purchase_id=purchase.pk,
            actor=self.admin,
        )
        self.assertEqual(
            StockMovement.objects.filter(purchase_line__purchase=purchase).count(),
            26,
        )
        self.assertEqual(StockUnit.objects.filter(product=self.router).count(), 5)
        self.assertEqual(
            InventoryBalance.objects.get(product=self.cable).quantity,
            Decimal("915.000000"),
        )

    def test_one_invalid_line_rolls_back_the_whole_mixed_purchase(self):
        purchase = self.make_purchase("BULK-ROLLBACK-001")
        self.add_pack_line(purchase)
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
        self.assertFalse(
            StockMovement.objects.filter(purchase_line__purchase=purchase).exists()
        )
        self.assertFalse(InventoryBalance.objects.filter(product=self.cable).exists())
        self.assertFalse(InventoryBalance.objects.filter(product=self.router).exists())
        self.assertFalse(StockUnit.objects.filter(product=self.router).exists())

    def test_duplicate_serials_across_purchase_lines_fail_before_stock_posting(self):
        second_router = self.make_product(
            name="Outdoor Router",
            sku="RTR-OUT-001",
            serialized=True,
            buying_price="95000.000000",
            selling_price="140000.00",
        )
        purchase = self.make_purchase("BULK-SERIAL-001")
        PurchaseLine.objects.create(
            purchase=purchase,
            product=self.router,
            quantity=Decimal("1.000000"),
            unit_cost=Decimal("85000.000000"),
            serial_numbers="DUPLICATE-SERIAL",
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            product=second_router,
            quantity=Decimal("1.000000"),
            unit_cost=Decimal("95000.000000"),
            serial_numbers="duplicate-serial",
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

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)
        self.assertFalse(
            StockMovement.objects.filter(purchase_line__purchase=purchase).exists()
        )
        self.assertFalse(StockUnit.objects.filter(tenant=self.organization).exists())

    def test_confirmation_rederives_tampered_pack_values_at_posting_boundary(self):
        purchase = self.make_purchase("BULK-TAMPER-001")
        line = self.add_pack_line(purchase)
        PurchaseLine.objects.filter(pk=line.pk).update(
            quantity=Decimal("1.000000"),
            unit_cost=Decimal("1.000000"),
            authoritative_purchase_total=Decimal("1.00"),
        )

        InventoryService.confirm_purchase(
            organization=self.organization,
            purchase_id=purchase.pk,
            actor=self.admin,
        )

        line.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(line.quantity, Decimal("915.000000"))
        self.assertEqual(line.unit_cost, Decimal("655.737705"))
        self.assertEqual(line.authoritative_purchase_total, Decimal("600000.00"))
        self.assertEqual(purchase.total_cost, Decimal("600000.00"))
        self.assertEqual(
            InventoryBalance.objects.get(product=self.cable).quantity,
            Decimal("915.000000"),
        )

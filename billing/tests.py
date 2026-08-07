from __future__ import annotations

from datetime import date
import importlib
import unittest
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from threading import Barrier, Thread

from audit.models import AuditLog
from billing.forms import BillingLineItemFormSet
from billing.models import BillingDocument, BillingItem, BillingLineItem, BillingSheet, CustomerSubscription, DocumentSequence, Promotion, SubscriptionPeriod
from billing.numbering import DocumentNumberService
from billing.services import (
    BillingService,
    BillingServiceError,
    BillingSheetService,
    ISSUED_INVOICE_EDIT_ERROR,
    LineItemInput,
    QuotationLifecycleService,
    SubscriptionBillingService,
)
from customers.models import Customer
from products.models import Product, UnitOfMeasure
from services.models import Package
from users.models import Membership, Organization, OrganizationBranding, UserAccessProfile


User = get_user_model()


class BillingServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="u1", password="pass")
        self.org1 = Organization.objects.create(name="Org One", slug="org-one")
        self.org2 = Organization.objects.create(name="Org Two", slug="org-two")

        self.customer_org1 = Customer.objects.create(
            organization=self.org1,
            name="Customer 1",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        self.customer_org2 = Customer.objects.create(
            organization=self.org2,
            name="Customer 2",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Arusha",
        )

        self.product_org1 = Product.objects.create(
            organization=self.org1,
            name="Router",
            category="hardware",
            quantity=Decimal("1.00"),
            measure_unit="Unit",
            buying_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            stock=10,
            is_active=True,
        )
        self.product_org2 = Product.objects.create(
            organization=self.org2,
            name="Other Router",
            category="hardware",
            quantity=Decimal("1.00"),
            measure_unit="Unit",
            buying_price=Decimal("10.00"),
            selling_price=Decimal("20.00"),
            stock=5,
            is_active=True,
        )
        self.package_org1 = Package.objects.create(
            organization=self.org1,
            name="10 Mbps",
            package_type="indoor",
            speed="10 Mbps",
            monthly_fee=Decimal("50000.00"),
            setup_fee=Decimal("0.00"),
            description="Internet package",
            is_active=True,
        )

    def _quotation_items(self, *, price: Decimal = Decimal("150.00")):
        pricing_mode = (
            BillingLineItem.PricingMode.MANUAL
            if price != Decimal("150.00")
            else BillingLineItem.PricingMode.RETAIL
        )
        return [
            LineItemInput(
                product_id=self.product_org1.id,
                quantity=Decimal("1.00"),
                unit_price=price,
                pricing_mode=pricing_mode,
            ),
            LineItemInput(package_id=self.package_org1.id, quantity=Decimal("1.00"), unit_price=Decimal("50000.00")),
        ]

    def test_product_and_package_units_are_saved_as_line_snapshots(self):
        quotation = self._create_quotation()
        product_line = quotation.items.get(product=self.product_org1)
        package_line = quotation.items.get(package=self.package_org1)
        self.assertEqual(product_line.unit_snapshot, 'Unit')
        self.assertEqual(package_line.unit_snapshot, 'Installation')

        metre = UnitOfMeasure.objects.create(
            organization=self.org1, tenant=self.org1, name='Metre', symbol='m'
        )
        self.product_org1.sales_unit = metre
        self.product_org1.save()
        product_line.refresh_from_db()
        self.assertEqual(product_line.unit_snapshot, 'Unit')

        recurring = BillingService.create_document(
            organization=self.org1, created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.pk, tax_rate=Decimal('0.00'),
            items=[LineItemInput(
                package_id=self.package_org1.pk, quantity=Decimal('1.00'),
                billing_behavior=BillingLineItem.BillingBehavior.RECURRING_MONTHLY,
            )],
        )
        self.assertEqual(recurring.items.get().unit_snapshot, 'Month')

    def test_invoice_account_summary_keeps_previous_debt_out_of_current_total(self):
        prior = BillingService.create_document(
            organization=self.org1, created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.pk, status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal('0.00'), items=[LineItemInput(description='Older debt', unit_price=Decimal('100.00'))],
        )
        current = BillingService.create_document(
            organization=self.org1, created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.pk, status=BillingDocument.Status.DRAFT,
            tax_rate=Decimal('0.00'), items=[LineItemInput(description='Current work', unit_price=Decimal('50.00'))],
        )
        summary = BillingService.invoice_account_summary(organization=self.org1, invoice=current)
        self.assertEqual(current.total, Decimal('50.00'))
        self.assertEqual(summary['previous_outstanding_balance'], prior.total)
        self.assertEqual(summary['total_amount_due'], Decimal('150.00'))
        self.assertEqual(summary['outstanding_account_balance'], Decimal('150.00'))

    def test_tax_exempt_product_is_excluded_from_document_vat(self):
        exempt_product = Product.objects.create(
            organization=self.org1,
            name="Exempt installation material",
            category="hardware",
            quantity=Decimal("1.00"),
            measure_unit="Unit",
            buying_price=Decimal("20.00"),
            selling_price=Decimal("60.00"),
            stock=1,
            tax_eligible=False,
            is_active=True,
        )

        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            status=BillingDocument.Status.DRAFT,
            tax_rate=Decimal("18.00"),
            items=[
                LineItemInput(product_id=self.product_org1.id, quantity=Decimal("1.00")),
                LineItemInput(product_id=exempt_product.id, quantity=Decimal("1.00")),
            ],
        )

        self.assertEqual(invoice.subtotal, Decimal("210.00"))
        self.assertEqual(invoice.tax_amount, Decimal("27.00"))
        self.assertEqual(invoice.total, Decimal("237.00"))

    def _create_quotation(self) -> BillingDocument:
        return BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.QUOTATION,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            items=self._quotation_items(),
        )

    def _create_invoice(self, *, status: str = BillingDocument.Status.DRAFT) -> BillingDocument:
        return BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            status=status,
            items=self._quotation_items(),
        )

    def test_quotation_version_creation_preserves_history(self):
        quotation_v1 = self._create_quotation()

        quotation_v2 = BillingService.create_quotation_version(
            organization=self.org1,
            created_by=self.user,
            quotation_id=quotation_v1.id,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            due_date=timezone.now().date(),
            status=BillingDocument.Status.DRAFT,
            currency="TZS",
            tax_rate=Decimal("16.00"),
            notes="Updated quotation",
            items=self._quotation_items(price=Decimal("175.00")),
        )

        quotation_v1.refresh_from_db()
        self.assertEqual(quotation_v1.version_number, 1)
        self.assertFalse(quotation_v1.is_current_version)
        self.assertEqual(quotation_v2.version_number, 2)
        self.assertEqual(quotation_v2.number, quotation_v1.number)
        self.assertRegex(quotation_v1.number, r"^QTN-JS-\d{4}-0001$")
        self.assertEqual(quotation_v2.parent_quotation_id, quotation_v1.id)
        self.assertEqual(quotation_v2.root_quotation_id, quotation_v1.id)
        self.assertTrue(quotation_v2.is_current_version)

    def test_subscription_renewal_creates_discounted_invoice_and_paid_period(self):
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_org1,
            package=self.package_org1,
            start_date=date(2026, 4, 1),
        )
        promotion = Promotion.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Pay 5 get 1 free",
            applies_to=Promotion.AppliesTo.PACKAGE,
            package=self.package_org1,
            minimum_months=5,
            reward_type=Promotion.RewardType.FREE_MONTHS,
            reward_value=Decimal("1.00"),
        )

        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.id,
            period_start=date(2026, 4, 1),
            months=5,
            promotion_id=promotion.id,
        )

        self.assertEqual(period.status, SubscriptionPeriod.Status.INVOICED)
        self.assertEqual(period.free_months, 1)
        self.assertEqual(period.final_amount, Decimal("250000.00"))
        self.assertIsNotNone(period.invoice_id)
        self.assertEqual(period.invoice.tax_rate, Decimal("0.00"))
        self.assertEqual(period.invoice.tax_amount, Decimal("0.00"))
        self.assertEqual(period.invoice.total, Decimal("250000.00"))
        invoice_item = period.invoice.items.get()
        self.assertEqual(invoice_item.billing_behavior, invoice_item.BillingBehavior.RECURRING_MONTHLY)
        self.assertEqual(invoice_item.promotion_id, promotion.id)

        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=period.invoice_id,
            amount_paid=period.invoice.total,
            payment_date=date(2026, 4, 2),
            payment_method="cash",
        )

        period.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(period.status, SubscriptionPeriod.Status.PAID)
        self.assertEqual(period.receipt_id, receipt.id)
        self.assertEqual(subscription.paid_through_date, period.period_end)

    def test_subscription_invoice_description_uses_speed_and_month_range_without_office_name(self):
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_org1,
            package=self.package_org1,
            start_date=date(2026, 4, 1),
        )

        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.id,
            period_start=date(2026, 4, 1),
            months=2,
        )

        description = period.invoice.items.get().description
        self.assertEqual(description, "Billing for the 10 Mbps (10 Mbps) - April 2026 - May 2026")
        self.assertNotIn("Office", description)

    def test_new_invoice_captures_prior_open_balance_without_rebilling_it(self):
        prior_invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=date(2026, 4, 1),
            status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal("0.00"),
            items=[LineItemInput(description="April service", quantity=Decimal("1.00"), unit_price=Decimal("100000.00"))],
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=prior_invoice.id,
            amount_paid=Decimal("40000.00"),
            payment_method="cash",
        )

        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=date(2026, 5, 1),
            status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal("0.00"),
            items=[LineItemInput(description="May service", quantity=Decimal("1.00"), unit_price=Decimal("50000.00"))],
        )

        self.assertEqual(invoice.balance_brought_forward, Decimal("60000.00"))
        self.assertEqual(invoice.total, Decimal("50000.00"))
        self.assertEqual(
            BillingService.customer_open_invoice_balance(organization=self.org1, customer=self.customer_org1),
            Decimal("110000.00"),
        )

    def test_subscription_renewal_adds_vat_for_vrn_customer(self):
        self.customer_org1.vrn_number = "VRN-123"
        self.customer_org1.save(update_fields=["vrn_number"])
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_org1,
            package=self.package_org1,
            start_date=date(2026, 4, 1),
        )

        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.id,
            period_start=date(2026, 4, 1),
            months=1,
        )

        self.assertEqual(period.invoice.tax_rate, Decimal("18.00"))
        self.assertEqual(period.invoice.tax_amount, Decimal("9000.00"))
        self.assertEqual(period.invoice.total, Decimal("59000.00"))

    def test_reissue_subscription_invoice_recalculates_tax_and_moves_period_link(self):
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_org1,
            package=self.package_org1,
            start_date=date(2026, 4, 1),
        )
        period = SubscriptionBillingService.create_period(
            organization=self.org1,
            subscription=subscription,
            period_start=date(2026, 4, 1),
            months=1,
        )
        original_invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=date(2026, 4, 1),
            status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal("18.00"),
            items=[
                LineItemInput(
                    package_id=self.package_org1.id,
                    description="April subscription",
                    quantity=Decimal("1.00"),
                    unit_price=Decimal("50000.00"),
                    billing_behavior=BillingLineItem.BillingBehavior.RECURRING_MONTHLY,
                )
            ],
        )
        SubscriptionPeriod.objects.filter(id=period.id).update(
            invoice=original_invoice,
            status=SubscriptionPeriod.Status.INVOICED,
        )

        reissued = BillingService.reissue_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=original_invoice.id,
            reason="Wrong tax treatment on the original invoice.",
        )
        period.refresh_from_db()
        original_invoice.refresh_from_db()

        self.assertEqual(original_invoice.status, BillingDocument.Status.SUPERSEDED)
        self.assertEqual(reissued.status, BillingDocument.Status.DRAFT)
        self.assertEqual(reissued.tax_rate, Decimal("0.00"))
        self.assertEqual(reissued.tax_amount, Decimal("0.00"))
        self.assertEqual(period.invoice_id, reissued.id)

    def test_void_subscription_invoice_cancels_invoice_and_period_with_audit_reason(self):
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_org1,
            package=self.package_org1,
            start_date=date(2026, 4, 1),
        )
        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.id,
            period_start=date(2026, 4, 1),
            months=1,
        )
        invoice_id = period.invoice_id

        resolved = BillingService.void_subscription_invoice(
            organization=self.org1,
            performed_by=self.user,
            period_id=period.id,
            reason="Invoice was created for the wrong month.",
        )
        invoice = BillingDocument.objects.get(id=invoice_id)

        self.assertEqual(resolved.status, SubscriptionPeriod.Status.CANCELLED)
        self.assertEqual(resolved.invoice_id, invoice_id)
        self.assertEqual(invoice.status, BillingDocument.Status.VOID)
        log = AuditLog.objects.get(action_type="subscription.invoice_voided", document_id=str(invoice_id))
        self.assertEqual(log.metadata["reason"], "Invoice was created for the wrong month.")
        self.assertEqual(log.metadata["subscription_period_id"], period.id)

    def test_void_subscription_invoice_blocks_paid_periods(self):
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_org1,
            package=self.package_org1,
            start_date=date(2026, 4, 1),
        )
        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.id,
            period_start=date(2026, 4, 1),
            months=1,
        )
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=period.invoice_id,
            amount_paid=period.invoice.total,
            payment_date=date(2026, 4, 2),
            payment_method="cash",
            payment_reference="paid-period",
        )
        period.refresh_from_db()

        with self.assertRaisesMessage(BillingServiceError, "Paid subscription periods need"):
            BillingService.void_subscription_invoice(
                organization=self.org1,
                performed_by=self.user,
                period_id=period.id,
                reason="Mistake found after payment.",
            )

        self.assertEqual(period.receipt_id, receipt.id)
        self.assertEqual(period.status, SubscriptionPeriod.Status.PAID)

    def test_subscription_period_prevents_duplicate_month(self):
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_org1,
            package=self.package_org1,
            start_date=date(2026, 4, 1),
        )
        first = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.id,
            period_start=date(2026, 4, 1),
            months=1,
        )
        second = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.id,
            period_start=date(2026, 4, 20),
            months=1,
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(SubscriptionPeriod.objects.filter(subscription=subscription).count(), 1)

    def test_quotation_history_retrieval_returns_all_versions_and_current(self):
        quotation_v1 = self._create_quotation()
        quotation_v2 = BillingService.create_quotation_version(
            organization=self.org1,
            created_by=self.user,
            quotation_id=quotation_v1.id,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            due_date=None,
            status=BillingDocument.Status.DRAFT,
            currency="TZS",
            tax_rate=Decimal("18.00"),
            notes="Revision 2",
            items=self._quotation_items(price=Decimal("160.00")),
        )
        quotation_v3 = BillingService.create_quotation_version(
            organization=self.org1,
            created_by=self.user,
            quotation_id=quotation_v2.id,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            due_date=None,
            status=BillingDocument.Status.DRAFT,
            currency="TZS",
            tax_rate=Decimal("18.00"),
            notes="Revision 3",
            items=self._quotation_items(price=Decimal("170.00")),
        )

        history = list(BillingService.get_quotation_history(organization=self.org1, quotation_id=quotation_v3.id).order_by("version_number"))

        self.assertEqual([item.version_number for item in history], [1, 2, 3])
        self.assertEqual(history[-1].id, quotation_v3.id)
        self.assertTrue(history[-1].is_current_version)

        comparison = BillingService.compare_quotation_versions(
            organization=self.org1,
            from_quotation_id=quotation_v1.id,
            to_quotation_id=quotation_v3.id,
        )
        self.assertIn("items", comparison["changes"])

    def test_invoice_editing_allowed_only_in_draft(self):
        invoice = self._create_invoice(status=BillingDocument.Status.DRAFT)

        updated = BillingService.update_draft_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
            tax_rate=Decimal("10.00"),
            items=[LineItemInput(description="Draft-only edit", quantity=Decimal("2.00"), unit_price=Decimal("25.00"))],
        )

        self.assertEqual(updated.status, BillingDocument.Status.DRAFT)
        self.assertEqual(updated.tax_rate, Decimal("10.00"))
        self.assertEqual(updated.items.count(), 1)
        self.assertEqual(updated.items.first().description, "Draft-only edit")

    def test_invoice_draft_edit_can_publish_invoice(self):
        invoice = self._create_invoice(status=BillingDocument.Status.DRAFT)

        updated = BillingService.update_draft_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
            tax_rate=Decimal("18.00"),
            status=BillingDocument.Status.ISSUED,
            items=[LineItemInput(description="Published invoice", quantity=Decimal("1.00"), unit_price=Decimal("50.00"))],
        )

        self.assertEqual(updated.status, BillingDocument.Status.ISSUED)
        self.assertIsNotNone(updated.issued_at)
        self.assertEqual(updated.items.first().description, "Published invoice")

    def test_reissued_subscription_invoice_update_syncs_paid_through_to_multiple_months(self):
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_org1,
            package=self.package_org1,
            start_date=date(2026, 7, 1),
        )
        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.id,
            period_start=date(2026, 7, 1),
            months=1,
        )
        original_invoice = period.invoice

        reissued = BillingService.reissue_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=original_invoice.id,
            reason="Invoice should have covered two months, not one.",
        )
        updated = BillingService.update_draft_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=reissued.id,
            tax_rate=Decimal("0.00"),
            status=BillingDocument.Status.ISSUED,
            items=[
                LineItemInput(
                    package_id=self.package_org1.id,
                    description="July-August subscription",
                    quantity=Decimal("1.00"),
                    unit_price=Decimal("100000.00"),
                    billing_behavior=BillingLineItem.BillingBehavior.RECURRING_MONTHLY,
                    pricing_mode=BillingLineItem.PricingMode.MANUAL,
                )
            ],
        )
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=updated.id,
            amount_paid=updated.total,
            payment_method="cash",
        )

        period.refresh_from_db()
        subscription.refresh_from_db()

        self.assertEqual(period.months, 2)
        self.assertEqual(period.period_end, date(2026, 8, 31))
        self.assertEqual(subscription.paid_through_date, date(2026, 8, 31))
        self.assertEqual(receipt.invoice_id, updated.id)

    def test_invoice_editing_blocked_after_issuing(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)

        with self.assertRaisesMessage(BillingServiceError, ISSUED_INVOICE_EDIT_ERROR):
            BillingService.update_draft_invoice(
                organization=self.org1,
                performed_by=self.user,
                invoice_id=invoice.id,
                tax_rate=Decimal("10.00"),
                items=[LineItemInput(description="Blocked", quantity=Decimal("1.00"), unit_price=Decimal("10.00"))],
            )

    def test_product_pricing_uses_wholesale_for_qualified_customer_tier(self):
        self.customer_org1.pricing_tier = Customer.PricingTier.WHOLESALE
        self.customer_org1.save(update_fields=["pricing_tier"])
        self.product_org1.allow_wholesale = True
        self.product_org1.retail_price = Decimal("150.00")
        self.product_org1.wholesale_price = Decimal("120.00")
        self.product_org1.wholesale_min_quantity = Decimal("5.00")
        self.product_org1.save(
            update_fields=["allow_wholesale", "retail_price", "wholesale_price", "wholesale_min_quantity"]
        )

        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            tax_rate=Decimal("0.00"),
            items=[
                LineItemInput(
                    product_id=self.product_org1.id,
                    quantity=Decimal("5.00"),
                    unit_price=Decimal("999.00"),
                )
            ],
        )

        item = invoice.items.get()
        self.assertEqual(item.unit_price, Decimal("120.00"))
        self.assertEqual(item.pricing_mode, BillingLineItem.PricingMode.WHOLESALE)
        self.assertEqual(invoice.total, Decimal("600.00"))

    def test_selected_product_promotion_is_applied_server_side(self):
        promotion = Promotion.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Router discount",
            applies_to=Promotion.AppliesTo.PRODUCT,
            product=self.product_org1,
            minimum_quantity=Decimal("2.00"),
            reward_type=Promotion.RewardType.PERCENT,
            reward_value=Decimal("10.00"),
        )

        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            tax_rate=Decimal("0.00"),
            items=[
                LineItemInput(
                    product_id=self.product_org1.id,
                    quantity=Decimal("2.00"),
                    unit_price=Decimal("150.00"),
                    promotion_id=promotion.id,
                )
            ],
        )

        item = invoice.items.get()
        self.assertEqual(item.discount_amount, Decimal("30.00"))
        self.assertEqual(item.discount_reason, "Router discount")
        self.assertEqual(item.pricing_mode, BillingLineItem.PricingMode.PROMOTION)
        self.assertEqual(invoice.total, Decimal("270.00"))

    def test_credit_note_creation_references_invoice(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)

        credit_note = BillingService.create_credit_note(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
            amount=invoice.total,
            reason="Full reversal approved after issue.",
        )

        self.assertEqual(credit_note.document_type, BillingDocument.DocumentType.CREDIT_NOTE)
        self.assertEqual(credit_note.corrected_invoice_id, invoice.id)
        self.assertLess(credit_note.total, Decimal("0.00"))
        self.assertRegex(credit_note.number, r"^CRN-ORG-\d{8}-0001$")

    def test_visible_numbers_use_fixed_prefix_and_annual_sequence(self):
        issue_date = date(2026, 4, 1)

        quotation = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.QUOTATION,
            customer_id=self.customer_org1.id,
            issue_date=issue_date,
            items=self._quotation_items(),
        )
        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=issue_date,
            items=self._quotation_items(),
        )

        self.assertEqual(quotation.number, "QTN-JS-2026-0001")
        self.assertEqual(invoice.number, "INV-JS-2026-0001")
        self.assertIsNotNone(quotation.created_at)
        self.assertIsNotNone(quotation.updated_at)
        self.assertIsNotNone(quotation.issued_at)

    def test_tenant_isolation_raises_permission_denied(self):
        with self.assertRaises(PermissionDenied):
            BillingService.create_document(
                organization=self.org1,
                created_by=self.user,
                document_type=BillingDocument.DocumentType.QUOTATION,
                customer_id=self.customer_org2.id,
                issue_date=timezone.now().date(),
                items=[],
            )

        with self.assertRaises(PermissionDenied):
            BillingService.create_document(
                organization=self.org1,
                created_by=self.user,
                document_type=BillingDocument.DocumentType.QUOTATION,
                customer_id=self.customer_org1.id,
                issue_date=timezone.now().date(),
                items=[LineItemInput(product_id=self.product_org2.id, quantity=Decimal("1.00"), unit_price=Decimal("2.00"))],
            )

    def test_audit_logs_created_correctly_for_financial_actions(self):
        quotation = self._create_quotation()
        quotation_v2 = BillingService.create_quotation_version(
            organization=self.org1,
            created_by=self.user,
            quotation_id=quotation.id,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            due_date=None,
            status=BillingDocument.Status.DRAFT,
            currency="TZS",
            tax_rate=Decimal("18.00"),
            notes="Revision 2",
            items=self._quotation_items(price=Decimal("155.00")),
        )
        draft_invoice = BillingService.create_invoice_from_quotation(
            organization=self.org1,
            created_by=self.user,
            quotation_id=quotation_v2.id,
        )
        BillingService.update_draft_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=draft_invoice.id,
            tax_rate=Decimal("15.00"),
            items=[LineItemInput(description="Edited", quantity=Decimal("1.00"), unit_price=Decimal("75.00"))],
        )

        void_invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)
        BillingService.void_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=void_invoice.id,
            reason="Invoice should not exist for this customer.",
        )

        issued_invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)
        reissued_invoice = BillingService.reissue_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=issued_invoice.id,
            reason="Wrong package was used on the original invoice.",
        )
        BillingDocument.objects.filter(pk=reissued_invoice.pk).update(status=BillingDocument.Status.ISSUED)
        reissued_invoice.refresh_from_db()
        credit_note = BillingService.create_credit_note(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=reissued_invoice.id,
            amount=Decimal("50.00"),
            reason="Post-issue discount approved.",
        )

        actions = set(
            AuditLog.objects.filter(organization=self.org1).values_list("action_type", flat=True)
        )
        self.assertTrue(
            {
                "quotation_created",
                "quotation_version_created",
                "quotation_converted_to_invoice",
                "invoice_created",
                "invoice_edited",
                "invoice_voided",
                "invoice_superseded",
                "invoice_reissued",
                "credit_note_created",
            }.issubset(actions)
        )

        log = AuditLog.objects.get(action_type="invoice_reissued", document_id=str(reissued_invoice.id))
        self.assertEqual(log.performed_by_id, self.user.id)
        self.assertEqual(log.tenant_id, self.org1.id)
        self.assertEqual(log.old_value["id"], issued_invoice.id)
        self.assertEqual(log.new_value["id"], reissued_invoice.id)
        self.assertEqual(credit_note.corrected_invoice_id, reissued_invoice.id)

    def test_latest_quotation_version_only_can_convert_to_invoice(self):
        quotation_v1 = self._create_quotation()
        quotation_v2 = BillingService.create_quotation_version(
            organization=self.org1,
            created_by=self.user,
            quotation_id=quotation_v1.id,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            due_date=None,
            status=BillingDocument.Status.DRAFT,
            currency="TZS",
            tax_rate=Decimal("18.00"),
            notes="Revision 2",
            items=self._quotation_items(price=Decimal("160.00")),
        )

        with self.assertRaisesMessage(BillingServiceError, "Only the latest quotation version can be converted to an invoice."):
            BillingService.create_invoice_from_quotation(
                organization=self.org1,
                created_by=self.user,
                quotation_id=quotation_v1.id,
            )

        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org1,
            created_by=self.user,
            quotation_id=quotation_v2.id,
        )
        quotation_v2.refresh_from_db()
        self.assertEqual(invoice.document_type, BillingDocument.DocumentType.INVOICE)
        self.assertEqual(invoice.source_quotation_id, quotation_v2.id)
        self.assertEqual(quotation_v2.status, BillingDocument.Status.CONVERTED)
        self.assertEqual(quotation_v2.converted_invoice_id, invoice.id)

    def test_quotation_can_transition_from_draft_to_sent_to_accepted(self):
        quotation = self._create_quotation()

        sent = QuotationLifecycleService.send(
            organization=self.org1,
            performed_by=self.user,
            quotation_id=quotation.id,
            reason="Shared with the customer by WhatsApp.",
        )
        accepted = QuotationLifecycleService.accept(
            organization=self.org1,
            performed_by=self.user,
            quotation_id=quotation.id,
            reason="Customer approved the quotation.",
        )

        self.assertEqual(sent.status, BillingDocument.Status.SENT)
        self.assertIsNotNone(sent.sent_at)
        self.assertEqual(accepted.status, BillingDocument.Status.ACCEPTED)
        self.assertIsNotNone(accepted.accepted_at)

    def test_rejected_quotation_cannot_be_converted(self):
        quotation = self._create_quotation()
        QuotationLifecycleService.reject(
            organization=self.org1,
            performed_by=self.user,
            quotation_id=quotation.id,
            reason="Customer declined the offer.",
        )

        with self.assertRaisesMessage(BillingServiceError, "cannot be converted"):
            BillingService.create_invoice_from_quotation(
                organization=self.org1,
                created_by=self.user,
                quotation_id=quotation.id,
            )

    def test_receipt_creation_is_idempotent_for_same_invoice(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=invoice.total,
            payment_method="cash",
            payment_reference="ref-1",
        )

        duplicate_submit = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=invoice.total,
            payment_method="cash",
            payment_reference="ref-1",
        )

        self.assertEqual(duplicate_submit.id, receipt.id)
        self.assertEqual(
            BillingDocument.objects.filter(
                organization=self.org1,
                document_type=BillingDocument.DocumentType.RECEIPT,
                invoice=invoice,
            ).count(),
            1,
        )

    def test_receipt_reference_cannot_be_reused_for_another_invoice(self):
        first_invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)
        second_invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=first_invoice.id,
            amount_paid=first_invoice.total,
            payment_method="cash",
            payment_reference="ref-1",
        )

        with self.assertRaisesMessage(BillingServiceError, "payment reference has already been used"):
            BillingService.create_receipt_from_invoice(
                organization=self.org1,
                created_by=self.user,
                invoice_id=second_invoice.id,
                amount_paid=second_invoice.total,
                payment_method="cash",
                payment_reference="ref-1",
            )

    def test_receipt_number_uses_receipt_prefix(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)

        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=invoice.total,
            payment_method="cash",
            payment_reference="ref-unique",
        )

        self.assertRegex(receipt.number, r"^RCT-JS-\d{4}-0001$")

    def test_receipt_creation_uses_the_amount_paid_for_partial_payments(self):
        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            status=BillingDocument.Status.ISSUED,
            items=[
                LineItemInput(
                    description="Fiber installation",
                    quantity=Decimal("1.00"),
                    unit_price=Decimal("150000.00"),
                    pricing_mode=BillingLineItem.PricingMode.MANUAL,
                )
            ],
        )

        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=Decimal("100000.00"),
            payment_method="cash",
            payment_reference="partial-100k",
        )

        invoice.refresh_from_db()
        self.assertEqual(receipt.total, Decimal("100000.00"))
        self.assertEqual(receipt.subtotal, Decimal("100000.00"))
        self.assertEqual(receipt.items.get().line_total, Decimal("100000.00"))
        self.assertEqual(invoice.status, BillingDocument.Status.PARTIALLY_PAID)
        self.assertEqual(receipt.invoice_id, invoice.id)

    def test_partially_paid_invoice_becomes_paid_when_balance_is_fully_settled(self):
        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal("0.00"),
            items=[
                LineItemInput(
                    description="Fiber installation",
                    quantity=Decimal("1.00"),
                    unit_price=Decimal("100000.00"),
                    pricing_mode=BillingLineItem.PricingMode.MANUAL,
                )
            ],
        )

        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=Decimal("40000.00"),
            payment_method="cash",
            payment_reference="partial-40000",
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=Decimal("60000.00"),
            payment_method="cash",
            payment_reference="final-60000",
        )

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.PAID)

    def test_billing_sheet_invoice_generation_uses_the_selected_due_date(self):
        sheet = BillingSheetService.create_sheet(
            organization=self.org1,
            created_by=self.user,
            customer_id=self.customer_org1.id,
            title="One-time installation",
        )
        BillingItem.objects.create(
            billing_sheet=sheet,
            description="Cable installation",
            quantity=Decimal("1.00"),
            unit_price=Decimal("50000.00"),
        )

        invoice = BillingSheetService.generate_invoice(
            organization=self.org1,
            performed_by=self.user,
            sheet_id=sheet.id,
            due_date=date(2026, 4, 30),
        )

        sheet.refresh_from_db()
        self.assertEqual(invoice.due_date, date(2026, 4, 30))
        self.assertEqual(sheet.invoice_id, invoice.id)
        self.assertEqual(sheet.status, BillingSheet.Status.INVOICED)

    def test_document_detail_shows_creator_for_quotation_invoice_and_receipt(self):
        Membership.objects.create(
            organization=self.org1,
            user=self.user,
            role=Membership.Role.ADMIN,
            is_active=True,
        )
        self.client.login(username="u1", password="pass")
        session = self.client.session
        session["active_org_id"] = self.org1.id
        session.save()

        quotation = self._create_quotation()
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=invoice.total,
            payment_method="cash",
            payment_reference="creator-check",
        )

        for doc in (quotation, invoice, receipt):
            response = self.client.get(reverse("billing:document_detail", kwargs={"doc_type": doc.document_type, "pk": doc.id}))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Created by u1")

    def test_line_item_formset_accepts_hidden_delete_value(self):
        quotation = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.QUOTATION,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            tax_rate=Decimal("0.00"),
            items=[
                LineItemInput(
                    description="Remove this item",
                    quantity=Decimal("1.00"),
                    unit_price=Decimal("10000.00"),
                    pricing_mode=BillingLineItem.PricingMode.MANUAL,
                ),
                LineItemInput(
                    description="Keep this item",
                    quantity=Decimal("2.00"),
                    unit_price=Decimal("15000.00"),
                    pricing_mode=BillingLineItem.PricingMode.MANUAL,
                ),
            ],
        )
        first_item, second_item = list(quotation.items.order_by("id"))

        data = {
            "items-TOTAL_FORMS": "2",
            "items-INITIAL_FORMS": "2",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-id": str(first_item.id),
            "items-0-document": str(quotation.id),
            "items-0-product": "",
            "items-0-package": "",
            "items-0-description": first_item.description,
            "items-0-quantity": str(first_item.quantity),
            "items-0-unit_price": str(first_item.unit_price),
            "items-0-billing_behavior": first_item.billing_behavior,
            "items-0-pricing_mode": first_item.pricing_mode,
            "items-0-discount_amount": str(first_item.discount_amount),
            "items-0-discount_reason": "",
            "items-0-promotion": "",
            "items-0-DELETE": "on",
            "items-1-id": str(second_item.id),
            "items-1-document": str(quotation.id),
            "items-1-product": "",
            "items-1-package": "",
            "items-1-description": second_item.description,
            "items-1-quantity": str(second_item.quantity),
            "items-1-unit_price": str(second_item.unit_price),
            "items-1-billing_behavior": second_item.billing_behavior,
            "items-1-pricing_mode": second_item.pricing_mode,
            "items-1-discount_amount": str(second_item.discount_amount),
            "items-1-discount_reason": "",
            "items-1-promotion": "",
            "items-1-DELETE": "",
        }
        formset = BillingLineItemFormSet(
            data,
            prefix="items",
            form_kwargs={"organization": self.org1},
            instance=quotation,
        )

        self.assertTrue(formset.is_valid(), formset.errors)
        active_items = [form.cleaned_data for form in formset if not form.cleaned_data.get("DELETE")]
        self.assertEqual(len(active_items), 1)
        self.assertEqual(active_items[0]["description"], "Keep this item")

    def test_partial_payment_summary_is_shown_on_invoice_and_receipt_details(self):
        Membership.objects.create(
            organization=self.org1,
            user=self.user,
            role=Membership.Role.ADMIN,
            is_active=True,
        )
        self.client.login(username="u1", password="pass")
        session = self.client.session
        session["active_org_id"] = self.org1.id
        session.save()

        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal("0.00"),
            items=[
                LineItemInput(
                    description="Fiber installation",
                    quantity=Decimal("1.00"),
                    unit_price=Decimal("100000.00"),
                    pricing_mode=BillingLineItem.PricingMode.MANUAL,
                )
            ],
        )
        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=Decimal("50000.00"),
            payment_method="cash",
            payment_reference="partial-ui-1",
        )

        invoice_response = self.client.get(reverse("billing:document_detail", kwargs={"doc_type": "invoice", "pk": invoice.id}))
        receipt_response = self.client.get(reverse("billing:document_detail", kwargs={"doc_type": "receipt", "pk": receipt.id}))

        self.assertEqual(invoice_response.status_code, 200)
        self.assertContains(invoice_response, "Payment summary")
        self.assertContains(invoice_response, "Paid 50000.00")
        self.assertContains(invoice_response, "Due 50000.00")
        self.assertContains(invoice_response, "Create receipt")

        self.assertEqual(receipt_response.status_code, 200)
        self.assertContains(receipt_response, "Applied payment")
        self.assertContains(receipt_response, "Invoice")
        self.assertContains(receipt_response, "50000.00")

    def test_superseded_invoice_detail_shows_reason_and_replacement_link(self):
        Membership.objects.create(
            organization=self.org1,
            user=self.user,
            role=Membership.Role.ADMIN,
            is_active=True,
        )
        self.client.login(username="u1", password="pass")
        session = self.client.session
        session["active_org_id"] = self.org1.id
        session.save()

        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=timezone.now().date(),
            status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal("0.00"),
            items=[
                LineItemInput(
                    package_id=self.package_org1.id,
                    description="Monthly service",
                    quantity=Decimal("1.00"),
                    unit_price=Decimal("50000.00"),
                    billing_behavior=BillingLineItem.BillingBehavior.RECURRING_MONTHLY,
                    pricing_mode=BillingLineItem.PricingMode.MANUAL,
                )
            ],
        )

        reissued = BillingService.reissue_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
            reason="Wrong package amount used on the original invoice.",
        )

        response = self.client.get(reverse("billing:document_detail", kwargs={"doc_type": "invoice", "pk": invoice.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Superseded invoice")
        self.assertContains(response, "Wrong package amount used on the original invoice.")
        self.assertContains(response, reissued.number)

    def test_document_creation_requires_at_least_one_line_item(self):
        with self.assertRaisesMessage(BillingServiceError, "At least one line item is required."):
            BillingService.create_document(
                organization=self.org1,
                created_by=self.user,
                document_type=BillingDocument.DocumentType.QUOTATION,
                customer_id=self.customer_org1.id,
                issue_date=timezone.now().date(),
                items=[],
            )

    def test_document_creation_rejects_non_positive_quantity(self):
        with self.assertRaisesMessage(BillingServiceError, "Line item quantity must be greater than 0."):
            BillingService.create_document(
                organization=self.org1,
                created_by=self.user,
                document_type=BillingDocument.DocumentType.QUOTATION,
                customer_id=self.customer_org1.id,
                issue_date=timezone.now().date(),
                items=[LineItemInput(description="Broken item", quantity=Decimal("0.00"), unit_price=Decimal("10.00"))],
            )

    def test_annual_sequence_uses_issue_date_and_resets_next_year(self):
        day_one = date(2026, 4, 1)
        day_two = date(2026, 4, 2)
        next_year = date(2027, 1, 1)

        first = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=day_one,
            items=self._quotation_items(),
        )
        second = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=day_one,
            items=self._quotation_items(),
        )
        third = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=day_two,
            items=self._quotation_items(),
        )
        fourth = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=next_year,
            items=self._quotation_items(),
        )

        self.assertEqual(first.number, "INV-JS-2026-0001")
        self.assertEqual(second.number, "INV-JS-2026-0002")
        self.assertEqual(third.number, "INV-JS-2026-0003")
        self.assertEqual(fourth.number, "INV-JS-2027-0001")

    def test_counters_are_separate_per_tenant(self):
        issue_date = date(2026, 4, 1)

        invoice_org1 = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=issue_date,
            items=self._quotation_items(),
        )
        invoice_org2 = BillingService.create_document(
            organization=self.org2,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org2.id,
            issue_date=issue_date,
            items=[LineItemInput(product_id=self.product_org2.id, quantity=Decimal("1.00"), unit_price=Decimal("20.00"))],
        )

        self.assertEqual(invoice_org1.number, "INV-JS-2026-0001")
        self.assertEqual(invoice_org2.number, "INV-JS-2026-0001")

    def test_document_type_sequences_are_independent(self):
        document_date = date(2026, 8, 1)

        numbers = {
            document_type: DocumentNumberService.next_number(
                organization=self.org1,
                document_type=document_type,
                issue_date=document_date,
            ).value
            for document_type in (
                BillingDocument.DocumentType.QUOTATION,
                BillingDocument.DocumentType.INVOICE,
                BillingDocument.DocumentType.RECEIPT,
            )
        }

        self.assertEqual(numbers[BillingDocument.DocumentType.QUOTATION], "QTN-JS-2026-0001")
        self.assertEqual(numbers[BillingDocument.DocumentType.INVOICE], "INV-JS-2026-0001")
        self.assertEqual(numbers[BillingDocument.DocumentType.RECEIPT], "RCT-JS-2026-0001")

    def test_payment_reference_replay_cannot_change_the_payment_amount(self):
        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal("0.00"),
            items=[LineItemInput(package_id=self.package_org1.id, unit_price=Decimal("50000.00"))],
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=Decimal("10000.00"),
            payment_method="mobile_money",
            payment_reference="provider-transaction-123",
        )

        with self.assertRaisesMessage(BillingServiceError, "different payment amount"):
            BillingService.create_receipt_from_invoice(
                organization=self.org1,
                created_by=self.user,
                invoice_id=invoice.id,
                amount_paid=Decimal("20000.00"),
                payment_method="mobile_money",
                payment_reference="provider-transaction-123",
            )

        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.PARTIALLY_PAID)
        self.assertEqual(invoice.receipts.count(), 1)
        self.assertEqual(invoice.receipts.get().total, Decimal("10000.00"))

    def test_serial_padding_and_values_beyond_9999(self):
        sequence = DocumentSequence.objects.unscoped().create(
            organization=self.org1,
            tenant=self.org1,
            document_type=BillingDocument.DocumentType.INVOICE,
            year=2026,
            last_number=24,
        )

        padded = DocumentNumberService.next_number(
            organization=self.org1,
            document_type=BillingDocument.DocumentType.INVOICE,
            issue_date=date(2026, 1, 1),
        )
        self.assertEqual(padded.value, "INV-JS-2026-0025")

        sequence.refresh_from_db()
        sequence.last_number = 9999
        sequence.save(update_fields=["last_number"])
        unbounded = DocumentNumberService.next_number(
            organization=self.org1,
            document_type=BillingDocument.DocumentType.INVOICE,
            issue_date=date(2026, 12, 31),
        )
        self.assertEqual(unbounded.value, "INV-JS-2026-10000")

    def test_legacy_document_number_is_not_changed(self):
        legacy = BillingDocument.objects.create(
            organization=self.org1,
            tenant=self.org1,
            document_type=BillingDocument.DocumentType.INVOICE,
            number="INV-ORG-20251231-0042",
            customer=self.customer_org1,
            issue_date=date(2025, 12, 31),
        )

        DocumentNumberService.next_number(
            organization=self.org1,
            document_type=BillingDocument.DocumentType.INVOICE,
            issue_date=date(2026, 1, 1),
        )
        legacy.refresh_from_db()

        self.assertEqual(legacy.number, "INV-ORG-20251231-0042")

    def test_assigned_document_number_is_immutable(self):
        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            issue_date=date(2026, 1, 1),
            items=self._quotation_items(),
        )

        invoice.number = "INV-JS-2026-9999"
        with self.assertRaisesMessage(ValidationError, "assigned document number cannot be changed"):
            invoice.save()

    def test_database_rejects_duplicate_invoice_number_within_tenant(self):
        kwargs = {
            "organization": self.org1,
            "tenant": self.org1,
            "document_type": BillingDocument.DocumentType.INVOICE,
            "number": "INV-JS-2026-0001",
            "customer": self.customer_org1,
            "issue_date": date(2026, 1, 1),
        }
        BillingDocument.objects.create(**kwargs)

        with self.assertRaises(IntegrityError), transaction.atomic():
            BillingDocument.objects.create(**kwargs)

    def test_receipt_number_uses_payment_business_year(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)

        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=invoice.total,
            payment_date=date(2027, 1, 2),
            payment_method="cash",
            payment_reference="next-year-payment",
        )

        self.assertEqual(receipt.number, "RCT-JS-2027-0001")

    def test_tenant_code_prefers_short_slug_when_available(self):
        org = Organization.objects.create(name="JS Internet Services", slug="js")
        self.assertEqual(DocumentNumberService.get_tenant_code(org), "JS")

    def test_unpaid_invoice_can_be_voided_with_reason(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)

        voided = BillingService.void_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
            reason="Invoice was raised in error for the wrong service.",
        )

        self.assertEqual(voided.status, BillingDocument.Status.VOID)
        self.assertEqual(
            AuditLog.objects.get(action_type="invoice_voided", document_id=str(invoice.id)).metadata["reason"],
            "Invoice was raised in error for the wrong service.",
        )

    def test_partially_paid_invoice_cannot_be_voided(self):
        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.id,
            status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal('0.00'),
            items=[LineItemInput(description='Managed service', unit_price=Decimal('200.00'))],
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=Decimal("100.00"),
            payment_method="cash",
            payment_reference="void-block-partial",
        )

        with self.assertRaisesMessage(BillingServiceError, "Only unpaid issued invoices"):
            BillingService.void_invoice(
                organization=self.org1,
                performed_by=self.user,
                invoice_id=invoice.id,
                reason="Tried to void after payment started.",
            )

    def test_subscription_partial_payment_does_not_activate_period_until_fully_settled(self):
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_org1,
            package=self.package_org1,
            start_date=date(2026, 9, 1),
        )
        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.pk,
            period_start=date(2026, 9, 1),
            months=1,
        )

        first_receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1, created_by=self.user, invoice_id=period.invoice_id,
            amount_paid=Decimal('25000.00'), payment_method='mobile_money',
            payment_reference='sub-partial-25',
        )
        period.invoice.refresh_from_db()
        period.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(period.invoice.status, BillingDocument.Status.PARTIALLY_PAID)
        self.assertEqual(period.status, SubscriptionPeriod.Status.INVOICED)
        self.assertIsNone(period.receipt_id)
        self.assertIsNone(period.paid_at)
        self.assertIsNone(subscription.paid_through_date)
        self.assertEqual(first_receipt.total, Decimal('25000.00'))
        self.assertEqual(
            BillingService.invoice_remaining_balance(organization=self.org1, invoice=period.invoice),
            Decimal('25000.00'),
        )

        final_receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1, created_by=self.user, invoice_id=period.invoice_id,
            amount_paid=Decimal('25000.00'), payment_method='mobile_money',
            payment_reference='sub-final-25',
        )
        period.invoice.refresh_from_db()
        period.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(period.invoice.status, BillingDocument.Status.PAID)
        self.assertEqual(period.status, SubscriptionPeriod.Status.PAID)
        self.assertEqual(period.receipt_id, final_receipt.pk)
        self.assertIsNotNone(period.paid_at)
        self.assertEqual(subscription.paid_through_date, period.period_end)
        self.assertEqual(period.invoice.receipts.count(), 2)

    def test_paying_an_older_subscription_period_cannot_reduce_paid_through_date(self):
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1,
            customer=self.customer_org1,
            package=self.package_org1,
            start_date=date(2026, 9, 1),
        )
        period = SubscriptionBillingService.renew(
            organization=self.org1,
            created_by=self.user,
            subscription_id=subscription.pk,
            period_start=date(2026, 9, 1),
            months=1,
        )
        future_paid_through = date(2026, 12, 31)
        CustomerSubscription.objects.filter(pk=subscription.pk).update(
            paid_through_date=future_paid_through
        )

        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=period.invoice_id,
            amount_paid=period.final_amount,
            payment_method="cash",
            payment_reference="older-period-payment",
        )

        subscription.refresh_from_db()
        self.assertEqual(subscription.paid_through_date, future_paid_through)

    def test_service_invoice_rejects_overpayment_and_preserves_state(self):
        invoice = BillingService.create_document(
            organization=self.org1, created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer_org1.pk, status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal('0.00'),
            items=[LineItemInput(package_id=self.package_org1.pk, unit_price=Decimal('50000.00'))],
        )
        with self.assertRaisesMessage(BillingServiceError, 'cannot exceed the remaining balance'):
            BillingService.create_receipt_from_invoice(
                organization=self.org1, created_by=self.user, invoice_id=invoice.pk,
                amount_paid=Decimal('50000.01'), payment_method='cash', payment_reference='overpay',
            )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, BillingDocument.Status.ISSUED)
        self.assertFalse(invoice.receipts.exists())

    def test_credit_note_cannot_exceed_remaining_credit_capacity(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)
        BillingService.create_credit_note(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
            amount=Decimal("50.00"),
            reason="Discount approved.",
        )

        with self.assertRaisesMessage(BillingServiceError, "remaining credit capacity"):
            BillingService.create_credit_note(
                organization=self.org1,
                performed_by=self.user,
                invoice_id=invoice.id,
                amount=invoice.total,
                reason="Second full credit should be blocked.",
            )

    def test_receipt_cannot_exceed_remaining_balance_after_credit_note(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)
        BillingService.create_credit_note(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
            amount=Decimal("50.00"),
            reason="Discount approved.",
        )

        with self.assertRaisesMessage(BillingServiceError, "remaining balance"):
            BillingService.create_receipt_from_invoice(
                organization=self.org1,
                created_by=self.user,
                invoice_id=invoice.id,
                amount_paid=invoice.total,
                payment_method="cash",
                payment_reference="credit-overpay",
            )

    def test_receipt_can_settle_invoice_after_credit_note_reduces_balance(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)
        BillingService.create_credit_note(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
            amount=Decimal("50.00"),
            reason="Discount approved.",
        )

        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            amount_paid=invoice.total - Decimal("50.00"),
            payment_method="cash",
            payment_reference="credit-settle",
        )
        invoice.refresh_from_db()

        self.assertEqual(receipt.total, invoice.total - Decimal("50.00"))
        self.assertEqual(invoice.status, BillingDocument.Status.PAID)

    def test_invoice_status_migration_maps_legacy_rows_safely(self):
        migration = importlib.import_module("billing.migrations.0014_invoice_lifecycle_statuses")
        base_kwargs = {
            "organization": self.org1,
            "tenant": self.org1,
            "document_type": BillingDocument.DocumentType.INVOICE,
            "customer": self.customer_org1,
            "issue_date": date(2026, 4, 1),
            "due_date": date(2026, 4, 10),
            "subtotal": Decimal("100.00"),
            "tax_rate": Decimal("0.00"),
            "tax_amount": Decimal("0.00"),
            "total": Decimal("100.00"),
        }
        legacy_reissued_parent = BillingDocument.objects.create(number="INV-MIG-001", status=BillingDocument.Status.CANCELLED, **base_kwargs)
        BillingDocument.objects.create(number="INV-MIG-002", status=BillingDocument.Status.REISSUED, original_invoice=legacy_reissued_parent, **base_kwargs)
        legacy_cancelled = BillingDocument.objects.create(number="INV-MIG-003", status=BillingDocument.Status.CANCELLED, **base_kwargs)
        legacy_cancelled_with_receipt = BillingDocument.objects.create(number="INV-MIG-004", status=BillingDocument.Status.CANCELLED, **base_kwargs)
        BillingDocument.objects.create(
            organization=self.org1,
            tenant=self.org1,
            document_type=BillingDocument.DocumentType.RECEIPT,
            number="REC-MIG-001",
            customer=self.customer_org1,
            invoice=legacy_cancelled_with_receipt,
            issue_date=date(2026, 4, 2),
            status=BillingDocument.Status.PAID,
            subtotal=Decimal("100.00"),
            tax_rate=Decimal("0.00"),
            tax_amount=Decimal("0.00"),
            total=Decimal("100.00"),
            payment_date=date(2026, 4, 2),
            payment_method="cash",
        )

        migration.forward_invoice_statuses(django_apps, None)

        legacy_reissued_parent.refresh_from_db()
        legacy_cancelled.refresh_from_db()
        legacy_cancelled_with_receipt.refresh_from_db()

        self.assertEqual(legacy_reissued_parent.status, BillingDocument.Status.SUPERSEDED)
        self.assertEqual(legacy_cancelled.status, BillingDocument.Status.VOID)
        self.assertEqual(legacy_cancelled_with_receipt.status, BillingDocument.Status.CANCELLED)

    def test_quotation_status_migration_maps_legacy_rows_safely(self):
        migration = importlib.import_module("billing.migrations.0015_separate_document_lifecycles")
        base_kwargs = {
            "organization": self.org1,
            "tenant": self.org1,
            "document_type": BillingDocument.DocumentType.QUOTATION,
            "customer": self.customer_org1,
            "issue_date": date(2026, 4, 1),
            "due_date": date(2026, 4, 10),
            "subtotal": Decimal("100.00"),
            "tax_rate": Decimal("0.00"),
            "tax_amount": Decimal("0.00"),
            "total": Decimal("100.00"),
        }
        approved = BillingDocument.objects.create(number="QUO-MIG-001", status=BillingDocument.Status.APPROVED, **base_kwargs)
        issued = BillingDocument.objects.create(number="QUO-MIG-002", status=BillingDocument.Status.ISSUED, **base_kwargs)

        migration.forward_document_lifecycle_updates(django_apps, None)

        approved.refresh_from_db()
        issued.refresh_from_db()
        self.assertEqual(approved.status, BillingDocument.Status.ACCEPTED)
        self.assertEqual(issued.status, BillingDocument.Status.SENT)


class TechnicianSalePricingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pricing-user", password="pass")
        self.organization = Organization.objects.create(name="Pricing Org", slug="pricing-org")
        self.customer = Customer.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name="Walk-in buyer",
            customer_type="random",
            location="Walk-in",
        )
        self.product = Product.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name="Technician router",
            sku="TECH-ROUTER",
            quantity=Decimal("0.00"),
            buying_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            technician_price=Decimal("130.00"),
        )

    def create_document(self, category):
        return BillingService.create_document(
            organization=self.organization,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.QUOTATION,
            customer_id=self.customer.pk,
            sale_pricing_category=category,
            items=[LineItemInput(product_id=self.product.pk, quantity=Decimal("1.00"))],
        )

    def test_standard_and_technician_categories_are_applied_intentionally(self):
        standard = self.create_document(BillingDocument.SalePricingCategory.STANDARD)
        technician = self.create_document(BillingDocument.SalePricingCategory.TECHNICIAN)

        self.assertEqual(standard.items.get().unit_price, Decimal("150.00"))
        self.assertEqual(standard.items.get().pricing_mode, BillingLineItem.PricingMode.STANDARD)
        self.assertEqual(technician.items.get().unit_price, Decimal("130.00"))
        self.assertEqual(technician.items.get().pricing_mode, BillingLineItem.PricingMode.TECHNICIAN)

    def test_technician_fallback_and_historical_snapshots_survive_catalog_and_customer_changes(self):
        quotation = self.create_document(BillingDocument.SalePricingCategory.TECHNICIAN)
        original_price = quotation.items.get().unit_price

        self.product.technician_price = Decimal("140.00")
        self.product.save()
        self.customer.pricing_tier = Customer.PricingTier.WHOLESALE
        self.customer.save()
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.organization,
            created_by=self.user,
            quotation_id=quotation.pk,
        )

        quotation.refresh_from_db()
        self.assertEqual(quotation.items.get().unit_price, original_price)
        self.assertEqual(invoice.items.get().unit_price, original_price)
        self.assertEqual(invoice.sale_pricing_category, BillingDocument.SalePricingCategory.TECHNICIAN)

        self.product.technician_price = None
        self.product.save()
        fallback = self.create_document(BillingDocument.SalePricingCategory.TECHNICIAN)
        self.assertEqual(fallback.items.get().unit_price, self.product.selling_price)

    def test_migration_preserves_existing_documents_as_legacy_retail(self):
        migration = importlib.import_module("billing.migrations.0023_sale_pricing_category")
        add_field = migration.Migration.operations[0]
        self.assertEqual(add_field.field.default, BillingDocument.SalePricingCategory.LEGACY_RETAIL)
        self.assertEqual(BillingDocument._meta.get_field("sale_pricing_category").default, BillingDocument.SalePricingCategory.STANDARD)


class BillingNumberConcurrencyTests(TransactionTestCase):
    reset_sequences = False

    def setUp(self):
        self.user = User.objects.create_user(username="concurrent", password="pass")
        self.org = Organization.objects.create(name="Org Concurrent", slug="org")
        self.customer = Customer.objects.create(
            organization=self.org,
            tenant=self.org,
            name="Concurrent Customer",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        self.product = Product.objects.create(
            organization=self.org,
            tenant=self.org,
            name="Concurrent Router",
            category="hardware",
            quantity=Decimal("1.00"),
            measure_unit="Unit",
            buying_price=Decimal("100.00"),
            selling_price=Decimal("150.00"),
            stock=10,
            is_active=True,
        )

    def _create_invoice_in_thread(self, barrier: Barrier, results: list[str], errors: list[Exception], issue_date):
        connection.close()
        try:
            barrier.wait(timeout=5)
            invoice = BillingService.create_document(
                organization=self.org,
                created_by=self.user,
                document_type=BillingDocument.DocumentType.INVOICE,
                customer_id=self.customer.id,
                issue_date=issue_date,
                items=[LineItemInput(product_id=self.product.id, quantity=Decimal("1.00"), unit_price=Decimal("150.00"))],
            )
            results.append(invoice.number)
        except Exception as exc:
            errors.append(exc)
        finally:
            connections["default"].close()

    def test_concurrent_document_creation_allocates_unique_numbers(self):
        if connection.vendor == "sqlite":
            self.skipTest("SQLite does not provide reliable select_for_update semantics for this concurrency test.")

        issue_date = date(2026, 4, 1)
        barrier = Barrier(2)
        results: list[str] = []
        errors: list[Exception] = []
        threads = [
            Thread(target=self._create_invoice_in_thread, args=(barrier, results, errors, issue_date)),
            Thread(target=self._create_invoice_in_thread, args=(barrier, results, errors, issue_date)),
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        if errors:
            raise errors[0]

        self.assertCountEqual(
            results,
            ["INV-JS-2026-0001", "INV-JS-2026-0002"],
        )


class BillingListViewTests(TestCase):
    def setUp(self):
        self.org1 = Organization.objects.create(name="Tenant A", slug="tenant-a")
        self.org2 = Organization.objects.create(name="Tenant B", slug="tenant-b")
        self.user = User.objects.create_user(username="billing-staff", password="pass")
        UserAccessProfile.objects.create(user=self.user, tenant=self.org1, role=UserAccessProfile.Role.TENANT_ADMIN)
        self.client.login(username="billing-staff", password="pass")
        self.customer = Customer.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Alpha Customer",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Moshi",
        )
        self.other_customer = Customer.objects.create(
            organization=self.org2,
            tenant=self.org2,
            name="Other Customer",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            location="Arusha",
        )

    def make_document(self, doc_type, number, *, org=None, customer=None, status=BillingDocument.Status.DRAFT, total="100.00"):
        return BillingDocument.objects.create(
            organization=org or self.org1,
            tenant=org or self.org1,
            document_type=doc_type,
            number=number,
            customer=customer or self.customer,
            issue_date=date(2026, 4, 1),
            due_date=date(2026, 4, 10),
            status=status,
            total=Decimal(total),
            payment_date=date(2026, 4, 2) if doc_type == BillingDocument.DocumentType.RECEIPT else None,
            payment_method="cash" if doc_type == BillingDocument.DocumentType.RECEIPT else "",
            payment_reference=f"ref-{number}" if doc_type == BillingDocument.DocumentType.RECEIPT else "",
        )

    def test_document_list_filters_paginates_and_preserves_query(self):
        for index in range(105):
            self.make_document(BillingDocument.DocumentType.INVOICE, f"INV-A-{index:03d}", status=BillingDocument.Status.ISSUED)
        self.make_document(BillingDocument.DocumentType.INVOICE, "INV-OTHER", org=self.org2, customer=self.other_customer)

        response = self.client.get(
            reverse("billing:document_list", kwargs={"doc_type": "invoice"}),
            {"page_size": "50", "search": "INV-A", "status": BillingDocument.Status.ISSUED, "page": "2"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["documents"]), 50)
        self.assertEqual(response.context["result_count"], 105)
        self.assertContains(response, "search=INV-A")
        self.assertContains(response, "page_size=50")

    def test_document_sort_fallback_and_receipt_reference_search(self):
        receipt = self.make_document(BillingDocument.DocumentType.RECEIPT, "REC-A-001", status=BillingDocument.Status.PAID)
        self.make_document(
            BillingDocument.DocumentType.RECEIPT,
            "REC-B-001",
            org=self.org2,
            customer=self.other_customer,
            status=BillingDocument.Status.PAID,
        )

        response = self.client.get(
            reverse("billing:document_list", kwargs={"doc_type": "receipt"}),
            {"search": receipt.payment_reference, "sort": "bad"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["documents"]), [receipt])
        self.assertEqual(response.context["active_sort"], "payment_date")

    def test_annual_number_displays_in_list_detail_and_print_view(self):
        invoice = self.make_document(
            BillingDocument.DocumentType.INVOICE,
            "INV-JS-2026-0001",
            status=BillingDocument.Status.ISSUED,
        )

        responses = [
            self.client.get(reverse("billing:document_list", kwargs={"doc_type": "invoice"})),
            self.client.get(
                reverse("billing:document_detail", kwargs={"doc_type": "invoice", "pk": invoice.pk})
            ),
            self.client.get(
                reverse("billing:document_pdf", kwargs={"doc_type": "invoice", "pk": invoice.pk}),
                {"download": "0"},
            ),
        ]

        for response in responses:
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, invoice.number)

    def test_invoice_print_is_compact_multipage_safe_and_hides_blank_tax_ids(self):
        invoice = BillingService.create_document(
            organization=self.org1,
            created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.pk,
            status=BillingDocument.Status.DRAFT,
            tax_rate=Decimal('0.00'),
            items=[
                LineItemInput(description=f'Installation item {index}', quantity=Decimal('1.00'), unit_price=Decimal('10.00'))
                for index in range(35)
            ],
        )
        html = render_to_string('billing/sales_document_print.html', {
            'document': invoice,
            'items': list(invoice.items.all()),
            'invoice_account_summary': BillingService.invoice_account_summary(organization=self.org1, invoice=invoice),
            'show_discount_column': False,
            'show_tax_column': False,
            'LOGO_DATA_URI': 'data:image/png;base64,AA==',
        })
        self.assertIn('Proforma Invoice', html)
        self.assertIn('table-header-group', html)
        self.assertIn('counter(pages)', html)
        self.assertIn('<img class="logo"', html)
        self.assertNotIn('TIN:', html)
        self.assertNotIn('VRN:', html)
        self.assertNotIn('Previous Outstanding Balance', html)
        self.assertEqual(html.count('Installation item '), 35)

    def test_invoice_and_quotation_pdfs_keep_internal_notes_and_unused_terms_private(self):
        branding = OrganizationBranding.objects.create(
            organization=self.org1,
            legal_name="Tenant A Legal Company",
            address_line1="Business address",
            bank_details="Bank: Customer Payments Account",
            footer_note="LEGACY TERMS MUST NOT BE CUSTOMER VISIBLE",
        )
        self.customer.phone = "+255712345678"
        self.customer.email = "billing@example.test"
        self.customer.address = "Customer postal address"
        self.customer.tin_number = "TIN-CUSTOMER-01"
        self.customer.vrn_number = "VRN-CUSTOMER-01"
        self.customer.save()

        for document_type in (
            BillingDocument.DocumentType.INVOICE,
            BillingDocument.DocumentType.QUOTATION,
        ):
            document = BillingService.create_document(
                organization=self.org1,
                created_by=self.user,
                document_type=document_type,
                customer_id=self.customer.pk,
                status=BillingDocument.Status.DRAFT,
                tax_rate=Decimal("0.00"),
                notes="INTERNAL CREDIT-RISK NOTE — NEVER DISCLOSE",
                items=[LineItemInput(description="Customer-facing service", unit_price=Decimal("100.00"))],
            )
            context = {
                "document": document,
                "items": list(document.items.all()),
                "invoice_account_summary": (
                    BillingService.invoice_account_summary(organization=self.org1, invoice=document)
                    if document_type == BillingDocument.DocumentType.INVOICE
                    else None
                ),
                "show_discount_column": False,
                "show_tax_column": False,
                "LOGO_DATA_URI": "data:image/png;base64,AA==",
                "ACTIVE_BRANDING": branding,
            }

            html = render_to_string("billing/sales_document_print.html", context)

            self.assertNotIn("INTERNAL CREDIT-RISK NOTE", html)
            self.assertNotIn("LEGACY TERMS MUST NOT BE CUSTOMER VISIBLE", html)
            self.assertNotIn('class="support-title">Terms', html)
            self.assertNotIn('class="support-title">Notes', html)
            self.assertIn("Bank: Customer Payments Account", html)
            self.assertIn("Alpha Customer", html)
            self.assertIn("Customer postal address", html)
            self.assertIn("+255712345678", html)
            self.assertIn("billing@example.test", html)
            self.assertIn("TIN: TIN-CUSTOMER-01", html)
            self.assertIn("VRN: VRN-CUSTOMER-01", html)
            self.assertNotIn("Customer details", html)
            self.assertNotIn('<div class="company-name">Tenant A Legal Company</div>', html)

            no_logo_html = render_to_string(
                "billing/sales_document_print.html",
                {**context, "LOGO_DATA_URI": None},
            )
            self.assertIn('<h1 class="company-name">Tenant A Legal Company</h1>', no_logo_html)

    def test_quotation_print_never_discloses_customer_debt(self):
        BillingService.create_document(
            organization=self.org1, created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.pk, status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal('0.00'), items=[LineItemInput(description='Old debt', unit_price=Decimal('100.00'))],
        )
        quotation = BillingService.create_document(
            organization=self.org1, created_by=self.user,
            document_type=BillingDocument.DocumentType.QUOTATION,
            customer_id=self.customer.pk, status=BillingDocument.Status.DRAFT,
            tax_rate=Decimal('0.00'), items=[LineItemInput(description='Quoted work', unit_price=Decimal('50.00'))],
        )
        html = render_to_string('billing/sales_document_print.html', {
            'document': quotation, 'items': list(quotation.items.all()),
            'show_discount_column': False, 'show_tax_column': False,
        })
        self.assertIn('Quotation total', html)
        self.assertNotIn('Previous Outstanding Balance', html)
        self.assertNotIn('Total Amount Due', html)
        self.assertNotIn('Outstanding Account Balance', html)

    def test_invoice_print_shows_previous_debt_and_recorded_payment_separately(self):
        BillingService.create_document(
            organization=self.org1, created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.pk, status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal('0.00'), items=[LineItemInput(description='Earlier invoice', unit_price=Decimal('80.00'))],
        )
        invoice = BillingService.create_document(
            organization=self.org1, created_by=self.user,
            document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=self.customer.pk, status=BillingDocument.Status.ISSUED,
            tax_rate=Decimal('0.00'), items=[LineItemInput(description='Current invoice', unit_price=Decimal('50.00'))],
        )
        BillingService.create_receipt_from_invoice(
            organization=self.org1, created_by=self.user, invoice_id=invoice.pk,
            amount_paid=invoice.total, payment_method='cash', payment_reference='print-payment',
        )
        summary = BillingService.invoice_account_summary(organization=self.org1, invoice=invoice)
        html = render_to_string('billing/sales_document_print.html', {
            'document': invoice, 'items': list(invoice.items.all()),
            'invoice_account_summary': summary,
            'show_discount_column': False, 'show_tax_column': False,
        })
        self.assertIn('Previous Outstanding Balance', html)
        self.assertIn('Total Amount Due', html)
        self.assertIn('Payment Received', html)
        self.assertIn('Outstanding Account Balance', html)
        self.assertEqual(summary['current_invoice_total'], Decimal('50.00'))
        self.assertEqual(summary['previous_outstanding_balance'], Decimal('80.00'))

    def test_quotation_list_uses_quotation_status_choices_only(self):
        response = self.client.get(reverse("billing:document_list", kwargs={"doc_type": "quotation"}))

        self.assertEqual(response.status_code, 200)
        status_values = [value for value, _label in response.context["status_choices"]]
        self.assertEqual(
            status_values,
            [
                BillingDocument.Status.DRAFT,
                BillingDocument.Status.SENT,
                BillingDocument.Status.ACCEPTED,
                BillingDocument.Status.REJECTED,
                BillingDocument.Status.EXPIRED,
                BillingDocument.Status.CONVERTED,
            ],
        )

    def test_promotion_list_filters_paginates_and_scopes_by_tenant(self):
        package = Package.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Fiber",
            package_type="indoor",
            speed="50 Mbps",
            monthly_fee=Decimal("100000.00"),
            setup_fee=Decimal("0.00"),
            description="Fiber package",
        )
        other_package = Package.objects.create(
            organization=self.org2,
            tenant=self.org2,
            name="Other Fiber",
            package_type="indoor",
            speed="50 Mbps",
            monthly_fee=Decimal("100000.00"),
            setup_fee=Decimal("0.00"),
            description="Other package",
        )
        for index in range(55):
            Promotion.objects.create(
                organization=self.org1,
                tenant=self.org1,
                name=f"Fiber promo {index:03d}",
                applies_to=Promotion.AppliesTo.PACKAGE,
                package=package,
                reward_type=Promotion.RewardType.PERCENT,
                reward_value=Decimal("10.00"),
            )
        Promotion.objects.create(
            organization=self.org2,
            tenant=self.org2,
            name="Fiber promo other tenant",
            applies_to=Promotion.AppliesTo.PACKAGE,
            package=other_package,
            reward_type=Promotion.RewardType.PERCENT,
            reward_value=Decimal("10.00"),
        )

        response = self.client.get(
            reverse("billing:promotion_list"),
            {"search": "Fiber promo", "applies_to": Promotion.AppliesTo.PACKAGE, "page_size": "50", "page": "2"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["promotions"]), 5)
        self.assertEqual(response.context["result_count"], 55)
        self.assertContains(response, "page_size=50")

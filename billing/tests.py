from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection, connections
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from threading import Barrier, Thread

from audit.models import AuditLog
from billing.models import BillingDocument, CustomerSubscription, Promotion, SubscriptionPeriod
from billing.numbering import DocumentNumberService
from billing.services import BillingService, BillingServiceError, ISSUED_INVOICE_EDIT_ERROR, LineItemInput, SubscriptionBillingService
from customers.models import Customer
from products.models import Product
from services.models import Package
from users.models import Organization


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
        return [
            LineItemInput(product_id=self.product_org1.id, quantity=Decimal("1.00"), unit_price=price),
            LineItemInput(package_id=self.package_org1.id, quantity=Decimal("1.00"), unit_price=Decimal("50000.00")),
        ]

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
        self.assertRegex(quotation_v1.number, r"^QUO-ORG-\d{8}-0001$")
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
        invoice_item = period.invoice.items.get()
        self.assertEqual(invoice_item.billing_behavior, invoice_item.BillingBehavior.RECURRING_MONTHLY)
        self.assertEqual(invoice_item.promotion_id, promotion.id)

        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=period.invoice_id,
            payment_date=date(2026, 4, 2),
            payment_method="cash",
        )

        period.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(period.status, SubscriptionPeriod.Status.PAID)
        self.assertEqual(period.receipt_id, receipt.id)
        self.assertEqual(subscription.paid_through_date, period.period_end)

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

    def test_credit_note_creation_references_invoice(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)

        credit_note = BillingService.create_credit_note(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
        )

        self.assertEqual(credit_note.document_type, BillingDocument.DocumentType.CREDIT_NOTE)
        self.assertEqual(credit_note.corrected_invoice_id, invoice.id)
        self.assertLess(credit_note.total, Decimal("0.00"))
        self.assertRegex(credit_note.number, r"^CRN-ORG-\d{8}-0001$")

    def test_visible_numbers_include_tenant_code_and_daily_sequence(self):
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

        self.assertEqual(quotation.number, "QUO-ORG-20260401-0001")
        self.assertEqual(invoice.number, "INV-ORG-20260401-0001")
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
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org1,
            created_by=self.user,
            quotation_id=quotation_v2.id,
        )
        BillingService.update_draft_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
            tax_rate=Decimal("15.00"),
            items=[LineItemInput(description="Edited", quantity=Decimal("1.00"), unit_price=Decimal("75.00"))],
        )
        BillingService.cancel_invoice(organization=self.org1, performed_by=self.user, invoice_id=invoice.id)
        reissued_invoice = BillingService.reissue_invoice(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=invoice.id,
        )
        credit_note = BillingService.create_credit_note(
            organization=self.org1,
            performed_by=self.user,
            invoice_id=reissued_invoice.id,
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
                "invoice_cancelled",
                "invoice_reissued",
                "credit_note_created",
            }.issubset(actions)
        )

        log = AuditLog.objects.get(action_type="invoice_reissued", document_id=str(reissued_invoice.id))
        self.assertEqual(log.performed_by_id, self.user.id)
        self.assertEqual(log.tenant_id, self.org1.id)
        self.assertEqual(log.old_value["id"], invoice.id)
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
        self.assertEqual(invoice.document_type, BillingDocument.DocumentType.INVOICE)

    def test_receipt_reference_stays_unique(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)
        BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            payment_method="cash",
            payment_reference="ref-1",
        )

        with self.assertRaises(IntegrityError):
            BillingService.create_receipt_from_invoice(
                organization=self.org1,
                created_by=self.user,
                invoice_id=invoice.id,
                payment_method="cash",
                payment_reference="ref-1",
            )

    def test_receipt_number_uses_receipt_prefix(self):
        invoice = self._create_invoice(status=BillingDocument.Status.ISSUED)

        receipt = BillingService.create_receipt_from_invoice(
            organization=self.org1,
            created_by=self.user,
            invoice_id=invoice.id,
            payment_method="cash",
            payment_reference="ref-unique",
        )

        self.assertRegex(receipt.number, r"^REC-ORG-\d{8}-0001$")

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

    def test_daily_reset_uses_issue_date(self):
        day_one = date(2026, 4, 1)
        day_two = date(2026, 4, 2)

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

        self.assertEqual(first.number, "INV-ORG-20260401-0001")
        self.assertEqual(second.number, "INV-ORG-20260401-0002")
        self.assertEqual(third.number, "INV-ORG-20260402-0001")

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

        self.assertEqual(invoice_org1.number, "INV-ORG-20260401-0001")
        self.assertEqual(invoice_org2.number, "INV-ORG-20260401-0001")

    def test_tenant_code_prefers_short_slug_when_available(self):
        org = Organization.objects.create(name="JS Internet Services", slug="js")
        self.assertEqual(DocumentNumberService.get_tenant_code(org), "JS")


class BillingNumberConcurrencyTests(TransactionTestCase):
    reset_sequences = True

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
            ["INV-ORG-20260401-0001", "INV-ORG-20260401-0002"],
        )

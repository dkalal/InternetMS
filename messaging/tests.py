from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from billing.models import BillingDocument
from customers.models import Customer
from messaging.models import MessageTemplate, WhatsAppManualMessageLog
from messaging.services import MessageBuilderService, MessagingServiceError, WhatsAppDispatcher, send_whatsapp_message
from users.models import Organization, UserAccessProfile


User = get_user_model()


class MessagingTestCase(TestCase):
    def setUp(self):
        self.org1 = Organization.objects.create(name="Tenant A", slug="tenant-a")
        self.org2 = Organization.objects.create(name="Tenant B", slug="tenant-b")
        self.user = User.objects.create_user(username="staff", password="pass")
        UserAccessProfile.objects.create(user=self.user, tenant=self.org1, role=UserAccessProfile.Role.TENANT_STAFF)
        self.customer = Customer.objects.create(
            organization=self.org1,
            tenant=self.org1,
            name="Asha",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            phone="0712345678",
            location="Moshi",
        )
        self.other_customer = Customer.objects.create(
            organization=self.org2,
            tenant=self.org2,
            name="Other",
            customer_type="internet",
            status=Customer.Status.ACTIVE,
            phone="0711111111",
            location="Arusha",
        )
        self.template = MessageTemplate.objects.create(
            name="INVOICE_NOTIFICATION",
            category=MessageTemplate.Category.INVOICE,
            content="Hello {{ customer_name }}, invoice {{ invoice_number }} is {{ amount }} due {{ due_date }}.",
            variables_schema={"optional": ["due_date"]},
        )


class MessageBuilderServiceTests(MessagingTestCase):
    def test_renders_required_variables(self):
        message = MessageBuilderService.render(
            template=self.template,
            context={
                "customer_name": "Asha",
                "invoice_number": "INV-001",
                "amount": "50,000 TZS",
                "due_date": "2026-05-05",
            },
        )

        self.assertEqual(message, "Hello Asha, invoice INV-001 is 50,000 TZS due 2026-05-05.")
        self.assertNotIn("{{", message)

    def test_missing_required_variable_raises(self):
        with self.assertRaisesMessage(MessagingServiceError, "invoice_number"):
            MessageBuilderService.render(template=self.template, context={"customer_name": "Asha"})

    def test_optional_variable_can_be_blank(self):
        message = MessageBuilderService.render(
            template=self.template,
            context={"customer_name": "Asha", "invoice_number": "INV-001", "amount": "50,000 TZS"},
        )

        self.assertEqual(message, "Hello Asha, invoice INV-001 is 50,000 TZS due .")

    def test_tenant_and_global_templates_can_share_catalog(self):
        tenant_template = MessageTemplate.objects.create(
            tenant=self.org1,
            name="TENANT_FOLLOWUP",
            category=MessageTemplate.Category.GENERAL,
            content="Hi {{ customer_name }}",
        )

        self.assertIsNone(self.template.tenant_id)
        self.assertEqual(tenant_template.tenant_id, self.org1.id)


class WhatsAppDispatcherTests(MessagingTestCase):
    def test_normalizes_tanzania_phone_numbers(self):
        self.assertEqual(WhatsAppDispatcher.normalize_phone("0712345678"), "255712345678")
        self.assertEqual(WhatsAppDispatcher.normalize_phone("712345678"), "255712345678")
        self.assertEqual(WhatsAppDispatcher.normalize_phone("+255 712 345 678"), "255712345678")

    def test_builds_encoded_wa_me_url(self):
        url = WhatsAppDispatcher.build_manual_url(
            phone="0712345678",
            message="Hello Asha, invoice INV-001\nAmount: 50,000 TZS",
        )

        self.assertTrue(url.startswith("https://wa.me/255712345678?text=Hello%20Asha%2C%20invoice"))
        self.assertIn("%0AAmount%3A%2050%2C000%20TZS", url)

    def test_rejects_invalid_phone(self):
        with self.assertRaises(MessagingServiceError):
            WhatsAppDispatcher.normalize_phone("")


class SendWhatsAppMessageTests(MessagingTestCase):
    def test_manual_mode_logs_before_returning_url(self):
        result = send_whatsapp_message(
            organization=self.org1,
            customer=self.customer,
            phone=self.customer.phone,
            message="Hello Asha",
            actor=self.user,
            template=self.template,
            related_object_type="invoice",
            related_object_id="1",
        )

        self.assertIn("https://wa.me/255712345678", result.url)
        log = WhatsAppManualMessageLog.objects.get(id=result.log.id)
        self.assertEqual(log.tenant_id, self.org1.id)
        self.assertEqual(log.status, WhatsAppManualMessageLog.Status.OPENED)
        self.assertEqual(log.phone_number, "255712345678")

    def test_rejects_cross_tenant_customer(self):
        with self.assertRaises(PermissionDenied):
            send_whatsapp_message(
                organization=self.org1,
                customer=self.other_customer,
                phone=self.other_customer.phone,
                message="Hello",
                actor=self.user,
            )

    def test_api_mode_is_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            send_whatsapp_message(
                organization=self.org1,
                customer=self.customer,
                phone=self.customer.phone,
                message="Hello",
                actor=self.user,
                mode="api",
            )


class MessagingViewTests(MessagingTestCase):
    def setUp(self):
        super().setUp()
        self.client.login(username="staff", password="pass")
        self.invoice = BillingDocument.objects.create(
            organization=self.org1,
            tenant=self.org1,
            document_type=BillingDocument.DocumentType.INVOICE,
            number="INV-001",
            customer=self.customer,
            issue_date=date(2026, 4, 1),
            due_date=date(2026, 5, 5),
            status=BillingDocument.Status.ISSUED,
            total=Decimal("50000.00"),
            currency="TZS",
        )
        self.quotation = BillingDocument.objects.create(
            organization=self.org1,
            tenant=self.org1,
            document_type=BillingDocument.DocumentType.QUOTATION,
            number="QUO-001",
            customer=self.customer,
            issue_date=date(2026, 4, 1),
            status=BillingDocument.Status.DRAFT,
            total=Decimal("70000.00"),
            currency="TZS",
        )
        self.receipt = BillingDocument.objects.create(
            organization=self.org1,
            tenant=self.org1,
            document_type=BillingDocument.DocumentType.RECEIPT,
            number="REC-001",
            customer=self.customer,
            issue_date=date(2026, 4, 2),
            status=BillingDocument.Status.PAID,
            total=Decimal("50000.00"),
            currency="TZS",
            invoice=self.invoice,
            payment_date=date(2026, 4, 2),
            payment_method="cash",
        )

    def test_staff_can_fetch_preview_and_send(self):
        response = self.client.get(reverse("messaging:template_options"), {"category": "invoice", "customer": self.customer.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["templates"][0]["name"], "INVOICE_NOTIFICATION")

        response = self.client.post(
            reverse("messaging:preview_message"),
            {"template_id": self.template.id, "doc_type": "invoice", "doc_id": self.invoice.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("INV-001", response.json()["message"])

        response = self.client.post(
            reverse("messaging:send_manual_message"),
            {
                "template_id": self.template.id,
                "doc_type": "invoice",
                "doc_id": self.invoice.id,
                "message": "Hello Asha",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("https://wa.me/255712345678", response.json()["url"])
        self.assertEqual(WhatsAppManualMessageLog.objects.filter(tenant=self.org1).count(), 1)

    def test_cross_tenant_source_is_not_found(self):
        other_doc = BillingDocument.objects.create(
            organization=self.org2,
            tenant=self.org2,
            document_type=BillingDocument.DocumentType.INVOICE,
            number="INV-OTHER",
            customer=self.other_customer,
            issue_date=date(2026, 4, 1),
            status=BillingDocument.Status.ISSUED,
        )
        response = self.client.post(
            reverse("messaging:preview_message"),
            {"template_id": self.template.id, "doc_type": "invoice", "doc_id": other_doc.id},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not found", response.json()["error"])

    def test_missing_phone_blocks_send(self):
        self.customer.phone = ""
        self.customer.save(update_fields=["phone"])
        response = self.client.post(
            reverse("messaging:send_manual_message"),
            {"customer": self.customer.id, "template_id": self.template.id, "message": "Hello"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("phone", response.json()["error"].lower())

    def test_detail_pages_render_composer_trigger(self):
        customer_response = self.client.get(reverse("customer-detail", args=[self.customer.id]))
        invoice_response = self.client.get(
            reverse("billing:document_detail", kwargs={"doc_type": "invoice", "pk": self.invoice.id})
        )
        quotation_response = self.client.get(
            reverse("billing:document_detail", kwargs={"doc_type": "quotation", "pk": self.quotation.id})
        )
        receipt_response = self.client.get(
            reverse("billing:document_detail", kwargs={"doc_type": "receipt", "pk": self.receipt.id})
        )

        for response in [customer_response, invoice_response, quotation_response, receipt_response]:
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Send via WhatsApp")
        self.assertContains(receipt_response, "Download PDF to attach")

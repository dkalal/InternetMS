from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from billing import tests as fixtures
from billing.services import BillingService, LineItemInput, SubscriptionBillingService
from users.models import UserAccessProfile


class PackageDescriptionPrintTests(TestCase):
    def setUp(self):
        fixtures.BillingServiceTests.setUp(self)
        UserAccessProfile.objects.create(
            user=self.user, tenant=self.org1, role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)
        subscription = SubscriptionBillingService.get_or_create_subscription(
            organization=self.org1, customer=self.customer_org1, package=self.package_org1,
            start_date=date(2026, 4, 1),
        )
        self.period = SubscriptionBillingService.renew(
            organization=self.org1, created_by=self.user, subscription_id=subscription.pk,
            period_start=date(2026, 4, 1), months=3,
        )
        self.invoice = self.period.invoice
        self.line = self.invoice.items.get()
        self.url = reverse('billing:document_pdf', args=['invoice', self.invoice.pk])

    def render(self, template, *, item=None):
        return render_to_string(template, {
            'document': self.invoice, 'items': [item or self.line],
            'show_discount_column': False, 'show_tax_column': False,
        })

    def test_saved_service_period_is_visible_in_both_print_templates_without_mutation(self):
        before = (self.invoice.total, self.invoice.tax_amount, self.line.description)
        self.assertIn('01 Apr 2026 - 30 Jun 2026', self.line.description)
        for template in ('billing/sales_document_print.html', 'billing/document_print.html'):
            with self.subTest(template=template):
                self.assertIn(self.line.description, self.render(template))
        self.invoice.refresh_from_db()
        self.line.refresh_from_db()
        self.assertEqual(before, (self.invoice.total, self.invoice.tax_amount, self.line.description))

    def test_actual_pdf_contains_package_and_inclusive_service_dates(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        text = ' '.join(' '.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages).split())
        self.assertIn('10 Mbps', text)
        self.assertIn('01 Apr 2026 - 30 Jun 2026', text)

    @patch('billing.pdf._load_weasyprint', return_value=None)
    def test_html_fallback_shows_saved_dates_but_not_internal_notes(self, _backend):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.line.description)
        self.assertNotContains(response, self.invoice.notes)

    def test_description_is_escaped_and_duplicate_or_empty_copy_is_omitted(self):
        for template in ('billing/sales_document_print.html', 'billing/document_print.html'):
            with self.subTest(template=template):
                self.line.description = '<script>alert("unsafe")</script>\nSaved service dates'
                html = self.render(template)
                self.assertIn('&lt;script&gt;', html)
                self.assertNotIn('<script>alert(', html)
                for description in ('', self.package_org1.name):
                    self.line.description = description
                    self.assertEqual(self.render(template).count(self.package_org1.name), 1)

    def test_existing_legacy_description_is_preserved_without_inventing_dates(self):
        self.line.description = 'Billing for the Business (10 Mbps) - August 2026'
        for template in ('billing/sales_document_print.html', 'billing/document_print.html'):
            with self.subTest(template=template):
                html = self.render(template)
                self.assertIn(self.line.description, html)
                self.assertNotIn('01 Aug 2026', html)

    def test_foreign_tenant_invoice_is_not_exposed(self):
        foreign = BillingService.create_document(
            organization=self.org2, created_by=self.user, document_type='invoice',
            customer_id=self.customer_org2.pk,
            items=[LineItemInput(description='PRIVATE OTHER TENANT', unit_price=Decimal('100'))],
        )
        response = self.client.get(reverse('billing:document_pdf', args=['invoice', foreign.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'PRIVATE OTHER TENANT', status_code=404)

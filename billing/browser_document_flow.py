"""Optional Edge regression against synthetic, isolated test data."""
from decimal import Decimal
import re

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from billing.services import BillingService
from billing import tests_document_flow as fixtures


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class DocumentEditorBrowserTests(StaticLiveServerTestCase):
    setUp = fixtures.DocumentFlowTests.setUp
    quotation = fixtures.DocumentFlowTests.quotation

    def test_editor_matches_discounted_invoice_without_repricing_or_counting_blank_row(self):
        quotation = self.quotation()
        invoice = BillingService.create_invoice_from_quotation(
            organization=self.org, created_by=self.user, quotation_id=quotation.pk,
        )
        self.product.selling_price = Decimal('150')
        self.product.save(update_fields=['selling_price'])
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.route('https://**/*', lambda route: route.abort())
            context.add_cookies([{
                'name': settings.SESSION_COOKIE_NAME, 'value': self.client.session.session_key,
                'url': self.live_server_url,
            }])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + reverse('billing:document_edit', args=['invoice', invoice.pk]),
                      wait_until='domcontentloaded')
            expect(page.locator('#summary-count')).to_have_text('1')
            expect(page.locator('#summary-subtotal')).to_have_text('190')
            expect(page.locator('#summary-document-discount')).to_have_text('5.00')
            expect(page.locator('#summary-tax')).to_have_text('18.5')
            expect(page.locator('#summary-total')).to_have_text('203.5')
            expect(page.locator('[name="items-0-unit_price"]')).to_have_value(re.compile(r'^100(?:\.00)?$'))
            page.locator('[name="items-0-discount_amount"]').fill('12')
            expect(page.locator('#summary-total')).to_have_text('201.3')
            expect(page.locator('[name="items-0-unit_price"]')).to_have_value(re.compile(r'^100(?:\.00)?$'))
            self.assertEqual(errors, [])
            browser.close()

"""Actual browser gates for separate physical returns and measured wastage."""
import os
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright, expect
from .tests_packaging_receipts import ReceiptFixture
from . import tests_packaging_sales as sale_tests
from .models import ContainerAllocation, ContainerStockEvent, InventoryBalance


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class PackagingCorrectionBrowser(ReceiptFixture, StaticLiveServerTestCase):
    invoice = sale_tests.PackagingSaleTests.invoice
    pay = sale_tests.PackagingSaleTests.pay

    @patch('products.packaging.workflows_ready', return_value=True)
    def test_return_then_wastage_keeps_separate_piece_and_mobile_layout(self, ready):
        self.line(); self.receive()
        invoice = self.invoice('50'); self.pay(invoice)
        allocation = ContainerAllocation.objects.get()
        self.client.force_login(self.actor)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_BROWSER_CHANNEL', 'msedge') or None, headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            cache = Path(settings.BASE_DIR) / '.backups/cart-checkout/tailwind.js'
            if cache.exists():
                context.route('https://cdn.tailwindcss.com/**', lambda route: route.fulfill(path=str(cache), content_type='application/javascript'))
                context.route('https://cdn.tailwindcss.com', lambda route: route.fulfill(path=str(cache), content_type='application/javascript'))
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.session.session_key, 'url': self.live_server_url}])
            page = context.new_page(); errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + reverse('inventory:invoice_physical_return', args=[invoice.pk]))
            page.locator('[name=allocation]').select_option(str(allocation.pk))
            page.locator('[name=quantity]').fill('20')
            page.locator('[name=reason]').fill('Inspected and measured returned cable')
            page.get_by_role('button', name='Record physical stock change').click()
            page.wait_for_url('**/containers/', timeout=10000)
            page.goto(self.live_server_url + reverse('inventory:packaging_containers', args=[self.product.pk]) + '?state=returned_piece')
            page.get_by_role('link', name='Record wastage').click()
            page.locator('[name=quantity]').fill('5')
            page.locator('[name=reason]').fill('Measured damaged end of returned cable')
            page.get_by_role('button', name='Record physical stock change').click()
            page.wait_for_url('**/containers/', timeout=10000)
            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            self.assertEqual(errors, [])
            output = Path(settings.BASE_DIR) / '.backups/packaging-corrections'
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            browser.close()
        returned = ContainerStockEvent.objects.get(kind='return').container
        self.assertEqual(returned.remaining_quantity, 15)
        self.assertEqual(returned.state, 'returned_piece')
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, 3015)


from .tests_packaging_foundation import PackagingFixture


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class PackagingAdministrationBrowser(PackagingFixture, StaticLiveServerTestCase):
    def test_unpatched_configuration_count_rows_and_explicit_zero_stock_activation(self):
        self.client.force_login(self.actor)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_BROWSER_CHANNEL', 'msedge') or None, headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            cache = Path(settings.BASE_DIR) / '.backups/cart-checkout/tailwind.js'
            if cache.exists():
                context.route('https://cdn.tailwindcss.com/**', lambda route: route.fulfill(path=str(cache), content_type='application/javascript'))
                context.route('https://cdn.tailwindcss.com', lambda route: route.fulfill(path=str(cache), content_type='application/javascript'))
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.session.session_key, 'url': self.live_server_url}])
            page = context.new_page(); errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + reverse('product-packaging', args=[self.product.pk]))
            page.locator('[name=configuration-unit]').select_option(str(self.box.pk))
            page.locator('[name=configuration-factor]').fill('305')
            page.locator('[name=configuration-standard_price]').fill('900')
            page.get_by_role('button', name='Add revision', exact=True).click()
            expect(page.get_by_text('package reference', exact=False)).to_be_visible()
            page.get_by_role('button', name='Add physical piece', exact=True).click()
            expect(page.locator('[name=counted-TOTAL_FORMS]')).to_have_value('2')
            page.locator('[name=activation-reason]').fill('Verified empty warehouse; no physical stock')
            page.get_by_role('button', name='Activate with exact physical count', exact=True).click()
            expect(page.get_by_text('Disabling requires zero stock', exact=False)).to_be_visible()
            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            self.assertEqual(errors, [])
            output = Path(settings.BASE_DIR) / '.backups/packaging-corrections'
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / 'activation-mobile.png'), full_page=True)
            browser.close()
        self.product.refresh_from_db()
        self.assertTrue(self.product.packaging_enabled)

@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class DirectBillingUnitBrowser(ReceiptFixture, StaticLiveServerTestCase):
    def test_direct_document_loads_product_specific_unit_choices(self):
        self.line(); self.receive()
        expected_label = f'{self.package.unit.label} ({self.package.factor:g} {self.product.sales_unit.label})'
        self.client.force_login(self.actor)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_BROWSER_CHANNEL', 'msedge') or None, headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            cache = Path(settings.BASE_DIR) / '.backups/cart-checkout/tailwind.js'
            if cache.exists():
                context.route('https://cdn.tailwindcss.com/**', lambda route: route.fulfill(path=str(cache), content_type='application/javascript'))
                context.route('https://cdn.tailwindcss.com', lambda route: route.fulfill(path=str(cache), content_type='application/javascript'))
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.session.session_key, 'url': self.live_server_url}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + reverse('billing:document_create', args=[getattr(self, 'document_type', 'invoice')]))
            page.get_by_role('button', name='Items', exact=True).click()
            row = page.locator('.js-line-item').first
            row.locator('select[name$="-product"]').select_option(str(self.product.pk), force=True)
            field = row.locator('[name$="-commercial_unit"]')
            expect(field).to_be_visible()
            expect(field.locator('option')).to_have_count(3)
            field.select_option(label=expected_label)
            row.locator('[name$="-quantity"]').fill('2')
            page.get_by_role('button', name='Document details', exact=True).click()
            page.locator('[name="customer"]').select_option(str(self.customer.pk), force=True)
            page.locator('[name="sale_pricing_category"]').select_option('standard')
            page.locator('[name="tax_rate"]').fill('0')
            doc_type = getattr(self, 'document_type', 'invoice')
            page.get_by_role('button', name=f'Create {doc_type}', exact=True).click()
            page.wait_for_url(f'**/billing/{doc_type}/*/', timeout=10000)
            if doc_type == 'quotation':
                page.get_by_role('button', name='Create invoice', exact=True).click()
                page.wait_for_url('**/billing/invoice/*/', timeout=10000)
            invoice_id = int(page.url.rstrip('/').split('/')[-1])
            page.get_by_role('link', name='Review physical pieces', exact=True).click()
            pieces = page.locator('input[name^="line_"][name*="_container_"]')
            expect(pieces).to_have_count(10)
            pieces.nth(0).fill('305')
            pieces.nth(1).fill('305')
            page.locator('[name="issue_invoice"]').check()
            page.get_by_role('button', name='Save reviewed selections', exact=True).click()
            page.wait_for_url('**/billing/invoice/*/', timeout=10000)

            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            self.assertEqual(errors, [])
            browser.close()
        from billing.models import BillingDocument
        document = BillingDocument.objects.get(pk=invoice_id)
        self.assertEqual(document.status, 'issued')
        line = document.items.get()
        self.assertEqual(line.quantity, 2)
        self.assertEqual(line.base_quantity, 610)
        self.assertEqual(line.unit_snapshot, 'Box')
        self.assertEqual(line.unit_price, 900)
        self.assertEqual(line.container_selections.count(), 2)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, 3050)


class DirectQuotationUnitBrowser(DirectBillingUnitBrowser):
    document_type = 'quotation'

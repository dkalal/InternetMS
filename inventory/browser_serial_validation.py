"""Optional real Edge checks; use an isolated test database."""
import re
from datetime import date
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright, expect

from . import tests_purchase_product_search as fixtures
from .models import StockUnit, Supplier, Purchase


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class SerialFeedbackBrowserChecks(StaticLiveServerTestCase):
    make_product = fixtures.PurchaseProductSearchTests.make_product

    def setUp(self):
        fixtures.PurchaseProductSearchTests.setUp(self)
        self.client.force_login(self.admin)
        StockUnit.objects.create(tenant=self.organization, product=self.product, serial_number='OLD-001')
        self.supplier = Supplier.objects.create(tenant=self.organization, organization=self.organization,
                                               company_name='Live test supplier', created_by=self.admin)

    def browser(self, playwright):
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.session.session_key,
                             'url': self.live_server_url}])
        return browser, context.new_page()

    def visit(self, page, url):
        page.goto(self.live_server_url + url)
        page.add_style_tag(content='.hidden { display: none !important; }')

    def select_product(self, page, row):
        row.locator('input[type="search"]').fill(self.product.sku)
        page.get_by_role('option').filter(has_text=self.product.name).click()

    def test_purchase_duplicates_live_across_drawer_rows_and_deleted_row(self):
        with sync_playwright() as playwright:
            browser, page = self.browser(playwright)
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            self.visit(page, reverse('inventory:purchase_create'))
            first = page.locator('[data-purchase-line]').first
            self.select_product(page, first)
            first.locator('[data-open-line-details]').click()
            serial1 = page.locator('[name="lines-0-serial_numbers"]')
            serial1.fill('DUP-001\ndup-001')
            expect(serial1).to_have_attribute('aria-invalid', 'true')
            expect(page.locator('form > [data-live-serial-status]')).to_contain_text('DUP-001 is repeated')
            page.locator('[name=supplier]').select_option(str(self.supplier.pk), force=True)
            page.locator('[name=purchase_date]').fill(date.today().isoformat())
            first.locator('[name$="-quantity"]').fill('2')
            page.locator('[data-close-line-details]').click()
            page.locator('button[value=save_review]').click()
            expect(serial1).to_be_visible()
            expect(page.locator('button[value=save_review]')).to_be_enabled()
            serial1.fill('SN-A')
            expect(serial1).not_to_have_attribute('aria-invalid', 'true')
            page.locator('[data-add-purchase-line]').click()
            second = page.locator('[data-purchase-line]').nth(1)
            self.select_product(page, second)
            second.locator('[data-open-line-details]').click()
            serial2 = page.locator('[name="lines-1-serial_numbers"]')
            serial2.fill('sn-a')
            expect(serial2).to_have_attribute('aria-invalid', 'true')
            expect(page.locator('form > [data-live-serial-status]')).to_contain_text('Line 1, Line 2')
            first.locator('[data-remove-line]').click()
            expect(serial2).not_to_have_attribute('aria-invalid', 'true')
            serial2.fill('old-001')
            expect(page.locator('form > [data-live-serial-status]')).to_contain_text('already exists')
            serial2.fill('SN-NEW')
            expect(serial2).not_to_have_attribute('aria-invalid', 'true')
            second.locator('[name$="-quantity"]').fill('1')
            page.locator('button[value=save_review]').click()
            page.wait_for_url(re.compile(r'/inventory/purchases/\d+/$'))
            self.assertEqual(errors, [])
            browser.close()
        purchase = Purchase.objects.get()
        self.assertEqual(purchase.lines.get().serial_numbers, 'SN-NEW')
        self.assertEqual(StockUnit.objects.count(), 1)

    def test_stock_increase_and_decrease_have_different_existing_serial_rules(self):
        with sync_playwright() as playwright:
            browser, page = self.browser(playwright)
            self.visit(page, reverse('inventory:stock_adjust'))
            page.locator('[name=product]').select_option(str(self.product.pk), force=True)
            page.locator('[name=direction][value=increase]').check()
            serials = page.locator('[name=serial_numbers]')
            serials.fill('OLD-001')
            expect(serials).to_have_attribute('aria-invalid', 'true')
            page.locator('[name=direction][value=decrease]').check()
            expect(serials).not_to_have_attribute('aria-invalid', 'true')
            serials.fill('OLD-001,old-001')
            expect(serials).to_have_attribute('aria-invalid', 'true')
            browser.close()

    def test_import_editor_detects_cross_row_duplicates_while_typing(self):
        with sync_playwright() as playwright:
            browser, page = self.browser(playwright)
            self.visit(page, reverse('inventory:purchase_create'))
            page.locator('.purchase-more summary').click()
            page.locator('[data-open-paste-rows]').click()
            page.get_by_role('tab', name='Paste from spreadsheet').click()
            page.locator('[data-paste-rows-input]').fill('sku\tquantity\tunit_cost\tserial_numbers\n'
                + self.product.sku + '\t1\t100\tIMP-001\n' + self.product.sku + '\t1\t100\tIMP-002')
            page.locator('[data-import-primary]').click()
            page.locator('[data-import-results] .import-row-open').nth(1).click()
            page.locator('[data-import-editor] summary').filter(has_text='Purchase details').click()
            serials = page.locator('[data-field=serial_numbers]')
            serials.fill('imp-001')
            expect(serials).to_have_attribute('aria-invalid', 'true')
            expect(page.locator('[data-paste-rows-dialog] [data-live-serial-status]')).to_contain_text('IMP-001 is repeated')
            serials.fill('IMP-003')
            expect(serials).not_to_have_attribute('aria-invalid', 'true')
            browser.close()

    def test_invoice_checkbox_duplicate_is_live_and_clears_when_corrected(self):
        from decimal import Decimal
        from billing.models import BillingDocument
        from billing.services import BillingService, LineItemInput
        from customers.models import Customer
        from .models import InventoryBalance
        customer = Customer.objects.create(tenant=self.organization, organization=self.organization,
                                           name='Live invoice customer', location='Moshi', customer_type='random')
        InventoryBalance.objects.create(tenant=self.organization, organization=self.organization,
                                        product=self.product, quantity=2)
        unit2 = StockUnit.objects.create(tenant=self.organization, product=self.product, serial_number='OLD-002')
        unit1 = StockUnit.objects.get(serial_number='OLD-001')
        invoice = BillingService.create_document(
            organization=self.organization, created_by=self.admin, document_type=BillingDocument.DocumentType.INVOICE,
            customer_id=customer.pk, status=BillingDocument.Status.ISSUED,
            items=[LineItemInput(product_id=self.product.pk, quantity=Decimal('1')),
                   LineItemInput(product_id=self.product.pk, quantity=Decimal('1'))],
        )
        with sync_playwright() as playwright:
            browser, page = self.browser(playwright)
            self.visit(page, reverse('inventory:invoice_serials', args=[invoice.pk]))
            same = page.locator('[data-serial-selection][value="' + str(unit1.pk) + '"]')
            same.first.check(); same.nth(1).check()
            expect(same.first).to_have_attribute('aria-invalid', 'true')
            expect(page.locator('form [role=status]')).to_contain_text('more than one invoice line')
            same.nth(1).uncheck()
            page.locator('[data-serial-selection][value="' + str(unit2.pk) + '"]').nth(1).check()
            expect(same.first).not_to_have_attribute('aria-invalid', 'true')
            browser.close()

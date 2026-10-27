"""Optional authenticated Edge checks for the inventory report workspace."""
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import sync_playwright, expect

from . import tests_reporting as fixtures


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class InventoryReportBrowserChecks(StaticLiveServerTestCase):
    purchase = fixtures.InventoryReportTests.purchase
    url = fixtures.InventoryReportTests.url

    def setUp(self):
        fixtures.InventoryReportTests.setUp(self)
        self.purchase(count=51)

    def test_desktop_mobile_filters_totals_export_and_validation(self):
        output = Path(settings.BASE_DIR) / '.backups' / 'inventory-reports'
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                'value': self.client.session.session_key, 'domain': 'localhost', 'path': '/'}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + self.url())
            page.locator('#id_supplier').select_option(str(self.supplier.pk), force=True)
            page.get_by_label('From date').fill('2026-10-01')
            page.get_by_label('To date').fill('2026-10-01')
            page.get_by_role('button', name='Apply filters', exact=True).click()
            self.assertEqual(page.locator('td.report-number').first.evaluate('(el) => getComputedStyle(el).textAlign'), 'right')
            expect(page.get_by_text('Total purchases (TZS)', exact=True)).to_be_visible()
            expect(page.get_by_text('1,032.75', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'desktop.png'), full_page=True)
            page.get_by_role('link', name='Next', exact=True).click()
            expect(page.locator('#id_supplier')).to_have_value(str(self.supplier.pk))
            expect(page.get_by_text('1,032.75', exact=True)).to_be_visible()
            with page.expect_download() as download:
                page.get_by_role('link', name='Export Excel', exact=True).click()
            self.assertEqual(download.value.suggested_filename, 'purchases.xlsx')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.get_by_role('combobox', name='Supplier', exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 391)
            page.get_by_label('To date').fill('2026-09-30')
            page.get_by_role('button', name='Apply filters', exact=True).click()
            expect(page.get_by_role('alert').filter(has_text='Report not generated')).to_be_visible()
            expect(page.get_by_text('Total purchases (TZS)', exact=True)).to_have_count(0)
            page.screenshot(path=str(output / 'invalid-filters.png'), full_page=True)
            self.assertEqual(errors, [])
            browser.close()

    def test_search_scope_stock_breakdown_and_loss_review(self):
        from datetime import date
        from django.utils import timezone
        from billing.models import BillingDocument, BillingLineItem
        from customers.models import Customer
        from .models import InventoryBalance, StockMovement, InventorySale, InventorySaleLine
        InventoryBalance.objects.create(tenant=self.org, product=self.product, quantity=8, average_cost=10)
        StockMovement.objects.create(tenant=self.org, product=self.product, movement_type='opening', quantity=10, balance_after=10, unit_cost=10)
        StockMovement.objects.create(tenant=self.org, product=self.product, movement_type='sale_out', quantity=-2, balance_after=8, unit_cost=10)
        customer = Customer.objects.create(tenant=self.org, organization=self.org, name='Buyer', customer_type='random', location='Moshi')
        invoice = BillingDocument.objects.create(tenant=self.org, organization=self.org, customer=customer, document_type='invoice', number='BROWSER-LOSS', issue_date=date(2026, 10, 1), created_by=self.admin)
        line = BillingLineItem.objects.create(tenant=self.org, organization=self.org, document=invoice, product=self.product, description='Historical router', quantity=1, unit_price=20)
        sale = InventorySale.objects.create(tenant=self.org, invoice=invoice, stock_deducted=True, completed_at=timezone.now())
        InventorySaleLine.objects.create(tenant=self.org, sale=sale, billing_line=line, net_revenue=20, cost_total=30)
        output = Path(settings.BASE_DIR) / '.backups' / 'inventory-reports'
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.session.session_key, 'domain': 'localhost', 'path': '/'}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + self.url())
            page.get_by_label('Search report', exact=True).fill('5')
            page.get_by_role('button', name='Apply filters').click()
            expect(page.get_by_role('heading', name='No matching records')).to_be_visible()
            expect(page.get_by_label('Search in', exact=True)).to_have_count(0)
            page.get_by_label('Search report', exact=True).fill('REPORT-1')
            page.get_by_role('button', name='Apply filters').click()
            expect(page.get_by_text('51 records', exact=False)).to_be_visible()
            page.goto(self.live_server_url + self.url('stock-valuation'))
            page.get_by_text('How is this stock value calculated?', exact=True).click()
            expect(page.get_by_text('Opening stock', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'stock-breakdown.png'), full_page=True)
            page.goto(self.live_server_url + self.url('gross-profit'))
            expect(page.get_by_role('status').filter(has_text='1 loss-making line')).to_be_visible()
            expect(page.locator('td.report-loss')).to_have_text('-10')
            page.get_by_label('Profit result').select_option('loss')
            page.get_by_role('button', name='Apply filters').click()
            page.screenshot(path=str(output / 'gross-profit-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(output / 'gross-profit-mobile.png'), full_page=True)
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 391)
            page.goto(self.live_server_url + self.url('historical-purchases'))
            expect(page.get_by_role('heading', name='No imported records match this report')).to_be_visible()
            self.assertEqual(errors, [])
            browser.close()

    def test_simple_search_without_scope_selector(self):
        import os
        output = Path(settings.BASE_DIR) / 'docs' / 'screenshots' / 'report-simple-search'
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 960}, ignore_https_errors=True)
            cached = os.environ.get('PRODUCT_BROWSER_TAILWIND_PATH')
            if cached:
                context.route('https://cdn.tailwindcss.com/', lambda route: route.fulfill(path=cached, content_type='application/javascript'))
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.session.session_key, 'domain': 'localhost', 'path': '/'}])
            page = context.new_page()
            page.goto(self.live_server_url + self.url())
            expect(page.get_by_label('Search in', exact=True)).to_have_count(0)
            page.get_by_label('Search report', exact=True).fill('5')
            page.get_by_role('button', name='Apply filters').click()
            expect(page.get_by_role('heading', name='No matching records')).to_be_visible()
            page.get_by_label('Search report', exact=True).fill('REPORT-1')
            page.get_by_role('button', name='Apply filters').click()
            expect(page.get_by_text('51 records', exact=False)).to_be_visible()
            page.screenshot(path=str(output / 'desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 390)
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            browser.close()

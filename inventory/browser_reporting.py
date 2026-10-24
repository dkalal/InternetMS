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
            page.get_by_label('Supplier', exact=True).select_option(str(self.supplier.pk))
            page.get_by_label('From date').fill('2026-10-01')
            page.get_by_label('To date').fill('2026-10-01')
            page.get_by_role('button', name='Apply', exact=True).click()
            expect(page.get_by_text('Total purchases (TZS)', exact=True)).to_be_visible()
            expect(page.get_by_text('1,032.75', exact=True)).to_be_visible()
            page.screenshot(path=str(output / 'desktop.png'), full_page=True)
            page.get_by_role('link', name='Next', exact=True).click()
            expect(page.get_by_label('Supplier', exact=True)).to_have_value(str(self.supplier.pk))
            expect(page.get_by_text('1,032.75', exact=True)).to_be_visible()
            with page.expect_download() as download:
                page.get_by_role('link', name='Export Excel', exact=True).click()
            self.assertEqual(download.value.suggested_filename, 'purchases.xlsx')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.get_by_label('Supplier', exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 391)
            page.get_by_label('To date').fill('2026-09-30')
            page.get_by_role('button', name='Apply', exact=True).click()
            expect(page.get_by_role('alert').filter(has_text='Report not generated')).to_be_visible()
            expect(page.get_by_text('Total purchases (TZS)', exact=True)).to_have_count(0)
            page.screenshot(path=str(output / 'invalid-filters.png'), full_page=True)
            self.assertEqual(errors, [])
            browser.close()

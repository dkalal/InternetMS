"""Optional Edge smoke check: python manage.py test customers.browser_balance_summary."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from django.conf import settings
from django.db import connections
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright, expect
from . import tests_balance_summary as fixtures


def database_call(func):
    try:
        return func()
    finally:
        connections.close_all()


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class BalanceSummaryBrowserChecks(StaticLiveServerTestCase):
    doc = fixtures.CustomerBalanceSummaryTests.doc
    grant = fixtures.CustomerBalanceSummaryTests.grant

    def setUp(self):
        fixtures.CustomerBalanceSummaryTests.setUp(self)
        self.client.force_login(self.sales)

    def test_sales_summary_desktop_mobile_and_revocation(self):
        output = Path(settings.BASE_DIR) / '.backups' / 'sales-balance'
        output.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=1) as executor, sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,
                'value':self.client.session.session_key,'domain':'localhost','path':'/'}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + reverse('customer-detail', kwargs={'pk':self.customer.pk}))
            summary = page.locator('section[aria-labelledby="customer-balance-heading"]')
            expect(summary).to_contain_text('Contact Accounts')
            expect(summary.get_by_text('Outstanding', exact=True)).to_have_count(0)
            grant = executor.submit(database_call, self.grant).result()
            page.reload()
            expect(summary).to_contain_text('Outstanding')
            expect(summary).to_contain_text('100.00')
            expect(summary).to_contain_text('Overdue')
            expect(page.get_by_text(self.invoice.number, exact=True)).to_have_count(0)
            page.screenshot(path=str(output/'desktop.png'), full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            summary.scroll_into_view_if_needed()
            expect(summary).to_be_visible()
            box = summary.bounding_box()
            self.assertGreaterEqual(box['x'], 0)
            self.assertLessEqual(box['x']+box['width'], 390)
            page.screenshot(path=str(output/'mobile.png'), full_page=True)
            executor.submit(database_call, grant.delete).result()
            page.reload()
            expect(summary.get_by_text('Outstanding', exact=True)).to_have_count(0)
            self.assertEqual(errors, [])
            browser.close()

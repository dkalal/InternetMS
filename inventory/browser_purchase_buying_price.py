"""Optional real Edge regression for purchase buying-price defaults."""
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright, expect

from . import tests_purchase_product_search as fixtures


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class PurchaseBuyingPriceBrowserChecks(StaticLiveServerTestCase):
    make_product = fixtures.PurchaseProductSearchTests.make_product

    def setUp(self):
        fixtures.PurchaseProductSearchTests.setUp(self)
        self.client.force_login(self.admin)

    def test_selection_edit_and_quantity_preserve_expected_cost(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context()
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                                 'value': self.client.session.session_key, 'url': self.live_server_url}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + reverse('inventory:purchase_create'))
            # Keep this behavior check independent of the external Tailwind CDN.
            page.add_style_tag(content='.hidden { display: none !important; }')
            row = page.locator('[data-purchase-line]').first
            search = row.locator('input[type="search"]')
            search.fill(self.product.sku)
            page.get_by_role('option').filter(has_text=self.product.name).click()
            cost = row.locator('input[name$="-unit_cost"]')
            expect(cost).to_have_value('100')
            expect(row.locator('[data-buying-price-hint]')).to_contain_text('Buying: TZS 100 /')
            cost.fill('87.123456')
            row.locator('input[name$="-quantity"]').fill('3')
            expect(cost).to_have_value('87.123456')
            self.assertEqual(errors, [])
            browser.close()

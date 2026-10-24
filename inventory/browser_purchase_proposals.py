"""Optional actual Edge check for the draft-proposal receiving lifecycle."""
from pathlib import Path
import re
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright, expect
from products.models import Product
from . import tests_purchase_mixed_import as fixtures
from .models import Purchase, StockMovement, Cart


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class PurchaseProposalBrowserChecks(StaticLiveServerTestCase):
    rows = fixtures.MixedPurchaseImportTests.rows
    payload = fixtures.MixedPurchaseImportTests.payload

    def setUp(self):
        fixtures.MixedPurchaseImportTests.setUp(self)
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(self.rows()))
        self.assertEqual(response.status_code, 302)
        self.purchase = Purchase.objects.get()
        self.cart = Cart.objects.create(organization=self.tenant, tenant=self.tenant, created_by=self.user)

    def test_desktop_mobile_review_edit_and_receiving(self):
        output = Path(settings.BASE_DIR) / '.backups' / 'purchase-proposals'
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                'value': self.client.session.session_key, 'domain': 'localhost', 'path': '/'}])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + reverse('inventory:cart_detail', args=[self.cart.pk]))
            expect(page.locator('[data-pos-product-card]').filter(has_text='Router')).to_have_count(1)
            expect(page.locator('[data-pos-product-card]').filter(has_text='New radio')).to_have_count(0)
            page.screenshot(path=str(output / 'cart-draft.png'), full_page=True)
            page.goto(self.live_server_url + reverse('product-list'))
            expect(page.get_by_text('New radio', exact=True)).to_have_count(0)
            page.goto(self.live_server_url + reverse('inventory:purchase_detail', args=[self.purchase.pk]))
            expect(page.get_by_text('Proposed — created on receiving')).to_be_visible()
            page.screenshot(path=str(output / 'desktop.png'), full_page=True)
            page.set_viewport_size({'width':390, 'height':844})
            page.get_by_text('Proposed — created on receiving').scroll_into_view_if_needed()
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            page.goto(self.live_server_url + reverse('inventory:purchase_edit', args=[self.purchase.pk]))
            expect(page.locator('[data-import-rows]')).to_have_value(re.compile('New radio'))
            page.goto(self.live_server_url + reverse('inventory:purchase_detail', args=[self.purchase.pk]) + '?receive=1')
            page.get_by_role('button', name='Confirm & Receive Stock', exact=True).click()
            expect(page.get_by_text('Stock received.', exact=True)).to_be_visible()
            page.goto(self.live_server_url + reverse('inventory:cart_detail', args=[self.cart.pk]))
            expect(page.locator('[data-pos-product-card]').filter(has_text='New radio')).to_have_count(1)
            page.screenshot(path=str(output / 'cart-received.png'), full_page=True)
            self.assertEqual(errors, [])
            browser.close()
        self.assertEqual(Product.objects.count(), 2)
        self.assertEqual(StockMovement.objects.count(), 2)
        self.assertFalse(self.purchase.product_proposals.exists())

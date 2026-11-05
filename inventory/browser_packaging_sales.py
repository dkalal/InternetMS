"""Real browser regression for explicit commercial-unit and physical-piece entry."""
import os
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright, expect
from .tests_packaging_receipts import ReceiptFixture
from .models import Cart, InventoryBalance, StockContainer


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class PackagingSaleBrowser(ReceiptFixture, StaticLiveServerTestCase):
    @patch('products.packaging.workflows_ready', return_value=True)
    def test_commercial_unit_piece_review_and_mobile_layout(self, ready):
        self.line(); self.receive()
        cart = Cart.objects.create(tenant=self.org, organization=self.org, created_by=self.actor, customer=self.customer)
        container = StockContainer.objects.order_by('pk').first()
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
            page.goto(self.live_server_url + reverse('inventory:cart_line_create', args=[cart.pk]) + f'?product={self.product.pk}')
            expect(page.get_by_text('Selections reserve no stock.', exact=False)).to_be_visible()
            page.locator('[name=sale_packaging]').select_option(str(self.package.pk))
            page.locator('[name=quantity]').fill('1')
            page.locator(f'[name=container_{container.pk}]').fill('305')
            page.get_by_role('button', name='Save item', exact=True).click()
            page.wait_for_url('**/carts/' + str(cart.pk) + '/', timeout=10000)
            expect(page.get_by_role('link', name='Review unit and physical pieces')).to_be_visible()
            page.set_viewport_size({'width': 390, 'height': 844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            self.assertEqual(errors, [])
            browser.close()
        self.assertEqual(cart.lines.get().base_quantity, 305)
        self.assertEqual(cart.lines.get().unit_price, 900)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, 3050)

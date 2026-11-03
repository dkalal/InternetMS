"""Optional Edge checks using synthetic rows in an isolated test database."""
import os
from pathlib import Path
import re

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from . import tests_cart_lifecycle as fixtures
from .models import Cart, InventoryBalance, StockUnit
from billing.models import BillingDocument


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class CartWorkspaceBrowserChecks(fixtures.CartFixture, StaticLiveServerTestCase):
    def browser(self, playwright):
        browser = playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_BROWSER_CHANNEL', 'msedge') or None, headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        cached = Path(settings.BASE_DIR) / '.backups/cart-checkout/tailwind.js'
        if cached.exists():
            for pattern in ('https://cdn.tailwindcss.com', 'https://cdn.tailwindcss.com/**'):
                context.route(pattern, lambda route: route.fulfill(path=str(cached), content_type='application/javascript'))
        context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                             'value': self.client.session.session_key, 'url': self.live_server_url}])
        page = context.new_page()
        return browser, page

    def open_workspace(self, page):
        page.goto(self.live_server_url + reverse('inventory:cart_list'))
        page.get_by_role('button', name='Start sale', exact=True).first.click()
        page.wait_for_url('**/inventory/carts/workspace/**')
        expect(page.get_by_text('A draft is saved when you add an item or save sale details.')).to_be_visible()

    def test_first_add_then_aligned_checkout_buttons_desktop_mobile_and_receipt(self):
        self.product.item_type = 'service'
        self.product.save(update_fields=['item_type'])
        output = Path(settings.BASE_DIR) / '.backups/pos-workspace'
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser, page = self.browser(playwright)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            self.open_workspace(page)
            page.screenshot(path=str(output / 'empty-workspace.png'), full_page=True)
            page.get_by_role('button', name='Add Router to sale', exact=True).click()
            page.wait_for_url(re.compile(r'/inventory/carts/\d+/$'))
            for size, label in (({'width':1440,'height':1000}, 'desktop'), ({'width':390,'height':844}, 'mobile')):
                page.set_viewport_size(size)
                if size['width'] < 1200:
                    page.locator('[data-cart-tab=cart]').click()
                quotation = page.get_by_role('button', name='Save as quotation', exact=True)
                payment = page.get_by_role('button', name='Confirm payment & view receipt', exact=True)
                payment.scroll_into_view_if_needed()
                q, p = quotation.bounding_box(), payment.bounding_box()
                self.assertLess(abs(q['y'] - p['y']), 2)
                self.assertLess(abs(q['height'] - p['height']), 2)
                self.assertLess(q['x'], p['x'])
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
                page.screenshot(path=str(output / (label + '.png')), full_page=True)
            page.locator('[name=payment_method]').select_option('cash')
            page.get_by_role('button', name='Confirm payment & view receipt', exact=True).click()
            page.locator('[data-confirm-dialog-accept]').click()
            page.wait_for_url('**/billing/receipt/*/print/')
            expect(page.get_by_role('button', name='Print Receipt', exact=True)).to_be_visible()
            self.assertEqual(errors, [])
            browser.close()
        self.assertEqual(Cart.objects.count(), 1)
        self.assertEqual(BillingDocument.objects.filter(document_type='receipt').count(), 1)

    def test_first_customer_save_uses_ajax_redirect(self):
        with sync_playwright() as playwright:
            browser, page = self.browser(playwright)
            self.open_workspace(page)
            page.get_by_role('tab', name='Customer', exact=True).click()
            page.locator('[name=walk_in_name]').fill('Workspace buyer')
            page.get_by_role('button', name='Save customer', exact=True).click()
            page.wait_for_url(re.compile(r'/inventory/carts/\d+/$'))
            browser.close()
        self.assertEqual(Cart.objects.get().walk_in_name, 'Workspace buyer')

    def test_open_serial_picker_then_cancel_never_persists_a_draft(self):
        self.product.is_serialized = True
        self.product.save()
        InventoryBalance.objects.create(tenant=self.org, organization=self.org, product=self.product, quantity=1, average_cost=10)
        StockUnit.objects.create(tenant=self.org, organization=self.org, product=self.product, serial_number='BROWSER-WORK-1', unit_cost=10)
        with sync_playwright() as playwright:
            browser, page = self.browser(playwright)
            self.open_workspace(page)
            page.get_by_role('link', name='Select serial', exact=True).click()
            expect(page.get_by_role('heading', name='Add cart item', exact=True)).to_be_visible()
            expect(page.get_by_text('BROWSER-WORK-1', exact=True)).to_be_visible()
            page.get_by_role('link', name='Cancel', exact=True).click()
            expect(page.get_by_text('A draft is saved when you add an item or save sale details.')).to_be_visible()
            browser.close()
        self.assertFalse(Cart.objects.exists())

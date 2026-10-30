"""Optional Edge checkout smoke test against synthetic data in the test database."""
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import expect, sync_playwright

from . import tests_cart_checkout as fixtures
from .models import InventoryBalance
from billing.models import BillingDocument


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class CartCheckoutBrowserChecks(StaticLiveServerTestCase):
    setUp = fixtures.CartCheckoutTests.setUp

    def test_payment_survives_cart_refresh_and_opens_receipt(self):
        output = Path(settings.BASE_DIR) / '.backups' / 'cart-checkout'
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            cached_tailwind = output / 'tailwind.js'
            if cached_tailwind.exists():
                context.route('https://cdn.tailwindcss.com/**', lambda route: route.fulfill(
                    path=str(cached_tailwind), content_type='application/javascript',
                ))
                context.route('https://cdn.tailwindcss.com', lambda route: route.fulfill(
                    path=str(cached_tailwind), content_type='application/javascript',
                ))
            context.add_cookies([{
                'name': settings.SESSION_COOKIE_NAME, 'value': self.client.session.session_key,
                'url': self.live_server_url,
            }])
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + reverse('inventory:cart_detail', args=[self.cart.pk]))
            page.locator('[name=payment_method]').select_option('mobile_money')
            page.locator('[name=payment_reference]').fill('BROWSER-TX-ONE')
            page.get_by_role('button', name='Increase Router quantity', exact=True).click()
            expect(page.locator('[data-pos-quantity]')).to_have_text('3')
            page.get_by_role('button', name='Decrease Router quantity', exact=True).click()
            expect(page.locator('[data-pos-quantity]')).to_have_text('2')
            expect(page.locator('[name=payment_method]')).to_have_value('mobile_money')
            page.get_by_role('button', name='Confirm payment & view receipt', exact=True).wait_for()
            self.assertEqual(page.locator('[name=amount_paid], [name=payment_date]').count(), 0)
            page.screenshot(path=str(output / 'desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('[data-cart-tab=cart]').click()
            expect(page.locator('[name=payment_reference]')).to_have_value('BROWSER-TX-ONE')
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            page.get_by_role('button', name='Confirm payment & view receipt', exact=True).click()
            page.locator('[data-confirm-dialog-accept]').click()
            page.wait_for_url('**/billing/receipt/*/')
            expect(page.get_by_role('heading', name='Receipt', exact=False).first).to_be_visible()
            self.assertEqual(errors, [])
            browser.close()
        receipt = BillingDocument.objects.get(document_type='receipt')
        self.assertEqual(receipt.payment_method, 'mobile_money')
        self.assertEqual(receipt.payment_reference, 'BROWSER-TX-ONE')
        self.assertEqual(InventoryBalance.objects.get(pk=self.balance.pk).quantity, 3)

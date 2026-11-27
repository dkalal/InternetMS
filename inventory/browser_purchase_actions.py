"""Real-browser regression for purchase form submission and receipt review."""
import re
from datetime import date
from pathlib import Path
from unittest import TestCase

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright, expect

from . import tests_purchase_workspace as fixtures
from .models import Purchase, StockMovement


class SerialSubmitBrowserChecks(TestCase):
    def test_immediate_serial_validation_resumes_with_clicked_action(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            for serials in ('', 'SERIAL-001'):
                with self.subTest(serials=serials):
                    page = browser.new_page()
                    page.set_content('''<form><textarea name="serial_numbers"></textarea>
                        <button type="submit" name="action" value="save_review">Review</button></form>''')
                    page.add_script_tag(path=str(Path(settings.BASE_DIR) / 'inventory/static/inventory/js/serial-validation.js'))
                    page.locator('textarea').fill(serials)
                    page.evaluate('''() => {
                        const form = document.querySelector('form');
                        const controller = new JimsSerialValidation.Controller(form, {});
                        // Both empty and previously verified serials resolve without a fetch.
                        controller.verified = JSON.stringify(controller.snapshot(controller.entries()));
                        window.actions = [];
                        form.addEventListener('submit', event => {
                            event.preventDefault();
                            window.actions.push(new FormData(form, event.submitter).get('action'));
                        });
                    }''')
                    page.get_by_role('button', name='Review', exact=True).click()
                    page.wait_for_function('window.actions.length === 1', timeout=3000)
                    self.assertEqual(page.evaluate('window.actions'), ['save_review'])
                    page.close()
            browser.close()


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class PurchaseActionBrowserChecks(StaticLiveServerTestCase):
    make_product = fixtures.UnifiedPurchaseWorkspaceTests.make_product

    def setUp(self):
        fixtures.UnifiedPurchaseWorkspaceTests.setUp(self)

    def test_all_workspace_actions_submit_and_receipt_requires_confirmation(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context()
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME,
                                 'value': self.client.session.session_key, 'url': self.live_server_url}])
            page = context.new_page()
            page.route('https://cdn.tailwindcss.com/**', lambda route: route.abort())
            page.route('https://cdn.tailwindcss.com/', lambda route: route.abort())
            page.on('dialog', lambda dialog: dialog.accept())
            for action in ('save_continue', 'save_review', 'confirm_receive'):
                with self.subTest(action=action):
                    page.goto(self.live_server_url + reverse('inventory:purchase_create'))
                    page.add_style_tag(content='.hidden { display: none !important; }')
                    page.locator('[name=supplier]').select_option(str(self.supplier.pk))
                    page.locator('[name=purchase_date]').fill(date.today().isoformat())
                    row = page.locator('[data-purchase-line]').first
                    row.locator('input[type=search]').fill(self.product.sku)
                    page.get_by_role('option').filter(has=page.locator('span', has_text=self.product.sku + ' /')).click()
                    row.locator('[name$="-quantity"]').fill('2')
                    row.locator('[name$="-unit_cost"]').fill('100')
                    page.locator('button[value="' + action + '"]').click()
                    suffix = '/edit/' if action == 'save_continue' else '/?receive=1' if action == 'confirm_receive' else '/'
                    expect(page).to_have_url(re.compile(r'/purchases/\d+' + re.escape(suffix) + '$'))
                    expect(page.get_by_text('Draft', exact=True).first).to_be_visible()
            page.get_by_role('button', name='Confirm & Receive Stock', exact=True).click()
            expect(page.get_by_text('Stock received.', exact=True)).to_be_visible()
            browser.close()
        self.assertEqual(Purchase.objects.count(), 3)
        self.assertEqual(Purchase.objects.filter(status=Purchase.Status.DRAFT).count(), 2)
        self.assertEqual(StockMovement.objects.count(), 1)

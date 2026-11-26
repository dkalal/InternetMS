"""Optional browser verification for the catalog-style entry tables."""
from pathlib import Path
import os

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from inventory import tests_purchase_workspace as fixtures


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'],
                   STORAGES={
                       'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                       'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
                   })
class EntryTableBrowserChecks(StaticLiveServerTestCase):
    setUp = fixtures.UnifiedPurchaseWorkspaceTests.setUp
    make_product = fixtures.UnifiedPurchaseWorkspaceTests.make_product

    def test_entry_tables_desktop_mobile_and_supplier_errors(self):
        output = Path(settings.BASE_DIR) / 'docs/screenshots/entry-tables'
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 960})
            context.add_cookies([{
                'name': settings.SESSION_COOKIE_NAME,
                'value': self.client.session.session_key,
                'domain': 'localhost', 'path': '/',
            }])
            page = context.new_page()
            cached_tailwind = os.environ.get('ENTRY_TABLE_TAILWIND_PATH')
            if cached_tailwind:
                context.route('https://cdn.tailwindcss.com/', lambda route: route.fulfill(
                    path=cached_tailwind, content_type='application/javascript'))
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            for name, url, header in (
                ('purchase', '/inventory/purchases/new/', '.purchase-grid-heading'),
                ('quotation', '/billing/quotation/create/', '.jims-line-item-header'),
                ('invoice', '/billing/invoice/create/', '.jims-line-item-header'),
                ('supplier', '/inventory/suppliers/new/', '.jims-entry-table thead th'),
            ):
                page.set_viewport_size({'width': 1440, 'height': 960})
                response = page.goto(self.live_server_url + url)
                self.assertEqual(response.status, 200)
                page.wait_for_function("getComputedStyle(document.body).fontFamily.includes('sans')")
                if name in ('quotation', 'invoice'):
                    page.locator('[data-document-tab="items"]').click()
                    expect(page.locator('#document-items-panel')).to_be_visible()
                    headings = page.locator('.jims-line-item-header > div')
                    cells = page.locator('.js-line-item').first.locator('.jims-line-item-grid > *')
                    for index in range(8):
                        self.assertAlmostEqual(headings.nth(index).bounding_box()['x'],
                                               cells.nth(index).bounding_box()['x'], delta=1)
                expect(page.locator(header).first).to_have_css('background-color', 'rgb(245, 243, 255)')
                page.screenshot(path=str(output / f'{name}-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 1024, 'height': 960})
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), name + ' tablet')
                page.set_viewport_size({'width': 390, 'height': 844})
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), name)
                page.screenshot(path=str(output / f'{name}-mobile.png'), full_page=True)
            page.locator('details.jims-entry-details > summary').click()
            expect(page.locator('#id_notes')).to_be_visible()
            page.locator('#id_company_name').fill('')
            # Bypass native validation to exercise server-rendered field errors.
            page.locator('form[data-unsaved-form]').evaluate('(form) => { form.noValidate = true; }')
            page.get_by_role('button', name='Save supplier').click()
            expect(page.locator('#id_company_name')).to_be_visible()
            expect(page.get_by_text('This field is required.', exact=True)).to_be_visible()
            self.assertEqual(errors, [])
            browser.close()

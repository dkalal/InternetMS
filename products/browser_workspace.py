"""Optional Edge verification of the product creation workspace."""
from pathlib import Path
import os
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright
from products import tests_workspace as fixtures


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class ProductWorkspaceBrowserChecks(StaticLiveServerTestCase):
    def setUp(self):
        from tempfile import TemporaryDirectory
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        media_settings = override_settings(MEDIA_ROOT=self.media.name)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        fixtures.ProductWorkspaceTests.setUp(self)

    def test_grid_paste_details_mobile_and_save(self):
        output = Path(settings.BASE_DIR) / 'docs' / 'screenshots' / 'product-workspace'
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 960}, ignore_https_errors=True)
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.session.session_key,
                                 'domain': 'localhost', 'path': '/'}])
            cached_tailwind = os.environ.get('PRODUCT_BROWSER_TAILWIND_PATH')
            if cached_tailwind:
                context.route('https://cdn.tailwindcss.com/', lambda route: route.fulfill(path=cached_tailwind, content_type='application/javascript'))
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(self.live_server_url + self.url)
            page.wait_for_function("getComputedStyle(document.querySelector('body')).fontFamily.includes('sans')")
            expect(page.locator('.product-entry')).to_have_count(5)
            expect(page.locator('[data-detail=brand]')).to_have_count(0)
            expect(page.get_by_text('Legacy classification', exact=False)).to_have_count(0)
            money = page.locator('[data-field=buying_price]').first
            money.fill('1000.123456')
            money.press('ArrowUp')
            expect(money).to_have_value('1001.123456')
            money.locator('..').locator('[data-direction="-1"]').click()
            expect(money).to_have_value('1000.123456')
            money.fill('')
            page.locator('[data-field=name]').first.fill('Router')
            page.locator('[data-field=name]').first.press('Enter')
            expect(page.locator('[data-field=model_number]').first).to_be_focused()
            page.locator('[data-details]').first.click()
            expect(page.locator('.product-detail-row').first).to_be_visible()
            page.locator('[data-field=reorder_threshold]').first.fill('5')
            page.locator('[data-details]').first.click()
            page.on('dialog', lambda dialog: dialog.accept())
            page.locator('[data-field=name]').first.evaluate('''element => {
                const data = new DataTransfer();
                data.setData('text/plain', 'Name\\tModel\\tSKU\\tSales unit\\tBuying price\\tStandard price\\tDescription\\r\\nRouter\\t0012\\t0001\\tPiece (pc)\\t10\\t20\\t"First line\\nSecond line"');
                element.dispatchEvent(new ClipboardEvent('paste', {clipboardData:data,bubbles:true,cancelable:true}));
            }''')
            # Resolve the exact tenant unit label through the rendered choice.
            page.locator('[data-field=sales_unit]').first.select_option(str(self.unit.pk))
            page.locator('[data-field=buying_price]').first.fill('10')
            page.locator('[data-field=selling_price]').first.fill('20')
            from products.tests import product_image_upload
            picture = product_image_upload()
            page.locator('[data-field=image]').first.set_input_files({'name': 'router.jpg', 'mimeType': 'image/jpeg', 'buffer': picture.read()})
            page.locator('[data-remove-row]').nth(1).click()
            expect(page.locator('.product-entry')).to_have_count(4)
            page.locator('[data-details]').first.click()
            page.locator('[data-additional-information] > summary').first.click()
            page.locator('[data-grid-custom-field-open]').first.click()
            modal = page.locator('#custom-field-definition-modal')
            expect(modal).to_be_visible()
            modal.locator('#id_label').fill('Warranty')
            modal.locator('[data-custom-field-tab-button=advanced]').click()
            modal.locator('#id_required').check()
            modal.locator('#id_default_value').fill('12 months')
            modal.get_by_role('button', name='Save custom field', exact=True).click()
            expect(modal).not_to_be_visible()
            expect(page.locator('[data-field=cf_warranty]')).to_have_count(4)
            expect(page.locator('[data-field=cf_warranty]').first).to_have_value('12 months')
            page.locator('[data-field=cf_warranty]').first.fill('24 months')
            page.locator('#add-product-row').click()
            expect(page.locator('[data-field=cf_warranty]')).to_have_count(5)
            expect(page.locator('[data-field=cf_warranty]').last).to_have_value('12 months')
            page.locator('[data-remove-row]').last.click()
            page.locator('[data-details]').first.click()
            self.assertEqual(page.locator('[data-field=image]').first.evaluate('e => e.files.length'), 1)
            page.locator('[data-field=selling_price]').first.fill('5')
            page.locator('.product-toolbar summary').click()
            page.locator('[data-column=selling_price]').uncheck()
            page.locator('#create-products').click()
            expect(page.locator('[data-field=selling_price]').first).to_have_attribute('aria-invalid', 'true')
            expect(page.locator('[data-column=selling_price]')).to_be_checked()
            self.assertEqual(page.locator('[data-field=image]').first.evaluate('e => e.files.length'), 1)
            page.locator('[data-field=selling_price]').first.fill('20')
            page.locator('.product-toolbar summary').click()
            page.locator('.product-grid-scroll').evaluate('element => element.scrollLeft = 0')
            page.screenshot(path=str(output / 'desktop.png'), full_page=True)
            for width in (768, 390):
                page.set_viewport_size({'width': width, 'height': 900})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
            page.screenshot(path=str(output / 'mobile.png'), full_page=True)
            page.locator('#create-products').click()
            page.wait_for_url('**/products/')
            self.assertEqual(errors, [])
            browser.close()

        from products.models import Product
        product = Product.objects.unscoped().get(tenant=self.org)
        self.assertEqual(product.sku, '0001')
        self.assertEqual(product.model_number, '0012')
        self.assertEqual(product.description, 'First line\nSecond line')
        self.assertTrue(product.image)
        from custom_fields.models import CustomFieldValue
        self.assertEqual(CustomFieldValue.objects.get(tenant=self.org).value_text, '24 months')

    def test_paste_expands_to_limit_and_marks_unknown_choices(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 960}, ignore_https_errors=True)
            cached_tailwind = os.environ.get('PRODUCT_BROWSER_TAILWIND_PATH')
            if cached_tailwind:
                context.route('https://cdn.tailwindcss.com/', lambda route: route.fulfill(path=cached_tailwind, content_type='application/javascript'))
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': self.client.session.session_key,
                                 'domain': 'localhost', 'path': '/'}])
            page = context.new_page()
            page.goto(self.live_server_url + self.url)
            page.on('dialog', lambda dialog: dialog.accept())
            page.locator('#paste-product-rows').click()
            page.locator('#product-paste-text').fill('Name\tSales unit\nBad choice\tUnknown unit')
            page.locator('#apply-product-paste').click()
            expect(page.locator('[data-col=sales_unit] .cell-error').first).to_contain_text('Unknown')
            text = 'Name\tModel\tSales unit\tBuying price\tStandard price\tTechnician price\n'
            text += '\n'.join(f'Product {i}\t{i:04}\tPiece (pc)\t10\t20\t' for i in range(200))
            page.locator('#paste-product-rows').click()
            page.locator('#product-paste-text').fill(text)
            page.locator('#apply-product-paste').click()
            expect(page.locator('.product-entry')).to_have_count(200)
            expect(page.locator('[data-field=model_number]').last).to_have_value('0199')
            page.locator('#add-product-row').click()
            expect(page.locator('.product-entry')).to_have_count(200)
            browser.close()

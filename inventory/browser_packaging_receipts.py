"""Unit-aware receipt browser gate; synthetic data and closed release capability patched only in tests."""
import os
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from playwright.sync_api import sync_playwright, expect
from .tests_packaging_receipts import ReceiptFixture
from .models import InventoryBalance, StockContainer


@override_settings(DEBUG=True,SECURE_SSL_REDIRECT=False,SESSION_COOKIE_SECURE=False,CSRF_COOKIE_SECURE=False,ALLOWED_HOSTS=['localhost','testserver'])
class PackagingReceiptBrowser(ReceiptFixture,StaticLiveServerTestCase):
    @patch('products.packaging.workflows_ready',return_value=True)
    def test_commercial_entry_preview_receipt_and_responsive_container_list(self,ready):
        self.line(quantity='1');self.client.force_login(self.actor)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel=os.environ.get('PLAYWRIGHT_BROWSER_CHANNEL','msedge') or None,headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000})
            cache=Path(settings.BASE_DIR)/'.backups/cart-checkout/tailwind.js'
            if cache.exists():
                context.route('https://cdn.tailwindcss.com/**',lambda route:route.fulfill(path=str(cache),content_type='application/javascript'))
                context.route('https://cdn.tailwindcss.com',lambda route:route.fulfill(path=str(cache),content_type='application/javascript'))
            context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.session.session_key,'url':self.live_server_url}])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.live_server_url+reverse('inventory:purchase_edit',args=[self.purchase.pk]))
            row=page.locator('[data-purchase-line]').first
            expect(row.locator('[name$="-quantity"]')).to_have_value('1')
            row.locator('[name$="-purchase_packaging"]').select_option('')
            expect(row.locator('[name$="-unit_cost"]')).to_have_value('')
            row.locator('[name$="-purchase_packaging"]').select_option(str(self.package.pk))
            row.locator('[name$="-quantity"]').fill('10')
            row.locator('[name$="-unit_cost"]').fill('305')
            expect(row.locator('[data-base-impact]')).to_contain_text('3050')
            output=Path(settings.BASE_DIR)/'.backups/packaging-receipts'
            output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/'desktop.png'),full_page=True)
            page.get_by_role('button',name='Save & Review',exact=True).click()
            try:
                page.wait_for_url('**/purchases/'+str(self.purchase.pk)+'/',timeout=10000)
            except Exception:
                output=Path(settings.BASE_DIR)/'.backups/packaging-receipts'
                output.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(output/'validation.png'),full_page=True)
                print('Browser URL:',page.url)
                print('Browser JavaScript errors:',errors)
                print('Submit state:',page.evaluate("() => { const f=document.querySelector('.purchase-workspace-form'); return {submitting:f.dataset.submitting,validator:!!f.serialValidator,serialErrors:f.serialValidator ? Array.from(f.serialValidator.errors.entries()):[],resubmitting:f.serialValidator && f.serialValidator.resubmitting}; }"))
                print('Browser validation:',page.locator('[role=alert], .errorlist').all_text_contents())
                print('Invalid inputs:',page.locator(':invalid').evaluate_all('(nodes) => nodes.map(n => ({name:n.name,message:n.validationMessage}))'))
                raise
            expect(page.get_by_text('10 Box',exact=False).first).to_be_visible()
            page.get_by_role('button',name='Confirm & Receive',exact=True).click()
            page.locator('[data-confirm-dialog-accept]').click()
            expect(page.get_by_role('heading',name='Received containers')).to_be_visible()
            page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            page.screenshot(path=str(output/'mobile.png'),full_page=True)
            self.assertEqual(errors,[])
            browser.close()
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity,3050)
        self.assertEqual(StockContainer.objects.count(),10)

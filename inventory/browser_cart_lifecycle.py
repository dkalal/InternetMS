"""Optional Edge smoke check using synthetic carts and an isolated test database."""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from .tests_cart_lifecycle import CartFixture


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=['localhost', 'testserver'])
class CartLifecycleBrowserChecks(CartFixture, StaticLiveServerTestCase):
    def test_desktop_mobile_tabs_filters_and_start(self):
        self.cart(walk_in_name='Current shopper')
        self.cart(age=timedelta(days=8),walk_in_name='Stale shopper')
        self.cart(status='abandoned',walk_in_name='Past shopper')
        output=Path(settings.BASE_DIR)/'.backups'/'cart-lifecycle'
        output.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(channel='msedge',headless=True)
            context=browser.new_context(viewport={'width':1440,'height':900})
            context.add_cookies([{'name':settings.SESSION_COOKIE_NAME,'value':self.client.session.session_key,'url':self.live_server_url}])
            page=context.new_page()
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(self.live_server_url+'/inventory/carts/')
            expect(page.get_by_role('table')).to_be_visible()
            expect(page.get_by_role('navigation',name='Cart history').get_by_role('link',name='Active (2)',exact=True)).to_have_attribute('aria-current','page')
            expect(page.get_by_role('cell',name='Current shopper')).to_be_visible()
            page.screenshot(path=str(output/'desktop.png'),full_page=True)
            page.get_by_role('link',name='Stale drafts (1)',exact=True).click()
            expect(page.get_by_role('cell',name='Stale shopper')).to_be_visible()
            expect(page.get_by_role('cell',name='Current shopper')).to_have_count(0)
            page.set_viewport_size({'width':390,'height':844})
            page.reload()
            expect(page.get_by_role('table')).to_be_hidden()
            expect(page.get_by_role('article')).to_have_count(1)
            expect(page.get_by_role('article')).to_contain_text('Stale shopper')
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'))
            page.screenshot(path=str(output/'mobile.png'),full_page=True)
            page.get_by_role('link',name='Abandoned (1)',exact=True).click()
            page.get_by_role('link',name='View history',exact=True).click()
            expect(page.get_by_role('button',name='Restore as draft')).to_be_visible()
            page.get_by_role('button',name='Restore as draft').click()
            expect(page.get_by_text('Restored draft.',exact=False)).to_be_visible()
            self.assertEqual(errors,[])
            browser.close()

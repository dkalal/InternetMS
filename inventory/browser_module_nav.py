"""Optional real Edge check for the shared inventory module navigation."""
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from . import tests_purchase_mixed_import as fixtures


@override_settings(
    DEBUG=True,
    SECURE_SSL_REDIRECT=False,
    SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False,
    ALLOWED_HOSTS=["localhost", "testserver"],
)
class InventoryModuleNavigationBrowserChecks(StaticLiveServerTestCase):
    def setUp(self):
        fixtures.MixedPurchaseImportTests.setUp(self)

    def test_active_state_keyboard_region_and_mobile_overflow(self):
        output = Path(settings.BASE_DIR) / ".backups" / "inventory-module-nav"
        output.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            context.add_cookies([{
                "name": settings.SESSION_COOKIE_NAME,
                "value": self.client.session.session_key,
                "domain": "localhost",
                "path": "/",
            }])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{self.live_server_url}/products/")

            nav = page.get_by_role("navigation", name="Inventory sections")
            current = nav.locator('[aria-current="page"]')
            expect(current).to_have_count(1)
            expect(current).to_contain_text("Catalog")
            self.assertEqual(current.get_attribute("href"), "/products/")
            self.assertNotEqual(current.evaluate("element => getComputedStyle(element).backgroundImage"), "none")
            page.screenshot(path=str(output / "desktop.png"), full_page=True)

            page.set_viewport_size({"width": 390, "height": 844})
            page.reload()
            scroller = nav.locator(".jims-module-nav__scroller")
            overflow = scroller.evaluate("element => ({client: element.clientWidth, scroll: element.scrollWidth})")
            self.assertGreater(overflow["scroll"], overflow["client"])
            scroller.focus()
            expect(scroller).to_be_focused()
            expect(current).to_be_visible()
            page.screenshot(path=str(output / "mobile.png"), full_page=True)

            self.assertEqual(errors, [])
            browser.close()

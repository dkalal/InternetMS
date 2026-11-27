"""Opt-in browser smoke tests; synthetic PostgreSQL data, no office records."""
from pathlib import Path
import os
from unittest.mock import patch

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from .tests_assignments import AssignmentFixtures
from .models import WorkAssignment, TechnicianWorkReport, TechnicianPaymentRecord


@override_settings(DEBUG=True, SECURE_SSL_REDIRECT=False, SESSION_COOKIE_SECURE=False,
                   CSRF_COOKIE_SECURE=False, ALLOWED_HOSTS=["localhost", "testserver"])
class AssignmentBrowserTests(AssignmentFixtures, StaticLiveServerTestCase):
    @patch.dict(os.environ, {"DJANGO_ALLOW_ASYNC_UNSAFE": "true"})
    def flow(self, width):
        def url(name, *args):
            return self.live_server_url + reverse("work_reports:" + name, args=args)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            contexts = []
            def page_for(member):
                client = self.client_for(member)
                context = browser.new_context(viewport={"width": width, "height": 950})
                contexts.append(context)
                context.route("https://**/*", lambda route: route.abort())
                cached = Path(settings.BASE_DIR) / ".backups/cart-checkout/tailwind.js"
                if not cached.exists():
                    raise RuntimeError("Browser verification requires the repository's cached Tailwind runtime.")
                for pattern in ("https://cdn.tailwindcss.com", "https://cdn.tailwindcss.com/**"):
                    context.route(pattern, lambda route: route.fulfill(path=str(cached), content_type="application/javascript"))
                context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                     "value": client.session.session_key, "url": self.live_server_url}])
                return context.new_page()
            manager = page_for(self.manager)
            tech = page_for(self.tech)
            manager.goto(url("assignment_create"))
            manager.locator("#id_technician").select_option(str(self.tech.pk))
            manager.locator("#id_work_title").fill("Synthetic browser installation")
            manager.locator("#id_scope").fill("Install synthetic demonstration equipment")
            manager.locator("#id_proposed_start").fill(self.data["proposed_start"].isoformat())
            manager.locator("#id_proposed_amount").fill("100000")
            manager.locator("#id_management_notes").fill("PRIVATE-MANAGEMENT-ONLY")
            manager.get_by_role("button", name="Save draft", exact=True).click()
            expect(manager.get_by_role("heading", name="Synthetic browser installation")).to_be_visible()
            assignment = WorkAssignment.objects.unscoped().get(work_title="Synthetic browser installation")
            manager.locator("#id_action").select_option("send")
            manager.get_by_role("button", name="Confirm action").click()
            tech.goto(url("assignment_detail", assignment.pk))
            self.assertNotIn("PRIVATE-MANAGEMENT-ONLY", tech.content())
            tech.locator("#id_action").select_option("request_changes")
            tech.locator("#id_reason").fill("Additional synthetic equipment")
            tech.locator("#id_proposed_amount").fill("150000")
            tech.get_by_role("button", name="Confirm action").click()
            manager.reload()
            expect(manager.get_by_text("Additional synthetic equipment", exact=True)).to_be_visible()
            manager.get_by_role("link", name="Revise / reassign").click()
            manager.locator("#id_proposed_amount").fill("150000")
            manager.locator("#id_reason").fill("Agreed revised scope")
            manager.get_by_role("button", name="Save draft", exact=True).click()
            manager.locator("#id_action").select_option("send")
            manager.get_by_role("button", name="Confirm action").click()
            tech.reload()
            tech.locator("#id_action").select_option("accept")
            tech.get_by_role("button", name="Confirm action").click()
            expect(tech.get_by_role("link", name="Create Work Report")).to_be_visible()
            self.assertLessEqual(tech.evaluate("document.documentElement.scrollWidth"), width)
            out = Path(settings.BASE_DIR) / "docs" / "screenshots"
            out.mkdir(exist_ok=True)
            tech.screenshot(path=str(out / f"work_assignments_{width}.png"), full_page=True)
            tech.get_by_role("link", name="Create Work Report").click()
            expect(tech.locator("#id_agreed_amount")).to_be_disabled()
            tech.locator("#id_activity_description").fill("Completed synthetic installation and testing")
            tech.locator("#work-date-specific").fill(timezone.localdate().isoformat())
            tech.locator("#add-specific-work-date").click()
            tech.get_by_role("button", name="Save draft", exact=True).click()
            tech.get_by_role("button", name="Submit for review", exact=True).click()
            report = TechnicianWorkReport.objects.unscoped().get(assignment=assignment)
            manager.goto(url("detail", report.pk))
            manager.get_by_role("button", name="Approve report", exact=True).click()
            manager.get_by_role("link", name="Record payment", exact=True).click()
            manager.locator("#id_amount_paid").fill("150000")
            manager.locator("#id_payment_date").fill(timezone.localdate().isoformat())
            manager.locator("#id_payment_method").select_option("CASH")
            manager.get_by_role("button", name="Review and record", exact=True).click()
            manager.locator("#manual-payment-dialog button").filter(has_text="Record payment").click()
            tech.reload()
            tech.get_by_role("button", name="Confirm received", exact=True).first.click()
            tech.locator("#confirm-payment-dialog").get_by_role("button", name="Confirm received", exact=True).click()
            expect(tech.get_by_text("Received confirmed", exact=True)).to_be_visible()
            self.assertEqual(TechnicianPaymentRecord.objects.unscoped().get(report=report).status, "CONFIRMED")
            assignment.refresh_from_db()
            self.assertEqual(assignment.status, "COMPLETED")
            for context in contexts:
                context.close()
            browser.close()

    def test_desktop_workflow(self):
        self.flow(1440)

    def test_mobile_workflow(self):
        self.flow(390)

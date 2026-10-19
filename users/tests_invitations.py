from datetime import timedelta
import os
import subprocess
import sys
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.conf import settings
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils import timezone
from internetservices.log_filters import redact_invitation_path

from .models import Organization, TenantMembership, WorkspaceInvitation


@override_settings(APP_BASE_URL="http://office.example.test:8000")
class InvitationTests(TestCase):
    def setUp(self):
        self.tenant = Organization.objects.create(name="Office", slug="office-invite")
        self.manager = get_user_model().objects.create_user("office-manager", password="pass")
        TenantMembership.objects.create(tenant=self.tenant, user=self.manager, base_role="ADMIN_MANAGER")
        self.client.force_login(self.manager)

    def invite(self, email="new@example.test"):
        return self.client.post(reverse("invite_member"), {"email": email, "base_role": "SALES"})

    def test_canonical_link_preview_password_validation_and_replay(self):
        self.invite()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("http://office.example.test:8000/users/invitation/", mail.outbox[0].body)
        self.assertNotIn("testserver", mail.outbox[0].body)
        path = mail.outbox[0].body.split("http://office.example.test:8000", 1)[1].splitlines()[0]
        invited = get_user_model().objects.get(email="new@example.test")
        self.assertFalse(invited.has_usable_password())
        self.client.post(path, {"action": "sign_out"})
        self.assertEqual(self.client.get(path).status_code, 200)
        invited.refresh_from_db()
        self.assertFalse(invited.has_usable_password())
        invalid = self.client.post(path, {"new_password1": "short", "new_password2": "short"})
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "auth-error")
        self.assertContains(invalid, 'for="id_new_password1"')
        self.assertNotContains(invalid, "New invoice")
        self.assertFalse(get_user_model().objects.get(pk=invited.pk).has_usable_password())
        self.client.post(path, {"new_password1": "ValidOfficePassword123!", "new_password2": "ValidOfficePassword123!"})
        self.assertEqual(self.client.post(path, {"new_password1": "AnotherPassword123!", "new_password2": "AnotherPassword123!"}).status_code, 400)
        self.assertEqual(self.client.get(reverse("accept_workspace_invitation", args=["invalid"])).status_code, 400)

    def test_expired_link_and_resend_rotation(self):
        self.invite()
        old = mail.outbox[0].body.split("http://office.example.test:8000", 1)[1].splitlines()[0]
        invitation = WorkspaceInvitation.objects.get()
        invitation.expires_at = timezone.now() - timedelta(seconds=1)
        invitation.save(update_fields=["expires_at"])
        self.assertEqual(self.client.get(old).status_code, 400)
        membership = invitation.membership
        self.assertEqual(self.client.post(reverse("resend_member_activation", args=[membership.pk])).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)  # cooldown after a successful send

    @override_settings(INVITATION_RESEND_COOLDOWN_SECONDS=0)
    def test_resend_invalidates_old_link(self):
        self.invite()
        old = mail.outbox[0].body.split("http://office.example.test:8000", 1)[1].splitlines()[0]
        membership = WorkspaceInvitation.objects.get().membership
        self.client.post(reverse("resend_member_activation", args=[membership.pk]))
        self.assertEqual(len(mail.outbox), 2)
        new = mail.outbox[1].body.split("http://office.example.test:8000", 1)[1].splitlines()[0]
        self.assertNotEqual(old, new)
        self.assertEqual(self.client.get(old).status_code, 400)
        self.assertEqual(self.client.get(new).status_code, 200)

    def test_send_failure_preserves_invitation(self):
        with patch("users.invitations.EmailMultiAlternatives.send", side_effect=OSError("private SMTP response")):
            response = self.invite()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(WorkspaceInvitation.objects.exists())
        self.assertIsNone(WorkspaceInvitation.objects.get().sent_at)
        self.assertTrue(get_user_model().objects.filter(email="new@example.test").exists())

    def test_existing_user_keeps_password_and_accepts_own_membership(self):
        existing = get_user_model().objects.create_user("existing-office", email="existing@example.test", password="OldPassword123!")
        other = Organization.objects.create(name="Other", slug="other-invite")
        TenantMembership.objects.create(tenant=other, user=existing, base_role="SALES")
        old_password = existing.password
        self.invite("existing@example.test")
        membership = TenantMembership.objects.get(tenant=self.tenant, user=existing)
        self.assertFalse(membership.is_active)
        existing.refresh_from_db()
        self.assertEqual(existing.password, old_password)
        path = mail.outbox[0].body.split("http://office.example.test:8000", 1)[1].splitlines()[0]
        self.assertEqual(self.client.post(path).status_code, 403)
        self.client.force_login(existing)
        self.assertEqual(self.client.post(path).status_code, 302)
        membership.refresh_from_db()
        self.assertTrue(membership.is_active)
        existing.refresh_from_db()
        self.assertEqual(existing.password, old_password)

    def test_admin_session_on_same_browser_cannot_complete_technician_invitation(self):
        other_admin_browser = Client()
        other_admin_browser.force_login(self.manager)
        self.client.post(reverse("invite_member"), {"email": "field.tech@example.test", "base_role": "TECHNICIAN"})
        path = mail.outbox[0].body.split("http://office.example.test:8000", 1)[1].splitlines()[0]
        page = self.client.get(path)
        self.assertContains(page, "Sign out to continue")
        self.assertNotContains(page, "New invoice")
        self.assertNotContains(page, "Start sale")
        self.assertEqual(page["Referrer-Policy"], "origin")
        self.assertContains(page, '<meta name="referrer" content="origin">')
        self.assertIn("no-store", page["Cache-Control"])
        login_page = self.client.get(reverse("login"))
        self.assertNotContains(login_page, "New invoice")
        self.assertNotContains(login_page, "Start sale")
        self.assertEqual(self.client.post(path, {"new_password1": "FieldTechPassword123!", "new_password2": "FieldTechPassword123!"}).status_code, 403)
        self.assertFalse(get_user_model().objects.get(email="field.tech@example.test").has_usable_password())

        self.assertEqual(self.client.post(path, {"action": "sign_out"}).status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertIn("_auth_user_id", other_admin_browser.session)
        setup = self.client.get(path)
        self.assertContains(setup, "Set your password")
        self.assertNotContains(setup, "New invoice")
        self.assertNotContains(setup, "Start sale")
        completed = self.client.post(path, {"new_password1": "FieldTechPassword123!", "new_password2": "FieldTechPassword123!"}, follow=True)
        self.assertContains(completed, "Password updated")
        self.assertNotContains(completed, "New invoice")
        self.assertNotContains(completed, "Start sale")
        self.assertNotIn("_auth_user_id", self.client.session)

        technician = get_user_model().objects.get(email="field.tech@example.test")
        self.assertTrue(self.client.login(username=technician.username, password="FieldTechPassword123!"))
        self.assertRedirects(self.client.get(reverse("main_app:workspace_home")), reverse("work_reports:list"))
        self.assertEqual(self.client.get(reverse("billing:document_create", kwargs={"doc_type": "invoice"})).status_code, 403)
        self.assertEqual(self.client.post(reverse("inventory:cart_create")).status_code, 403)

    def test_invited_user_on_another_device_does_not_inherit_admin_session(self):
        self.client.post(reverse("invite_member"), {"email": "remote.tech@example.test", "base_role": "TECHNICIAN"})
        path = mail.outbox[0].body.split("http://office.example.test:8000", 1)[1].splitlines()[0]
        technician_browser = Client()
        page = technician_browser.get(path)
        self.assertContains(page, "Set your password")
        self.assertNotContains(page, "New invoice")
        self.assertNotContains(page, "Start sale")
        self.assertNotIn("_auth_user_id", technician_browser.session)
        self.assertIn("_auth_user_id", self.client.session)
        technician_browser.post(path, {"new_password1": "RemoteTechPassword123!", "new_password2": "RemoteTechPassword123!"})
        self.assertNotIn("_auth_user_id", technician_browser.session)
        self.assertIn("_auth_user_id", self.client.session)

    def test_switch_account_requires_csrf(self):
        self.client.post(reverse("invite_member"), {"email": "csrf.tech@example.test", "base_role": "TECHNICIAN"})
        path = mail.outbox[0].body.split("http://office.example.test:8000", 1)[1].splitlines()[0]
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.manager)
        response = browser.post(path, {"action": "sign_out"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("_auth_user_id", browser.session)

    def test_password_recovery_pages_use_standalone_form(self):
        reset_page = self.client.get(reverse("password_reset"))
        self.assertContains(reset_page, 'for="id_email"')
        self.assertNotContains(reset_page, "New invoice")

        uid = urlsafe_base64_encode(force_bytes(self.manager.pk))
        token = default_token_generator.make_token(self.manager)
        path = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})
        reset_form = self.client.get(path, follow=True)
        self.assertContains(reset_form, 'for="id_new_password1"')
        self.assertContains(reset_form, 'for="id_new_password2"')
        self.assertNotContains(reset_form, "New invoice")

    def test_login_form_preserves_origin_check_on_another_browser(self):
        browser = Client(enforce_csrf_checks=True)
        page = browser.get(reverse("login"))
        self.assertContains(page, '<meta name="referrer" content="origin">')
        csrf_token = browser.cookies[settings.CSRF_COOKIE_NAME].value
        denied = browser.post(
            reverse("login"),
            {"username": self.manager.username, "password": "pass", "csrfmiddlewaretoken": csrf_token},
            HTTP_ORIGIN="null",
        )
        self.assertEqual(denied.status_code, 403)
        allowed = browser.post(
            reverse("login"),
            {"username": self.manager.username, "password": "pass", "csrfmiddlewaretoken": csrf_token},
            HTTP_ORIGIN="http://testserver",
        )
        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(browser.session.get("_auth_user_id"), str(self.manager.pk))


from django.test import SimpleTestCase


class EmailSettingsTests(SimpleTestCase):
    def test_invitation_token_is_removed_from_audit_path(self):
        self.assertEqual(
            redact_invitation_path("/users/invitation/secretBearerValue/"),
            "/users/invitation/[redacted]/",
        )

    def test_email_settings_independent_of_debug(self):
        env = dict(os.environ, DJANGO_DEBUG="1", EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
                   EMAIL_USE_TLS="true", EMAIL_USE_SSL="false", EMAIL_PORT="587")
        result = subprocess.run(
            [sys.executable, "-c", "from internetservices import settings; print(settings.EMAIL_BACKEND, settings.EMAIL_USE_TLS, settings.EMAIL_PORT)"],
            env=env, capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "django.core.mail.backends.smtp.EmailBackend True 587")

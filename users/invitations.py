"""Tenant-bound first-password invitations."""
import hashlib
import logging
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import TenantMembership, WorkspaceInvitation

logger = logging.getLogger(__name__)


def office_base_url():
    url = settings.APP_BASE_URL
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (parsed.scheme not in {"http", "https"} or not host or parsed.username or
            parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or
            host in {"localhost", "127.0.0.1", "0.0.0.0", "web", "db", "internetms"} or
            "." not in host and ":" not in host):
        raise ValidationError("Set APP_BASE_URL to the office address reachable from other devices.")
    return url


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_invitation(membership):
    """Call under an atomic transaction after membership authorization."""
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    WorkspaceInvitation.objects.filter(
        membership=membership, accepted_at__isnull=True, revoked_at__isnull=True,
    ).update(revoked_at=now)
    invitation = WorkspaceInvitation.objects.create(
        membership=membership, approved_role=membership.base_role, token_hash=token_hash(token),
        expires_at=now + timedelta(hours=settings.INVITATION_EXPIRY_HOURS),
    )
    return invitation, token


def send_invitation(invitation, token):
    base = office_base_url()
    membership = invitation.membership
    user = membership.user
    url = base + reverse("accept_workspace_invitation", kwargs={"token": token})
    context = {"user": user, "tenant": membership.tenant, "activation_url": url,
               "has_password": user.has_usable_password(),
               "expires_at": invitation.expires_at}
    message = EmailMultiAlternatives(
        subject=f"{settings.EMAIL_SUBJECT_PREFIX}Set up your {membership.tenant.name} workspace account",
        body=render_to_string("registration/workspace_invitation_email.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL, to=[user.email],
    )
    message.attach_alternative(render_to_string("registration/workspace_invitation_email.html", context), "text/html")
    count = message.send(fail_silently=False)
    if count != 1:
        raise RuntimeError("Email backend did not accept the invitation")
    WorkspaceInvitation.objects.filter(pk=invitation.pk).update(sent_at=timezone.now())
    return settings.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"


def send_after_commit(invitation, token, request):
    """Synchronous after commit so the redirect can report the actual SMTP result."""
    from django.contrib import messages
    try:
        smtp = send_invitation(invitation, token)
    except (OSError, ValueError, RuntimeError, ValidationError) as exc:
        logger.warning("Invitation email failed for invitation %s (%s)", invitation.pk, type(exc).__name__)
        messages.error(request, "Invitation saved, but the email could not be sent. Please retry.")
    except Exception as exc:
        logger.warning("Invitation email failed for invitation %s (%s)", invitation.pk, type(exc).__name__)
        messages.error(request, "Invitation saved, but the email could not be sent. Please retry.")
    else:
        if smtp:
            messages.success(request, "Invitation sent. Ask the user to check Inbox or Spam.")
        else:
            messages.warning(request, "Invitation saved. Development email backend used; no real email was sent.")

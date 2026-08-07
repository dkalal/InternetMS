# import logging
# import socket
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, authenticate
from django.contrib import messages
from .forms import UserRegisterForm
from django.core.mail import send_mail, EmailMessage, EmailMultiAlternatives
# from django.http import HttpResponse
from django.contrib.auth.decorators import user_passes_test
# from smtplib import SMTPException, SMTPAuthenticationError, SMTPConnectError, SMTPServerDisconnected
# from django.utils import timezone
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.urls import reverse
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.shortcuts import render
from django.conf import settings
from django.contrib.auth import get_user_model
from .utils import send_password_reset_email  # Import our helper function
from .models import (
    Organization, Membership, OrganizationBranding, UserAccessProfile,
    TenantMembership, TenantPermissionGrant, SupportAccessSession,
)
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from users.permissions import (
    PermissionCode,
    permissions_for_membership, require_permission, validate_delegated_grant,
)
from users.tenancy import require_organization
from .forms import OrganizationBrandingForm, SupportAccessForm, TenantMemberInviteForm

# Create your views here.

User = get_user_model()


TEAM_PERMISSION_GROUPS = (
    ("Customers", "Customer records and follow-up", (
        (PermissionCode.CUSTOMERS_VIEW, "View customers"),
        (PermissionCode.CUSTOMERS_CREATE, "Create customers"),
        (PermissionCode.CUSTOMERS_UPDATE, "Edit customers"),
    )),
    ("Packages & services", "Service catalogue access", (
        (PermissionCode.PACKAGES_VIEW, "View packages and services"),
    )),
    ("Products", "Product catalogue access", (
        (PermissionCode.PRODUCTS_VIEW, "View products"),
    )),
    ("Quotations", "Sales quotation workflow", (
        (PermissionCode.SALES_DOCUMENTS_CREATE, "Create quotations"),
        (PermissionCode.QUOTATIONS_UPDATE_OWN_DRAFT, "Edit own draft quotations"),
        (PermissionCode.QUOTATIONS_SEND, "Send quotations"),
    )),
    ("Invoices & payments", "Assigned sales documents only", (
        (PermissionCode.INVOICES_UPDATE_OWN_DRAFT, "Edit own draft invoices"),
        (PermissionCode.INVOICES_ISSUE_OWN, "Issue own invoices"),
        (PermissionCode.PAYMENTS_RECORD_OWN, "Record invoice payments"),
        (PermissionCode.RECEIPTS_VIEW, "View own receipts"),
        (PermissionCode.RECEIPTS_ISSUE_OWN, "Issue own receipts"),
    )),
    ("Inventory", "Operational stock visibility", (
        (PermissionCode.INVENTORY_VIEW, "View inventory"),
    )),
)


ROLE_DESCRIPTIONS = {
    TenantMembership.BaseRole.ADMIN_MANAGER: "Runs workspace operations, approvals, reports, and team access.",
    TenantMembership.BaseRole.SALES: "Handles assigned sales documents and invoice payments; no business-wide financial visibility.",
    TenantMembership.BaseRole.TECHNICIAN: "Creates, submits, and tracks only their own Technician Work Reports.",
}


def _member_status(member):
    if not member.is_active:
        return "inactive", "Inactive"
    if not member.user.has_usable_password():
        return "invited", "Invited"
    return "active", "Active"


def _member_access_summary(member, effective):
    if member.base_role == TenantMembership.BaseRole.ADMIN_MANAGER:
        return "Full workspace operations"
    if member.base_role == TenantMembership.BaseRole.SALES:
        return "Sales workflow only"
    if effective:
        return "Additional access configured"
    return "No access configured"


def _member_effective_summary(member, effective):
    if member.base_role == TenantMembership.BaseRole.ADMIN_MANAGER:
        return "Can run workspace operations, approvals, reports, and team access. Financial controls remain policy-protected."
    if member.base_role == TenantMembership.BaseRole.SALES:
        return "Can manage customers and assigned quotations, invoices, permitted payments, and receipts. Cannot view purchases, costs, profit, receivables, tenant-wide sales, or other users’ financial records."
    if effective:
        return "Has only the additional operational permissions selected below. Financial visibility and platform access remain restricted."
    return "No operational access is configured. Financial visibility and platform access remain restricted."


def _member_permission_groups(member, effective):
    if member.base_role == TenantMembership.BaseRole.ADMIN_MANAGER:
        return []
    granted = set(member.granted_action_codes)
    groups = []
    for title, helper, definitions in TEAM_PERMISSION_GROUPS:
        items = []
        enabled_count = 0
        for code, label in definitions:
            inherited = code in effective and code not in granted
            granted_exception = code in granted
            if inherited or granted_exception:
                enabled_count += 1
            items.append({
                "code": code,
                "label": label,
                "inherited": inherited,
                "granted": granted_exception,
            })
        groups.append({"title": title, "helper": helper, "items": items, "enabled_count": enabled_count})
    return groups


def _access_change_description(log):
    if log.action == "security.member.invited":
        return "Invited this member"
    if log.action == "security.member.activation_resent":
        return "Resent the account setup email"
    if log.action == "security.member.activated":
        return "Reactivated workspace access"
    if log.action == "security.member.deactivated":
        return "Deactivated workspace access"
    if log.action == "security.member.access_changed":
        previous_role = (log.old_value or {}).get("base_role")
        new_role = (log.new_value or {}).get("base_role")
        if previous_role and new_role and previous_role != new_role:
            previous = dict(TenantMembership.BaseRole.choices).get(previous_role, previous_role)
            current = dict(TenantMembership.BaseRole.choices).get(new_role, new_role)
            return f"Changed role from {previous} to {current}"
        return "Updated additional permissions"
    return "Updated workspace access"


def _team_access_context(request, *, invite_form=None, invite_open=False):
    tenant = require_organization(request)
    require_permission(request, PermissionCode.USERS_VIEW)
    actor = request.membership
    members = TenantMembership.objects.filter(tenant=tenant).select_related("user").prefetch_related("permission_grants")
    if actor.base_role != TenantMembership.BaseRole.SUPER_ADMIN:
        members = members.exclude(base_role=TenantMembership.BaseRole.SUPER_ADMIN)

    all_members = list(members)
    for member in all_members:
        member.status_key, member.status_label = _member_status(member)
        member.display_name = member.user.get_full_name().strip() or member.user.username or member.user.email
        member.initials = "".join(part[0] for part in member.display_name.split()[:2]).upper() or "?"
        member.effective_permissions = permissions_for_membership(member)
        member.access_summary = _member_access_summary(member, member.effective_permissions)
        member.effective_summary = _member_effective_summary(member, member.effective_permissions)
        member.role_description = ROLE_DESCRIPTIONS.get(member.base_role, "Platform-managed access.")
        member.permission_groups = _member_permission_groups(member, member.effective_permissions)
        member.can_manage = actor.base_role == TenantMembership.BaseRole.ADMIN_MANAGER and member.pk != actor.pk and member.base_role != TenantMembership.BaseRole.SUPER_ADMIN

    query = request.GET.get("q", "").strip()
    role_filter = request.GET.get("role", "")
    status_filter = request.GET.get("status", "")
    sort = request.GET.get("sort", "name")
    filtered = all_members
    if query:
        query_lower = query.lower()
        filtered = [member for member in filtered if query_lower in member.display_name.lower() or query_lower in (member.user.email or "").lower()]
    if role_filter in {TenantMembership.BaseRole.ADMIN_MANAGER, TenantMembership.BaseRole.SALES, TenantMembership.BaseRole.TECHNICIAN}:
        filtered = [member for member in filtered if member.base_role == role_filter]
    if status_filter in {"active", "invited", "inactive"}:
        filtered = [member for member in filtered if member.status_key == status_filter]
    if sort == "recent":
        filtered.sort(key=lambda member: (member.created_at, member.pk), reverse=True)
    elif sort == "role":
        filtered.sort(key=lambda member: (member.get_base_role_display().lower(), member.display_name.lower()))
    else:
        sort = "name"
        filtered.sort(key=lambda member: member.display_name.lower())

    events = AuditLog.objects.filter(
        tenant=tenant,
        object_type="TenantMembership",
        object_id__in=[str(member.pk) for member in all_members],
        action__in=["security.member.invited", "security.member.activation_resent", "security.member.access_changed", "security.member.activated", "security.member.deactivated"],
    ).select_related("actor").order_by("-performed_at", "-created_at")
    events_by_member = {}
    for event in events:
        member_events = events_by_member.setdefault(event.object_id, [])
        if len(member_events) < 3:
            event.description = _access_change_description(event)
            event.actor_name = event.actor.get_full_name().strip() if event.actor else "System"
            event.actor_name = event.actor_name or (event.actor.username if event.actor else "System")
            member_events.append(event)
    for member in all_members:
        member.recent_access_events = events_by_member.get(str(member.pk), [])

    counts = {
        "total": len(all_members),
        "active": sum(member.status_key == "active" for member in all_members),
        "invited": sum(member.status_key == "invited" for member in all_members),
        "inactive": sum(member.status_key == "inactive" for member in all_members),
    }
    return {
        "members": filtered,
        "member_count": len(filtered),
        "team_counts": counts,
        "invite_form": invite_form or TenantMemberInviteForm(),
        "invite_open": invite_open,
        "team_can_manage": actor.base_role == TenantMembership.BaseRole.ADMIN_MANAGER,
        "query": query,
        "role_filter": role_filter,
        "status_filter": status_filter,
        "sort": sort,
    }


def _send_workspace_activation_email(request, *, user, tenant):
    """Send a one-time first-password link for a newly provisioned user."""
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    activation_url = request.build_absolute_uri(
        reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})
    )
    context = {"user": user, "tenant": tenant, "activation_url": activation_url}
    message = EmailMultiAlternatives(
        subject=f"{settings.EMAIL_SUBJECT_PREFIX}Set up your {tenant.name} workspace account",
        body=render_to_string("registration/workspace_invitation_email.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    message.attach_alternative(
        render_to_string("registration/workspace_invitation_email.html", context),
        "text/html",
    )
    message.send(fail_silently=False)

def custom_password_reset(request):
    """Custom password reset implementation for development environment"""
    if request.method == 'POST':
        email = request.POST.get('email', '')
        user = User.objects.filter(email=email).first()
        
        if user:
            # Generate token
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            
            # Always use the request's host for development
            domain = request.get_host()  # This will be localhost:8000 usually
            protocol = 'http'  # Use http for development
            
            # Explicitly build the reset URL with localhost domain
            reset_url = f"{protocol}://{domain}/reset/{uid}/{token}/"
            
            # Render the email template with the correct URL
            context = {
                'user': user,
                'reset_url': reset_url,
                'protocol': protocol,
                'domain': domain,
                'uid': uid,
                'token': token,
                'site_name': 'JS Internet Services',
            }
            
            email_subject = 'Reset Your JS Internet Services Password'
            email_body = render_to_string('registration/password_reset_email_custom.html', context)
            
            # Send email with proper headers to avoid spam filters
            email = EmailMessage(
                subject=email_subject,
                body=email_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
                headers={
                    'List-Unsubscribe': f'<mailto:unsubscribe@example.com>',
                    'X-Priority': '1',  # High priority
                    'X-MSMail-Priority': 'High',
                    'Importance': 'High',
                }
            )
            email.content_subtype = "html"  # Set content type to HTML
            email.send(fail_silently=False)
            
            return HttpResponse("Password reset email sent! Please check your inbox and spam folder.<br><br>"
                               f"<strong>Development Mode:</strong> The reset link will be: {reset_url}")
        
        # Always return success even if email not found to prevent user enumeration
        return HttpResponse("If your email is registered, you will receive password reset instructions.")
    
    # Show the form
    return render(request, 'registration/password_reset_form.html')

def register(request):
    # Tenant accounts are provisioned through the audited Team & Access workflow.
    raise PermissionDenied("Public self-registration is disabled; use Team & Access.")
    # Legacy implementation retained temporarily below for migration reference.
    if request.method == 'POST':
        form = UserRegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            username = form.cleaned_data.get('username')
            messages.success(request, f'Account created for {username}!')

            org = Organization.objects.filter(slug='default', is_active=True).first()
            if org:
                Membership.objects.get_or_create(
                    organization=org,
                    user=user,
                    defaults={'role': 'member', 'is_active': True},
                )
                UserAccessProfile.objects.get_or_create(
                    user=user,
                    defaults={
                        "tenant": org,
                        "role": UserAccessProfile.Role.TENANT_STAFF,
                    },
                )

            # Automatically log in user after registration
            raw_password = form.cleaned_data.get('password1')
            user = authenticate(username=username, password=raw_password)
            login(request, user)
            return redirect('customer-list')
    else:
        form = UserRegisterForm()
    return render(request, 'auth/register.html', {'form': form})


@login_required
def branding_settings(request):
    organization = require_organization(request)
    require_permission(request, PermissionCode.BILLING_SETTINGS_CHANGE)

    branding, _ = OrganizationBranding.objects.get_or_create(organization=organization)

    if request.method == "POST":
        form = OrganizationBrandingForm(request.POST, request.FILES, instance=branding)
        if form.is_valid():
            form.save()
            messages.success(request, "Branding updated.")
            return redirect("branding_settings")
    else:
        form = OrganizationBrandingForm(instance=branding)

    return render(request, "users/branding_settings.html", {"form": form, "branding": branding, "organization": organization})


def _request_metadata(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return {
        "ip": (forwarded.split(",", 1)[0].strip() if forwarded else request.META.get("REMOTE_ADDR", ""))[:64],
        "method": request.method,
        "path": request.path[:500],
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:500],
    }


@login_required
def team_access(request):
    return render(request, "users/team_access.html", _team_access_context(request))


@login_required
@require_POST
@transaction.atomic
def invite_member(request):
    tenant = require_organization(request)
    require_permission(request, PermissionCode.USERS_INVITE)
    actor = request.membership
    form = TenantMemberInviteForm(request.POST)
    if not form.is_valid():
        return render(request, "users/team_access.html", _team_access_context(request, invite_form=form, invite_open=True), status=400)
    email = form.cleaned_data["email"].strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user and (user.is_superuser or user.tenant_memberships.filter(base_role=TenantMembership.BaseRole.SUPER_ADMIN).exists()):
        raise PermissionDenied("Super Administrator accounts are platform-managed.")
    new_user = user is None
    if new_user:
        base_username = email.split("@", 1)[0][:140] or "member"
        username = base_username
        suffix = 1
        while User.objects.filter(username=username).exists():
            suffix += 1
            username = f"{base_username[:140-len(str(suffix))]}{suffix}"
        user = User.objects.create(
            username=username, email=email,
            first_name=form.cleaned_data["first_name"], last_name=form.cleaned_data["last_name"],
        )
        user.set_unusable_password()
        user.save(update_fields=["password"])
    membership, created = TenantMembership.objects.get_or_create(
        tenant=tenant, user=user,
        defaults={"base_role": form.cleaned_data["base_role"], "invited_by": actor, "is_active": True},
    )
    if not created:
        messages.error(request, "That user already belongs to this company.")
        return redirect("team_access")
    if new_user:
        try:
            _send_workspace_activation_email(request, user=user, tenant=tenant)
        except Exception:
            transaction.set_rollback(True)
            messages.error(request, "The invitation could not be sent. Check the email configuration and try again.")
            return redirect("team_access")
    AuditLog.objects.create(
        organization=tenant, tenant=tenant, actor=request.user,
        action="security.member.invited", object_type="TenantMembership", object_id=str(membership.pk),
        new_value={"user_id": user.pk, "base_role": membership.base_role, "is_active": True, "activation_email_sent": new_user},
        metadata=_request_metadata(request),
    )
    if new_user:
        messages.success(request, "Invitation sent. The member can use the email link to set a password and log in.")
    else:
        messages.success(request, "Member added to this workspace. They can log in with their existing account.")
    return redirect("team_access")


@login_required
@require_POST
def resend_member_activation(request, membership_id):
    tenant = require_organization(request)
    require_permission(request, PermissionCode.USERS_INVITE)
    target = get_object_or_404(
        TenantMembership.objects.select_related("user"), pk=membership_id, tenant=tenant
    )
    if target.base_role == TenantMembership.BaseRole.SUPER_ADMIN:
        raise PermissionDenied("Super Administrator accounts are platform-managed.")
    if not target.is_active or target.user.has_usable_password():
        messages.error(request, "A setup email can only be resent to an active pending invitation.")
        return redirect("team_access")
    try:
        _send_workspace_activation_email(request, user=target.user, tenant=tenant)
    except Exception:
        messages.error(request, "The setup email could not be sent. Check the email configuration and try again.")
        return redirect("team_access")
    AuditLog.objects.create(
        organization=tenant, tenant=tenant, actor=request.user,
        action="security.member.activation_resent", object_type="TenantMembership", object_id=str(target.pk),
        metadata=_request_metadata(request),
    )
    messages.success(request, "A new account setup email was sent.")
    return redirect("team_access")


@login_required
@require_POST
@transaction.atomic
def update_member_access(request, membership_id):
    tenant = require_organization(request)
    require_permission(request, PermissionCode.USERS_UPDATE_ACCESS)
    target = get_object_or_404(TenantMembership.objects.select_for_update(), pk=membership_id, tenant=tenant)
    actor = request.membership
    if target.pk == actor.pk:
        raise PermissionDenied("Users cannot change their own access.")
    if target.base_role == TenantMembership.BaseRole.SUPER_ADMIN:
        raise PermissionDenied("Super Administrator accounts are platform-managed.")
    role = request.POST.get("base_role", target.base_role)
    if role not in {TenantMembership.BaseRole.ADMIN_MANAGER, TenantMembership.BaseRole.SALES, TenantMembership.BaseRole.TECHNICIAN}:
        raise PermissionDenied("That role cannot be assigned by a tenant manager.")
    requested = set(request.POST.getlist("permissions"))
    scope = request.POST.get("scope", TenantPermissionGrant.Scope.OWN)
    for action in requested:
        validate_delegated_grant(actor_membership=actor, target_membership=target, action_code=action, scope=scope)
    before = {"base_role": target.base_role, "permissions": sorted(target.permission_grants.values_list("action_code", flat=True))}
    target.base_role = role
    target.full_clean()
    target.save(update_fields=["base_role", "updated_at"])
    target.permission_grants.all().delete()
    TenantPermissionGrant.objects.bulk_create([
        TenantPermissionGrant(membership=target, action_code=action, scope=scope, granted_by=actor)
        for action in sorted(requested)
    ])
    AuditLog.objects.create(
        organization=tenant, tenant=tenant, actor=request.user,
        action="security.member.access_changed", object_type="TenantMembership", object_id=str(target.pk),
        old_value=before, new_value={"base_role": role, "permissions": sorted(requested), "scope": scope},
        metadata=_request_metadata(request),
    )
    messages.success(request, "Access updated.")
    return redirect("team_access")


@login_required
@require_POST
@transaction.atomic
def change_member_status(request, membership_id):
    tenant = require_organization(request)
    require_permission(request, PermissionCode.USERS_DEACTIVATE)
    target = get_object_or_404(TenantMembership.objects.select_for_update(), pk=membership_id, tenant=tenant)
    if target.pk == request.membership.pk or target.base_role == TenantMembership.BaseRole.SUPER_ADMIN:
        raise PermissionDenied("This membership cannot be changed here.")
    old = target.is_active
    target.is_active = request.POST.get("active") == "1"
    target.save(update_fields=["is_active", "updated_at"])
    AuditLog.objects.create(
        organization=tenant, tenant=tenant, actor=request.user,
        action="security.member.activated" if target.is_active else "security.member.deactivated",
        object_type="TenantMembership", object_id=str(target.pk),
        old_value={"is_active": old}, new_value={"is_active": target.is_active},
        metadata=_request_metadata(request),
    )
    return redirect("team_access")


def _require_super_admin(request):
    membership = TenantMembership.objects.filter(
        user=request.user, is_active=True, base_role=TenantMembership.BaseRole.SUPER_ADMIN
    ).first()
    if membership is None:
        raise PermissionDenied("Super Administrator access is required.")
    return membership


@login_required
def start_support_access(request):
    _require_super_admin(request)
    if request.method == "POST":
        form = SupportAccessForm(request.POST)
        if form.is_valid():
            if request.session.session_key is None:
                request.session.create()
            SupportAccessSession.objects.filter(
                actor=request.user, session_key=request.session.session_key, ended_at__isnull=True
            ).update(ended_at=timezone.now())
            tenant = form.cleaned_data["tenant_id"]
            support = SupportAccessSession.objects.create(
                actor=request.user, tenant=tenant, reason=form.cleaned_data["reason"],
                session_key=request.session.session_key,
            )
            request.session["support_access_session_id"] = support.pk
            AuditLog.objects.create(
                organization=tenant, tenant=tenant, actor=request.user,
                action="security.support_context.entered", object_type="SupportAccessSession", object_id=str(support.pk),
                new_value={"tenant_id": tenant.pk, "reason": support.reason}, metadata=_request_metadata(request),
            )
            return redirect("main_app:workspace_home")
    else:
        form = SupportAccessForm()
    return render(request, "users/support_access.html", {"form": form})


@login_required
@require_POST
def exit_support_access(request):
    _require_super_admin(request)
    support = SupportAccessSession.objects.filter(
        pk=request.session.get("support_access_session_id"), actor=request.user, ended_at__isnull=True
    ).select_related("tenant").first()
    if support:
        AuditLog.objects.create(
            organization=support.tenant, tenant=support.tenant, actor=request.user,
            action="security.support_context.exited", object_type="SupportAccessSession", object_id=str(support.pk),
            metadata=_request_metadata(request),
        )
        support.ended_at = timezone.now()
        support.save(update_fields=["ended_at", "last_used_at"])
    request.session.pop("support_access_session_id", None)
    return redirect("start_support_access")


# Set up a logger for email-related issues
# logger = logging.getLogger('email_tests')

# @user_passes_test(lambda u: u.is_superuser)  # Only superusers can access this test view
# def test_email(request):
#     sender = 'dullakalal360@gmail.com'
#     recipient = 'kilionetrekkingandsafari@gmail.com'
#     test_results = []
    
#     # Test 1: Check DNS resolution for SMTP server
#     try:
#         socket.gethostbyname('smtp.gmail.com')
#         test_results.append("✅ DNS resolution for smtp.gmail.com successful")
#     except socket.gaierror:
#         test_results.append("❌ DNS resolution for smtp.gmail.com failed - network or DNS issue")
    
#     # Test 2: Check if port 587 is reachable
#     try:
#         sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#         sock.settimeout(5)
#         result = sock.connect_ex(('smtp.gmail.com', 587))
#         if result == 0:
#             test_results.append("✅ Connection to smtp.gmail.com:587 successful")
#         else:
#             test_results.append(f"❌ Connection to smtp.gmail.com:587 failed with error code {result}")
#         sock.close()
#     except Exception as e:
#         test_results.append(f"❌ Socket connection error: {str(e)}")
    
#     # Test 3: Try sending email
#     try:
#         send_mail(
#             'Test Email from JS Internet Services',
#             'This is a test email to verify the email configuration is working properly. Sent at: ' + 
#             str(timezone.now()),
#             sender,
#             [recipient],
#             fail_silently=False,
#         )
#         test_results.append(f"✅ Email sent successfully to {recipient}")
#         logger.info(f"Test email sent successfully to {recipient}")
#     except SMTPAuthenticationError as e:
#         error_message = f"❌ SMTP Authentication Error: {str(e)}"
#         test_results.append(error_message)
#         logger.error(error_message)
#     except SMTPConnectError as e:
#         error_message = f"❌ SMTP Connection Error: {str(e)}"
#         test_results.append(error_message)
#         logger.error(error_message)
#     except SMTPServerDisconnected as e:
#         error_message = f"❌ SMTP Server Disconnected: {str(e)}"
#         test_results.append(error_message)
#         logger.error(error_message)
#     except SMTPException as e:
#         error_message = f"❌ SMTP Error: {str(e)}"
#         test_results.append(error_message)
#         logger.error(error_message)
#     except Exception as e:
#         error_message = f"❌ Unexpected Error: {str(e)}"
#         test_results.append(error_message)
#         logger.error(error_message)
    
#     # Add email configuration details to the response (with password partially masked)
#     from django.conf import settings
#     email_config = {
#         'EMAIL_BACKEND': settings.EMAIL_BACKEND,
#         'EMAIL_HOST': settings.EMAIL_HOST,
#         'EMAIL_PORT': settings.EMAIL_PORT,
#         'EMAIL_USE_TLS': settings.EMAIL_USE_TLS,
#         'EMAIL_HOST_USER': settings.EMAIL_HOST_USER,
#         'EMAIL_HOST_PASSWORD': '****' + settings.EMAIL_HOST_PASSWORD[-4:] if settings.EMAIL_HOST_PASSWORD else 'Not set',
#         'DEFAULT_FROM_EMAIL': settings.DEFAULT_FROM_EMAIL,
#     }
    
#     config_details = "<h3>Current Email Configuration:</h3>"
#     for key, value in email_config.items():
#         config_details += f"<p><strong>{key}:</strong> {value}</p>"
    
#     # Format results into a nice HTML response
#     results_html = "<h3>Email System Tests:</h3><ul>"
#     for result in test_results:
#         results_html += f"<li>{result}</li>"
#     results_html += "</ul>"
    
#     return HttpResponse(
#         f"<h1>Email System Test Results</h1>{results_html}{config_details}"
#         f"<p>If email was sent successfully but not received, please check:</p>"
#         f"<ul>"
#         f"<li>Spam/junk folder in {recipient}</li>"
#         f"<li>Gmail sending limits (especially for new accounts)</li>"
#         f"<li>Gmail's App Password settings</li>"
#         f"<li>Gmail's 'Less secure app access' settings (if applicable)</li>"
#         f"</ul>"
#     )

# # You can also add a view to check email delivery more thoroughly
# @user_passes_test(lambda u: u.is_superuser)
# def test_email_multiple(request):
#     """Test email delivery to multiple providers to identify if issue is provider-specific"""
#     test_emails = [
#         'kilionetrekkingandsafari@gmail.com',  # Gmail
#         # Add your other test emails here (e.g., Outlook, Yahoo, etc.)
#     ]
    
#     results = []
#     for recipient in test_emails:
#         try:
#             send_mail(
#                 f'This is a test email sent to {recipient} at {timezone.now()}',
#                 f'This is a test email sent to {recipient} at {str(timezone.now())}',
#                 'dullakalal360@gmail.com',
#                 [recipient],
#                 fail_silently=False,
#             )
#             results.append(f"✅ Email to {recipient}: SENT")
#         except Exception as e:
#             results.append(f"❌ Email to {recipient}: FAILED - {str(e)}")
    
#     results_html = "<h3>Multiple Provider Test Results:</h3><ul>"
#     for result in results:
#         results_html += f"<li>{result}</li>"
#     results_html += "</ul>"
    
#     return HttpResponse(f"<h1>Multiple Email Provider Test</h1>{results_html}")

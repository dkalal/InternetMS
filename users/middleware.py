from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.utils.deprecation import MiddlewareMixin

from audit.models import AuditLog
from .models import Membership, Organization, SupportAccessSession, TenantMembership, UserAccessProfile
from .tenant_context import clear_current_tenant, set_current_tenant


class ActiveOrganizationMiddleware(MiddlewareMixin):
    SESSION_KEY = "active_tenant_id"
    LEGACY_SESSION_KEY = "active_org_id"
    SUPPORT_SESSION_KEY = "support_access_session_id"

    def process_request(self, request):
        request.organization = request.tenant = request.membership = None
        request.user_role = None
        request.support_access = None
        clear_current_tenant()
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None

        memberships = TenantMembership.objects.select_related("tenant").filter(
            user=user, is_active=True, tenant__is_active=True
        )
        if not memberships.exists():
            # Transitional bridge for users created by legacy scripts. It derives
            # access only from trusted server-side flags/profile data.
            profile = getattr(user, "access_profile", None)
            tenant = getattr(profile, "tenant", None)
            legacy = Membership.objects.filter(user=user, is_active=True, organization__is_active=True).order_by("pk").first()
            tenant = tenant or (legacy.organization if legacy else None)
            if user.is_superuser:
                tenant = tenant or Organization.objects.filter(is_active=True).order_by("created_at", "pk").first()
                role = TenantMembership.BaseRole.SUPER_ADMIN
            elif tenant is not None:
                role = (
                    TenantMembership.BaseRole.ADMIN_MANAGER
                    if (profile and profile.role == UserAccessProfile.Role.TENANT_ADMIN)
                    or (legacy and legacy.role in {Membership.Role.OWNER, Membership.Role.ADMIN})
                    else TenantMembership.BaseRole.SALES
                )
            else:
                role = None
            if tenant is not None and role is not None:
                TenantMembership.objects.get_or_create(
                    tenant=tenant, user=user, defaults={"base_role": role, "is_active": user.is_active}
                )
                memberships = TenantMembership.objects.select_related("tenant").filter(
                    user=user, is_active=True, tenant__is_active=True
                )
        super_membership = memberships.filter(base_role=TenantMembership.BaseRole.SUPER_ADMIN).first()
        if super_membership is not None:
            request.membership = super_membership
            request.user_role = TenantMembership.BaseRole.SUPER_ADMIN
            support_id = request.session.get(self.SUPPORT_SESSION_KEY)
            support = SupportAccessSession.objects.select_related("tenant").filter(
                pk=support_id,
                actor=user,
                session_key=request.session.session_key or "",
                ended_at__isnull=True,
                tenant__is_active=True,
            ).first()
            if support is not None:
                request.support_access = support
                request.tenant = request.organization = support.tenant
                set_current_tenant(support.tenant, scope_required=True)
                if not request.path.startswith(("/static/", "/health", "/ready", "/alive")):
                    AuditLog.objects.create(
                        organization=support.tenant,
                        tenant=support.tenant,
                        actor=user,
                        action="security.support_request",
                        object_type="Request",
                        object_id=str(support.pk),
                        metadata={
                            "method": request.method,
                            "path": request.path[:500],
                            "ip": _client_ip(request),
                            "support_reason": support.reason,
                        },
                    )
            else:
                request.session.pop(self.SUPPORT_SESSION_KEY, None)
                set_current_tenant(None, scope_required=True)
            return None

        active_id = request.session.get(self.SESSION_KEY) or request.session.get(self.LEGACY_SESSION_KEY)
        membership = memberships.filter(tenant_id=active_id).first() if active_id else None
        membership = membership or memberships.order_by("created_at", "pk").first()
        if membership is None:
            raise PermissionDenied("An active tenant membership is required.")
        request.membership = membership
        request.user_role = membership.base_role
        request.tenant = request.organization = membership.tenant
        request.session[self.SESSION_KEY] = membership.tenant_id
        set_current_tenant(membership.tenant, scope_required=True)
        return None

    def process_response(self, request, response):
        clear_current_tenant()
        return response

    def process_exception(self, request, exception):
        if isinstance(exception, PermissionDenied) and getattr(request, "tenant", None) is not None:
            path = getattr(request, "path", "")
            if path.startswith(("/billing/", "/inventory/reports", "/inventory/purchases", "/inventory/suppliers")):
                AuditLog.objects.create(
                    organization=request.tenant, tenant=request.tenant,
                    actor=request.user if request.user.is_authenticated else None,
                    action="security.financial_permission_denied", object_type="Request", object_id="",
                    metadata={"method": request.method, "path": path[:500], "ip": _client_ip(request)},
                )
        clear_current_tenant()
        return None


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",", 1)[0].strip() if forwarded else request.META.get("REMOTE_ADDR", ""))[:64]

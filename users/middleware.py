from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.utils.deprecation import MiddlewareMixin

from .models import Membership, UserAccessProfile
from .tenant_context import clear_current_tenant, set_current_tenant


class ActiveOrganizationMiddleware(MiddlewareMixin):
    """
    Sets `request.tenant`/`request.organization`, `request.user_role`, and `request.membership`.

    Strict behavior:
    - SUPER_ADMIN has no tenant context by default.
    - Non-super-admin users must have a tenant.
    - ORM tenant scoping is enabled for authenticated users.
    """

    SESSION_KEY = "active_org_id"

    def process_request(self, request):
        request.organization = None
        request.tenant = None
        request.membership = None
        request.user_role = None

        clear_current_tenant()
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None

        profile = getattr(user, "access_profile", None)
        if profile is None:
            membership = (
                Membership.objects.select_related("organization")
                .filter(user=user, is_active=True, organization__is_active=True)
                .order_by("organization__created_at")
                .first()
            )
            role = UserAccessProfile.Role.SUPER_ADMIN if user.is_superuser else UserAccessProfile.Role.TENANT_STAFF
            tenant = None if role == UserAccessProfile.Role.SUPER_ADMIN else (membership.organization if membership else None)
            profile = UserAccessProfile.objects.create(user=user, tenant=tenant, role=role)

        request.user_role = profile.role

        membership_qs = Membership.objects.select_related("organization").filter(
            user=user, is_active=True, organization__is_active=True
        )
        active_org_id = request.session.get(self.SESSION_KEY)
        membership = membership_qs.filter(organization_id=active_org_id).first() if active_org_id else None
        if membership is None and profile.tenant_id:
            membership = membership_qs.filter(organization_id=profile.tenant_id).first()
        if membership is None:
            membership = membership_qs.order_by("organization__created_at").first()

        request.membership = membership

        if profile.role == UserAccessProfile.Role.SUPER_ADMIN:
            request.tenant = None
            request.organization = None
            set_current_tenant(None, scope_required=True)
            return None

        if profile.tenant is None:
            raise PermissionDenied("Tenant assignment is required.")

        # Derive an effective tenant role from the organization membership.
        # This keeps UI + permissions consistent for "tenant admins" even if their
        # access_profile.role was never explicitly promoted.
        if membership is not None and membership.role in {Membership.Role.OWNER, Membership.Role.ADMIN}:
            request.user_role = UserAccessProfile.Role.TENANT_ADMIN
        elif membership is not None:
            request.user_role = UserAccessProfile.Role.TENANT_STAFF

        request.tenant = profile.tenant
        request.organization = profile.tenant
        request.session[self.SESSION_KEY] = profile.tenant_id
        set_current_tenant(profile.tenant, scope_required=True)
        return None

    def process_response(self, request, response):
        clear_current_tenant()
        return response

    def process_exception(self, request, exception):
        clear_current_tenant()
        return None

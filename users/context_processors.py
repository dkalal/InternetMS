from django.core.exceptions import ObjectDoesNotExist

from .permissions import PermissionCode, permissions_for_membership


def active_organization(request):
    branding = None
    organization = getattr(request, "organization", None)
    if organization is not None:
        try:
            branding = organization.branding
        except ObjectDoesNotExist:
            branding = None

    effective = permissions_for_membership(getattr(request, 'membership', None)) if organization else set()
    return {
        'ACTIVE_ORGANIZATION': organization,
        'ACTIVE_MEMBERSHIP': getattr(request, 'membership', None),
        'ACTIVE_BRANDING': branding,
        'ACTIVE_PERMISSIONS': effective,
        'FINANCE_CAN_VIEW_ALL': PermissionCode.FINANCE_SALES_VIEW_ALL in effective,
        'SUPPORT_ACCESS': getattr(request, 'support_access', None),
    }

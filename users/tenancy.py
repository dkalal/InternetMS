from django.core.exceptions import PermissionDenied


def require_organization(request):
    organization = getattr(request, "organization", None)
    if organization is None:
        raise PermissionDenied("No active tenant selected.")
    return organization


def require_tenant(request):
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        raise PermissionDenied("No active tenant selected.")
    return tenant

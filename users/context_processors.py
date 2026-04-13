from django.core.exceptions import ObjectDoesNotExist


def active_organization(request):
    branding = None
    organization = getattr(request, "organization", None)
    if organization is not None:
        try:
            branding = organization.branding
        except ObjectDoesNotExist:
            branding = None

    return {
        'ACTIVE_ORGANIZATION': organization,
        'ACTIVE_MEMBERSHIP': getattr(request, 'membership', None),
        'ACTIVE_BRANDING': branding,
    }

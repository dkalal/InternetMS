from rest_framework.permissions import SAFE_METHODS, BasePermission

from .services import resolve_integration_consumer


class IsActiveIntegrationConsumer(BasePermission):
    message = 'A valid active integration consumer token is required.'

    def has_permission(self, request, view):
        if request.method not in SAFE_METHODS:
            return False
        consumer = resolve_integration_consumer(request)
        return consumer is not None


class IsActiveIntegrationConsumerWriter(BasePermission):
    """Allow an active, tenant-bound integration consumer to replace projections."""

    message = 'A valid active integration consumer token is required.'

    def has_permission(self, request, view):
        return request.method in {'PUT', 'OPTIONS'} and resolve_integration_consumer(request) is not None

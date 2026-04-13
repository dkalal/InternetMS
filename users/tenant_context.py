from __future__ import annotations

from contextvars import ContextVar


_current_tenant = ContextVar("current_tenant", default=None)
_scope_required = ContextVar("tenant_scope_required", default=False)


def set_current_tenant(tenant, *, scope_required: bool):
    _current_tenant.set(tenant)
    _scope_required.set(scope_required)


def clear_current_tenant():
    _current_tenant.set(None)
    _scope_required.set(False)


def get_current_tenant():
    return _current_tenant.get()


def is_scope_required() -> bool:
    return bool(_scope_required.get())


def scope_queryset(queryset, *, field_name: str = "tenant"):
    if not is_scope_required():
        return queryset
    tenant = get_current_tenant()
    if tenant is None:
        return queryset.none()
    return queryset.filter(**{field_name: tenant})

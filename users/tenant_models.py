from __future__ import annotations

from django.db import models

from .tenant_context import scope_queryset


class TenantScopedQuerySet(models.QuerySet):
    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)


class TenantScopedManager(models.Manager):
    def get_queryset(self):
        queryset = super().get_queryset()
        return scope_queryset(queryset, field_name="tenant")

    def unscoped(self):
        return super().get_queryset()

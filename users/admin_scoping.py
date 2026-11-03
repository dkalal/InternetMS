from django.contrib import admin


class TenantScopedAdmin(admin.ModelAdmin):
    """Django admin boundary for tenant-owned business rows.

    Super Administrators see these models only while middleware has established
    an explicit audited support tenant context. Tenant and legacy organization
    values are always derived from that context.
    """

    def _tenant(self, request):
        return getattr(request, "tenant", None)

    def get_queryset(self, request):
        tenant = self._tenant(request)
        queryset = super().get_queryset(request)
        return queryset.filter(tenant=tenant) if tenant is not None else queryset.none()

    def has_module_permission(self, request):
        return self._tenant(request) is not None and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        if self._tenant(request) is None:
            return False
        if obj is not None and getattr(obj, "tenant_id", None) != self._tenant(request).pk:
            return False
        return super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return self._tenant(request) is not None and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        if self._tenant(request) is None:
            return False
        if obj is not None and getattr(obj, "tenant_id", None) != self._tenant(request).pk:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if self._tenant(request) is None:
            return False
        if obj is not None and getattr(obj, "tenant_id", None) != self._tenant(request).pk:
            return False
        return super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        existing = list(super().get_readonly_fields(request, obj))
        field_names = {field.name for field in self.model._meta.fields}
        for name in ("tenant", "organization"):
            if name in field_names and name not in existing:
                existing.append(name)
        return tuple(existing)

    def save_model(self, request, obj, form, change):
        tenant = self._tenant(request)
        if tenant is None:
            raise PermissionError("Explicit tenant support context is required.")
        field_names = {field.name for field in obj._meta.fields}
        if "tenant" in field_names:
            obj.tenant = tenant
        if "organization" in field_names:
            obj.organization = tenant
        super().save_model(request, obj, form, change)

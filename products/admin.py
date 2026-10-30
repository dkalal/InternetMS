from django.contrib import admin
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError

from .models import Product, ProductCategory, UnitOfMeasure

# Register your models here.


class ProductAdminForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = '__all__'

    def clean_sku(self):
        from .sku import validate_manual_sku
        try:
            return validate_manual_sku(self.cleaned_data.get('sku'), existing=self.instance.sku if self.instance.pk else '')
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    form = ProductAdminForm
    list_display = ('sku', 'name', 'tenant', 'sales_unit', 'is_active')
    list_filter = ('tenant', 'is_active', 'item_type')
    search_fields = ('sku', 'name')

    def save_model(self, request, obj, form, change):
        if change:
            from users.permissions import PermissionCode, has_tenant_permission
            from inventory.services import audit
            if not has_tenant_permission(request.user, obj.tenant, PermissionCode.PRODUCT_MANAGE):
                raise PermissionDenied('Product management permission is required in this business.')
            previous = Product.objects.unscoped().get(pk=obj.pk, tenant=obj.tenant)
            super().save_model(request, obj, form, change)
            audit(organization=obj.tenant, actor=request.user, action='inventory.product.updated', obj=obj,
                  old_value={'sku': previous.sku}, new_value={'sku': obj.sku})
        else:
            from .creation import save_purchase_product
            save_purchase_product(obj, organization=obj.tenant, supplied_sku=obj.sku, actor=request.user)

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.has_unit_history():
            return ('sales_unit', 'measure_unit', 'item_type', 'track_stock', 'is_serialized')
        return ()

admin.site.register(ProductCategory)
admin.site.register(UnitOfMeasure)

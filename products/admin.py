from django.contrib import admin

from .models import Product, ProductCategory, UnitOfMeasure

# Register your models here.

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('sku', 'name', 'tenant', 'sales_unit', 'is_active')
    list_filter = ('tenant', 'is_active', 'item_type')
    search_fields = ('sku', 'name')

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.has_unit_history():
            return ('sales_unit', 'measure_unit', 'item_type', 'track_stock', 'is_serialized')
        return ()

admin.site.register(ProductCategory)
admin.site.register(UnitOfMeasure)

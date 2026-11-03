from django.contrib import admin

from .models import (
    Cart, HistoricalInventoryRecord, ImportJob, InventoryBalance, InventorySale,
    Purchase, PurchaseLine, StockAdjustment, StockMovement, StockUnit, Supplier,
)

for model in [Supplier, Purchase, PurchaseLine, InventoryBalance, StockMovement, StockUnit, StockAdjustment, InventorySale, ImportJob, HistoricalInventoryRecord]:
    admin.site.register(model)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    """Cart writes must use the authorized, locked lifecycle workflows."""
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

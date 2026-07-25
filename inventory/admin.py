from django.contrib import admin

from .models import (
    Cart, HistoricalInventoryRecord, ImportJob, InventoryBalance, InventorySale,
    Purchase, PurchaseLine, StockAdjustment, StockMovement, StockUnit, Supplier,
)

for model in [Supplier, Purchase, PurchaseLine, InventoryBalance, StockMovement, StockUnit, StockAdjustment, Cart, InventorySale, ImportJob, HistoricalInventoryRecord]:
    admin.site.register(model)

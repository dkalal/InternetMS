from django.urls import path

from .api import (
    InvoiceListCreateAPI,
    InvoicePaymentAPI,
    MovementListAPI,
    ProductDetailAPI,
    ProductListCreateAPI,
    StockListAPI,
    SupplierDetailAPI,
    SupplierListCreateAPI,
)

urlpatterns = [
    path('products/', ProductListCreateAPI.as_view(), name='api-products'),
    path('products/<int:pk>/', ProductDetailAPI.as_view(), name='api-product-detail'),
    path('suppliers/', SupplierListCreateAPI.as_view(), name='api-suppliers'),
    path('suppliers/<int:pk>/', SupplierDetailAPI.as_view(), name='api-supplier-detail'),
    path('stock/', StockListAPI.as_view(), name='api-stock'),
    path('stock-movements/', MovementListAPI.as_view(), name='api-stock-movements'),
    path('invoices/', InvoiceListCreateAPI.as_view(), name='api-invoices'),
    path('invoices/<int:pk>/pay/', InvoicePaymentAPI.as_view(), name='api-invoice-pay'),
]

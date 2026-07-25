from django.urls import path

from . import views

app_name = 'inventory'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('categories/', views.category_list, name='category_list'),
    path('categories/new/', views.category_form, name='category_create'),
    path('categories/<int:pk>/edit/', views.category_form, name='category_edit'),
    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/new/', views.supplier_form, name='supplier_create'),
    path('suppliers/<int:pk>/', views.supplier_detail, name='supplier_detail'),
    path('suppliers/<int:pk>/edit/', views.supplier_form, name='supplier_edit'),
    path('suppliers/<int:pk>/payments/new/', views.supplier_payment_create, name='supplier_payment_create'),
    path('purchases/', views.purchase_list, name='purchase_list'),
    path('purchases/new/', views.purchase_create, name='purchase_create'),
    path('purchases/<int:pk>/edit/', views.purchase_edit, name='purchase_edit'),
    path('purchases/<int:pk>/cancel/', views.purchase_cancel, name='purchase_cancel'),
    path('purchases/<int:pk>/', views.purchase_detail, name='purchase_detail'),
    path('purchases/<int:pk>/confirm/', views.purchase_confirm, name='purchase_confirm'),
    path('stock/', views.stock_list, name='stock_list'),
    path('stock/adjust/', views.stock_adjust, name='stock_adjust'),
    path('movements/', views.movement_list, name='movement_list'),
    path('carts/', views.cart_list, name='cart_list'),
    path('carts/new/', views.cart_create, name='cart_create'),
    path('carts/<int:pk>/', views.cart_detail, name='cart_detail'),
    path('carts/<int:cart_pk>/items/new/', views.cart_line_form, name='cart_line_create'),
    path('carts/<int:cart_pk>/items/adjust/', views.cart_line_adjust, name='cart_line_adjust'),
    path('carts/<int:cart_pk>/items/<int:line_pk>/edit/', views.cart_line_form, name='cart_line_edit'),
    path('carts/<int:cart_pk>/items/<int:line_pk>/delete/', views.cart_line_delete, name='cart_line_delete'),
    path('carts/<int:pk>/abandon/', views.cart_abandon, name='cart_abandon'),
    path('carts/<int:pk>/convert/<str:target>/', views.cart_convert, name='cart_convert'),
    path('reports/', views.report, name='reports'),
    path('reports/<slug:report_name>/', views.report, name='report'),
    path('imports/', views.import_data, name='import_data'),
    path('imports/<int:pk>/commit/', views.import_commit, name='import_commit'),
    path('imports/template/<str:import_type>/', views.import_template, name='import_template'),
    path('exports/<str:record_type>/', views.export_records, name='export_records'),
    path('invoices/<int:pk>/serials/', views.invoice_serials, name='invoice_serials'),
    path('settings/', views.settings_edit, name='settings'),
]

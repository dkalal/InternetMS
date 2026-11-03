from django.urls import path

from .views import (
    IntegrationCustomerAssetSnapshotView,
    IntegrationCustomerDetailView,
    IntegrationCustomerListView,
)


urlpatterns = [
    path('customers/', IntegrationCustomerListView.as_view(), name='integration-customer-list'),
    path('customers/<uuid:uuid>/', IntegrationCustomerDetailView.as_view(), name='integration-customer-detail'),
    path('customers/<uuid:uuid>/assets/', IntegrationCustomerAssetSnapshotView.as_view(), name='integration-customer-assets'),
]

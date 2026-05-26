from django.urls import path

from .views import IntegrationCustomerDetailView, IntegrationCustomerListView


urlpatterns = [
    path('customers/', IntegrationCustomerListView.as_view(), name='integration-customer-list'),
    path('customers/<uuid:uuid>/', IntegrationCustomerDetailView.as_view(), name='integration-customer-detail'),
]

from django.urls import path

from .views import IntegrationCustomerDetailView, IntegrationCustomerListView


urlpatterns = [
    path('', IntegrationCustomerListView.as_view(), name='integration-customer-alias-list'),
    path('<uuid:uuid>/', IntegrationCustomerDetailView.as_view(), name='integration-customer-alias-detail'),
]

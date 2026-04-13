from django.urls import path
from . import views

urlpatterns = [
    path('', views.CustomerListView.as_view(), name='customer-list'),
    path('<int:pk>/', views.CustomerDetailView.as_view(), name='customer-detail'),
    path('create/', views.CustomerCreateView.as_view(), name='customer-create'),
    path('<int:pk>/update/', views.CustomerUpdateView.as_view(), name='customer-update'),
    path('<int:pk>/delete/', views.CustomerDeleteView.as_view(), name='customer-delete'),
    path('<int:pk>/restore/', views.restore_customer, name='customer-restore'),
    path('<int:pk>/anonymize/', views.CustomerAnonymizeView.as_view(), name='customer-anonymize'),
    path('<int:pk>/hard-delete/', views.CustomerHardDeleteView.as_view(), name='customer-hard-delete'),
]

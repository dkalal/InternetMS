from django.urls import path
from . import views

urlpatterns = [
    path('', views.CustomerListView.as_view(), name='customer-list'),
    path('<int:pk>/', views.CustomerDetailView.as_view(), name='customer-detail'),
    path('create/', views.CustomerCreateView.as_view(), name='customer-create'),
    path('<int:pk>/update/', views.CustomerUpdateView.as_view(), name='customer-update'),
    path('<int:customer_id>/sites/create/', views.CustomerSiteCreateView.as_view(), name='customer-site-create'),
    path('sites/<int:pk>/update/', views.CustomerSiteUpdateView.as_view(), name='customer-site-update'),
    path('<int:customer_id>/services/create/', views.InternetServiceCreateView.as_view(), name='internet-service-create'),
    path('services/<int:pk>/', views.InternetServiceDetailView.as_view(), name='internet-service-detail'),
    path('services/<int:pk>/change-package/', views.ServicePackageChangeView.as_view(), name='internet-service-change-package'),
    path('services/<int:pk>/status/<str:action>/', views.ServiceStatusChangeView.as_view(), name='internet-service-status-change'),
    path('<int:pk>/delete/', views.CustomerDeleteView.as_view(), name='customer-delete'),
    path('<int:pk>/restore/', views.restore_customer, name='customer-restore'),
    path('<int:pk>/anonymize/', views.CustomerAnonymizeView.as_view(), name='customer-anonymize'),
    path('<int:pk>/hard-delete/', views.CustomerHardDeleteView.as_view(), name='customer-hard-delete'),
]

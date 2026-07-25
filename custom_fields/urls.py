from django.urls import path

from . import views


urlpatterns = [
    path("", views.CustomFieldDefinitionListView.as_view(), name="custom-field-list"),
    path("select-organization/", views.CustomFieldOrganizationSelectView.as_view(), name="custom-field-org-select"),
    path("create/", views.CustomFieldDefinitionCreateView.as_view(), name="custom-field-create"),
    path("inline-create/", views.CustomFieldInlineCreateView.as_view(), name="custom-field-inline-create"),
    path("<int:pk>/edit/", views.CustomFieldDefinitionUpdateView.as_view(), name="custom-field-edit"),
    path("<int:pk>/deactivate/", views.CustomFieldDefinitionDeactivateView.as_view(), name="custom-field-deactivate"),
]

from django.urls import path

from . import views

app_name = "work_reports"

urlpatterns = [
    path("", views.report_list, name="list"),
    path("new/", views.report_create, name="create"),
    path("approvals/", views.approval_queue, name="approval_queue"),
    path("<int:pk>/", views.report_detail, name="detail"),
    path("<int:pk>/edit/", views.report_edit, name="edit"),
    path("<int:pk>/submit/", views.report_submit, name="submit"),
    path("<int:pk>/approve/", views.report_approve, name="approve"),
    path("<int:pk>/reject/", views.report_reject, name="reject"),
    path("<int:pk>/correct/", views.report_correct, name="correct"),
]

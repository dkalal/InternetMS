from django.urls import path

from . import views

app_name = "work_reports"

urlpatterns = [
    path("", views.report_list, name="list"),
    path("new/", views.report_create, name="create"),
    path("approvals/", views.approval_queue, name="approval_queue"),
    path("payments/", views.payment_workspace, name="payment_workspace"),
    path("payments/batches/record/", views.payment_batch_record, name="payment_batch_record"),
    path("payments/batches/<int:pk>/", views.payment_batch_detail, name="payment_batch_detail"),
    path("payments/batches/<int:pk>/confirm/", views.payment_batch_confirm, name="payment_batch_confirm"),
    path("payments/batches/<int:pk>/dispute/", views.payment_batch_dispute, name="payment_batch_dispute"),
    path("payments/batches/<int:pk>/void/", views.payment_batch_void, name="payment_batch_void"),
    path("payments/batches/<int:pk>/replace/", views.payment_batch_replace, name="payment_batch_replace"),
    path("<int:pk>/", views.report_detail, name="detail"),
    path("<int:pk>/edit/", views.report_edit, name="edit"),
    path("<int:pk>/submit/", views.report_submit, name="submit"),
    path("<int:pk>/approve/", views.report_approve, name="approve"),
    path("<int:pk>/reject/", views.report_reject, name="reject"),
    path("<int:pk>/correct/", views.report_correct, name="correct"),
    path("<int:report_pk>/payment/record/", views.payment_record, name="payment_record"),
    path("payments/<int:pk>/confirm/", views.payment_confirm, name="payment_confirm"),
    path("payments/<int:pk>/dispute/", views.payment_dispute, name="payment_dispute"),
    path("payments/<int:pk>/void/", views.payment_void, name="payment_void"),
    path("payments/<int:pk>/replace/", views.payment_replace, name="payment_replace"),
]

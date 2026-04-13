from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("promotions/", views.PromotionListView.as_view(), name="promotion_list"),
    path("promotions/create/", views.PromotionCreateView.as_view(), name="promotion_create"),
    path("promotions/<int:pk>/edit/", views.PromotionUpdateView.as_view(), name="promotion_update"),
    path("subscription/<int:subscription_id>/renew/", views.renew_subscription, name="renew_subscription"),
    path("<str:doc_type>/", views.document_list, name="document_list"),
    path("<str:doc_type>/create/", views.document_create, name="document_create"),
    path("<str:doc_type>/<int:pk>/", views.document_detail, name="document_detail"),
    path("<str:doc_type>/<int:pk>/edit/", views.document_edit, name="document_edit"),
    path("<str:doc_type>/<int:pk>/pdf/", views.document_pdf, name="document_pdf"),
    path("quotation/<int:pk>/create-invoice/", views.create_invoice_from_quotation, name="create_invoice_from_quotation"),
    path("invoice/<int:pk>/cancel/", views.cancel_invoice, name="cancel_invoice"),
    path("invoice/<int:pk>/reissue/", views.reissue_invoice, name="reissue_invoice"),
    path("invoice/<int:pk>/credit-note/", views.create_credit_note, name="create_credit_note"),
    path("invoice/<int:pk>/create-receipt/", views.create_receipt_from_invoice, name="create_receipt_from_invoice"),
]

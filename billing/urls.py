from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("promotions/", views.PromotionListView.as_view(), name="promotion_list"),
    path("promotions/create/", views.PromotionCreateView.as_view(), name="promotion_create"),
    path("promotions/<int:pk>/edit/", views.PromotionUpdateView.as_view(), name="promotion_update"),
    path("subscription/<int:subscription_id>/renew/", views.renew_subscription, name="renew_subscription"),
    path("subscription/<int:subscription_id>/cancel/", views.cancel_subscription, name="cancel_subscription"),
    path("subscription-period/<int:period_id>/resolve-issue/", views.resolve_subscription_invoice_issue, name="resolve_subscription_invoice_issue"),
    # Billing Sheets
    path("sheets/", views.billing_sheet_list, name="billing_sheet_list"),
    path("sheets/create/", views.billing_sheet_create, name="billing_sheet_create"),
    path("sheets/<int:pk>/", views.billing_sheet_detail, name="billing_sheet_detail"),
    path("sheets/<int:pk>/edit/", views.billing_sheet_edit, name="billing_sheet_edit"),
    path("sheets/<int:pk>/generate-invoice/", views.billing_sheet_generate_invoice, name="billing_sheet_generate_invoice"),
    path("sheets/<int:sheet_pk>/items/add/", views.billing_item_add, name="billing_item_add"),
    path("sheets/<int:sheet_pk>/items/<int:item_pk>/edit/", views.billing_item_edit, name="billing_item_edit"),
    path("sheets/<int:sheet_pk>/items/<int:item_pk>/delete/", views.billing_item_delete, name="billing_item_delete"),
    # Generic document routes (must remain last)
    path("<str:doc_type>/", views.document_list, name="document_list"),
    path("<str:doc_type>/create/", views.document_create, name="document_create"),
    path("<str:doc_type>/<int:pk>/", views.document_detail, name="document_detail"),
    path("<str:doc_type>/<int:pk>/edit/", views.document_edit, name="document_edit"),
    path("receipt/<int:pk>/print/", views.receipt_print, name="receipt_print"),
    path("<str:doc_type>/<int:pk>/pdf/", views.document_pdf, name="document_pdf"),
    path("quotation/<int:pk>/create-invoice/", views.create_invoice_from_quotation, name="create_invoice_from_quotation"),
    path("quotation/<int:pk>/send/", views.send_quotation, name="send_quotation"),
    path("quotation/<int:pk>/accept/", views.accept_quotation, name="accept_quotation"),
    path("quotation/<int:pk>/reject/", views.reject_quotation, name="reject_quotation"),
    path("quotation/<int:pk>/expire/", views.expire_quotation, name="expire_quotation"),
    path("invoice/<int:pk>/void/", views.void_invoice, name="void_invoice"),
    path("invoice/<int:pk>/reissue/", views.reissue_invoice, name="reissue_invoice"),
    path("invoice/<int:pk>/credit-note/", views.create_credit_note, name="create_credit_note"),
    path("invoice/<int:pk>/create-receipt/", views.create_receipt_from_invoice, name="create_receipt_from_invoice"),
    path("credit-note/<int:pk>/void/", views.void_credit_note, name="void_credit_note"),
]

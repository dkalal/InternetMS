from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied
from django.db.models import Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.views.generic import CreateView, ListView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin

from users.permissions import PermissionCode, permissions_for_membership, require_permission, sales_document_queryset_for
from users.tenancy import require_organization
from internetservices.listing import apply_sort, clean_page_size, page_context, paginate_queryset, positive_decimal

from .forms import (
    BillingDocumentForm,
    BillingItemForm,
    BillingLineItemFormSet,
    BillingSheetForm,
    BillingSheetGenerateForm,
    CancelSubscriptionForm,
    CreditNoteCreateForm,
    DraftInvoiceEditForm,
    InvoiceActionForm,
    PromotionForm,
    QuotationActionForm,
    ReceiptCreateForm,
    SubscriptionInvoiceIssueForm,
    SubscriptionRenewalForm,
)
from .models import BillingDocument, BillingItem, BillingLineItem, BillingSheet, CustomerSubscription, Promotion, SubscriptionPeriod
from .pdf import build_image_data_uri, render_pdf_or_html
from .services import (
    BillingService,
    BillingServiceError,
    BillingSheetService,
    LineItemInput,
    QuotationLifecycleService,
    SubscriptionBillingService,
    first_day_of_month,
)


DOC_TYPE_DISPLAY = dict(BillingDocument.DocumentType.choices)


class PromotionListView(LoginRequiredMixin, ListView):
    model = Promotion
    template_name = "billing/promotion_list.html"
    context_object_name = "promotions"
    paginate_by = 25
    sort_options = {
        "name": ("name", "id"),
        "-name": ("-name", "-id"),
        "status": ("-is_active", "name"),
        "-status": ("is_active", "name"),
        "applies_to": ("applies_to", "name"),
        "-applies_to": ("-applies_to", "name"),
        "valid_until": ("valid_until", "name"),
        "-valid_until": ("-valid_until", "name"),
    }

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        queryset = Promotion.objects.filter(organization=organization).select_related("product", "package")
        q = self.request.GET.get("search")
        if q:
            queryset = queryset.filter(
                Q(name__icontains=q)
                | Q(product__name__icontains=q)
                | Q(package__name__icontains=q)
            )
        is_active = self.request.GET.get("is_active")
        if is_active in {"1", "0"}:
            queryset = queryset.filter(is_active=is_active == "1")
        applies_to = self.request.GET.get("applies_to")
        if applies_to:
            queryset = queryset.filter(applies_to=applies_to)
        reward_type = self.request.GET.get("reward_type")
        if reward_type:
            queryset = queryset.filter(reward_type=reward_type)
        today = timezone.localdate()
        validity = self.request.GET.get("validity")
        if validity == "scheduled":
            queryset = queryset.filter(valid_from__gt=today)
        elif validity == "expired":
            queryset = queryset.filter(valid_until__lt=today)
        elif validity == "current":
            queryset = queryset.filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today)).filter(
                Q(valid_until__isnull=True) | Q(valid_until__gte=today)
            )
        queryset, self.active_sort = apply_sort(queryset, self.request.GET.get("sort"), self.sort_options, "status")
        return queryset

    def get_paginate_by(self, queryset):
        return clean_page_size(self.request.GET.get("page_size"), default=self.paginate_by)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        for promotion in context["promotions"]:
            if not promotion.is_active:
                promotion.list_status = "inactive"
            elif promotion.valid_from and promotion.valid_from > today:
                promotion.list_status = "scheduled"
            elif promotion.valid_until and promotion.valid_until < today:
                promotion.list_status = "expired"
            else:
                promotion.list_status = "active"
        context["active_sort"] = getattr(self, "active_sort", self.request.GET.get("sort", "status"))
        context["applies_to_choices"] = Promotion.AppliesTo.choices
        context["reward_type_choices"] = Promotion.RewardType.choices
        context.update(page_context(self.request, context["page_obj"], page_size=self.get_paginate_by(self.object_list)))
        return context


class PromotionCreateView(LoginRequiredMixin, CreateView):
    model = Promotion
    form_class = PromotionForm
    template_name = "billing/promotion_form.html"
    success_url = reverse_lazy("billing:promotion_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = require_organization(self.request)
        return kwargs

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        form.instance.organization = organization
        form.instance.tenant = organization
        messages.success(self.request, "Promotion saved.")
        return super().form_valid(form)


class PromotionUpdateView(LoginRequiredMixin, UpdateView):
    model = Promotion
    form_class = PromotionForm
    template_name = "billing/promotion_form.html"
    success_url = reverse_lazy("billing:promotion_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["organization"] = require_organization(self.request)
        return kwargs

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        return Promotion.objects.filter(organization=organization)

    def form_valid(self, form):
        require_permission(self.request, PermissionCode.BILLING_SETTINGS_CHANGE)
        messages.success(self.request, "Promotion updated.")
        return super().form_valid(form)


def _require_valid_doc_type(doc_type: str) -> str:
    if doc_type not in DOC_TYPE_DISPLAY:
        raise Http404("Invalid document type.")
    return DOC_TYPE_DISPLAY[doc_type]


def _require_billing_read(request):
    allowed = permissions_for_membership(getattr(request, "membership", None))
    if not ({PermissionCode.SALES_DOCUMENTS_VIEW_OWN, PermissionCode.FINANCE_SALES_VIEW_ALL} & allowed):
        raise PermissionDenied("Billing access is not permitted.")


def _require_billing_write(request):
    require_permission(request, PermissionCode.BILLING_CREATE)


def _require_finance_all(request):
    require_permission(request, PermissionCode.FINANCE_SALES_VIEW_ALL)


def _build_document_form_context(*, form, formset, doc_type: str, doc_type_display: str, **extra):
    primary_order = ("customer", "sale_pricing_category", "issue_date", "due_date", "status", "currency", "tax_rate")
    primary_fields = [form[name] for name in primary_order if name in form.fields]
    secondary_fields = [form[name] for name in form.fields if name not in set(primary_order) | {"notes"}]
    empty_item_form = formset.empty_form
    customer_catalog = form.fields["customer"].queryset.order_by("name") if "customer" in form.fields else []
    promotion_catalog = empty_item_form.fields["promotion"].queryset.order_by("name")

    context = {
        "form": form,
        "formset": formset,
        "doc_type": doc_type,
        "doc_type_display": doc_type_display,
        "primary_fields": primary_fields,
        "secondary_fields": secondary_fields,
        "notes_field": form["notes"] if "notes" in form.fields else None,
        "product_catalog": empty_item_form.fields["product"].queryset.select_related(
            "sales_unit", "catalog_category", "catalog_category__default_unit"
        ).prefetch_related("catalog_category__allowed_units").order_by("name"),
        "package_catalog": empty_item_form.fields["package"].queryset.order_by("name"),
        "customer_catalog": customer_catalog,
        "promotion_catalog": promotion_catalog,
    }
    context.update(extra)
    return context


def _extract_items(formset) -> list[LineItemInput]:
    items: list[LineItemInput] = []
    for form in formset:
        if not form.cleaned_data or form.cleaned_data.get("DELETE"):
            continue
        product = form.cleaned_data.get("product")
        package = form.cleaned_data.get("package")
        items.append(
            LineItemInput(
                product_id=product.id if product else None,
                package_id=package.id if package else None,
                description=form.cleaned_data.get("description") or "",
                unit_snapshot=form.cleaned_data.get("unit_snapshot") or "",
                quantity=form.cleaned_data.get("quantity") or Decimal("0.00"),
                unit_price=form.cleaned_data.get("unit_price") or Decimal("0.00"),
                discount_amount=form.cleaned_data.get("discount_amount") or Decimal("0.00"),
                discount_reason=form.cleaned_data.get("discount_reason") or "",
                pricing_mode=form.cleaned_data.get("pricing_mode") or BillingLineItem.PricingMode.RETAIL,
                billing_behavior=form.cleaned_data.get("billing_behavior") or BillingLineItem.BillingBehavior.ONE_TIME,
                promotion_id=form.cleaned_data["promotion"].id if form.cleaned_data.get("promotion") else None,
            )
        )
    return items


def _attach_invoice_list_state(organization, document: BillingDocument) -> BillingDocument:
    state = BillingService.get_invoice_action_state(organization=organization, invoice=document)
    document.can_register_payment = state["can_register_payment"]
    document.remaining_balance = state["remaining_balance"]
    return document


def _attach_quotation_list_state(organization, document: BillingDocument) -> BillingDocument:
    state = QuotationLifecycleService.get_action_state(organization=organization, quotation=document)
    document.list_can_edit = state["can_edit"]
    document.list_can_convert = state["can_convert"]
    document.list_can_send = state["can_send"]
    document.list_can_accept = state["can_accept"]
    document.list_can_reject = state["can_reject"]
    document.list_can_expire = state["can_expire"]
    return document


def _build_invoice_action_form(*, request, organization, pk: int, form_class, action_title: str, submit_label: str, action_url_name: str, success_message: str, service_call, form_kwargs: dict | None = None, initial: dict | None = None, extra_context: dict | None = None):
    invoice = get_object_or_404(
        sales_document_queryset_for(request.user, organization, membership=request.membership).select_related("customer"),
        document_type=BillingDocument.DocumentType.INVOICE,
        pk=pk,
    )
    state = BillingService.get_invoice_action_state(organization=organization, invoice=invoice)

    if request.method == "POST":
        form = form_class(request.POST, **(form_kwargs or {}))
        if form.is_valid():
            try:
                result = service_call(invoice=invoice, form=form)
            except BillingServiceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, success_message)
                return redirect("billing:document_detail", doc_type=result.document_type, pk=result.pk)
    else:
        form = form_class(initial=initial or {}, **(form_kwargs or {}))

    context = {
        "form": form,
        "invoice": invoice,
        "action_title": action_title,
        "submit_label": submit_label,
        "action_url": reverse_lazy(action_url_name, kwargs={"pk": invoice.pk}),
        "state": state,
    }
    if extra_context:
        context.update(extra_context)
    return render(request, "billing/invoice_action_form.html", context)


def _build_quotation_action_form(*, request, organization, pk: int, action_title: str, submit_label: str, action_url_name: str, success_message: str, service_call, form_kwargs: dict | None = None):
    quotation = get_object_or_404(
        sales_document_queryset_for(request.user, organization, membership=request.membership).select_related("customer", "converted_invoice"),
        document_type=BillingDocument.DocumentType.QUOTATION,
        pk=pk,
    )
    state = QuotationLifecycleService.get_action_state(organization=organization, quotation=quotation)

    if request.method == "POST":
        form = QuotationActionForm(request.POST, **(form_kwargs or {}))
        if form.is_valid():
            try:
                result = service_call(quotation=quotation, form=form)
            except BillingServiceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, success_message)
                return redirect("billing:document_detail", doc_type=result.document_type, pk=result.pk)
    else:
        form = QuotationActionForm(**(form_kwargs or {}))

    return render(
        request,
        "billing/quotation_action_form.html",
        {
            "form": form,
            "quotation": quotation,
            "state": state,
            "action_title": action_title,
            "submit_label": submit_label,
            "action_url": reverse_lazy(action_url_name, kwargs={"pk": quotation.pk}),
        },
    )


@login_required
def document_list(request, doc_type: str):
    organization = require_organization(request)
    _require_billing_read(request)
    doc_type_display = _require_valid_doc_type(doc_type)

    documents = sales_document_queryset_for(
        request.user, organization, membership=request.membership
    ).filter(document_type=doc_type).select_related("customer", "invoice")
    if doc_type == BillingDocument.DocumentType.QUOTATION and request.GET.get("include_history") != "1":
        documents = documents.filter(is_current_version=True)

    q = request.GET.get("search")
    if q:
        documents = documents.filter(
            Q(number__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(invoice__number__icontains=q)
            | Q(payment_reference__icontains=q)
        )
    status = request.GET.get("status")
    if status:
        documents = documents.filter(status=status)
    customer_id = request.GET.get("customer")
    if customer_id:
        documents = documents.filter(customer_id=customer_id)
    start_date = parse_date(request.GET.get("date_from") or "")
    end_date = parse_date(request.GET.get("date_to") or "")
    if start_date:
        date_field = "payment_date" if doc_type == BillingDocument.DocumentType.RECEIPT else "issue_date"
        documents = documents.filter(**{f"{date_field}__gte": start_date})
    if end_date:
        date_field = "payment_date" if doc_type == BillingDocument.DocumentType.RECEIPT else "issue_date"
        documents = documents.filter(**{f"{date_field}__lte": end_date})
    due_from = parse_date(request.GET.get("due_from") or "")
    due_to = parse_date(request.GET.get("due_to") or "")
    if due_from:
        documents = documents.filter(due_date__gte=due_from)
    if due_to:
        documents = documents.filter(due_date__lte=due_to)
    min_total = positive_decimal(request.GET.get("min_total"))
    max_total = positive_decimal(request.GET.get("max_total"))
    if min_total is not None:
        documents = documents.filter(total__gte=min_total)
    if max_total is not None:
        documents = documents.filter(total__lte=max_total)
    payment_method = request.GET.get("payment_method")
    if payment_method:
        documents = documents.filter(payment_method__icontains=payment_method)

    today = timezone.localdate()
    worklist = request.GET.get("worklist")
    if doc_type == BillingDocument.DocumentType.INVOICE:
        if worklist == "unpaid":
            documents = documents.exclude(status__in=[BillingDocument.Status.PAID, BillingDocument.Status.VOID, BillingDocument.Status.SUPERSEDED, BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED])
        elif worklist == "overdue":
            documents = documents.filter(due_date__lt=today).exclude(status__in=[BillingDocument.Status.PAID, BillingDocument.Status.VOID, BillingDocument.Status.SUPERSEDED, BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED])
        elif worklist == "draft":
            documents = documents.filter(status=BillingDocument.Status.DRAFT)
        elif worklist == "paid_month":
            documents = documents.filter(status=BillingDocument.Status.PAID, issue_date__year=today.year, issue_date__month=today.month)

    sort_options = {
        "date": ("-issue_date", "-created_at"),
        "-date": ("issue_date", "created_at"),
        "number": ("number", "id"),
        "-number": ("-number", "-id"),
        "customer": ("customer__name", "-issue_date"),
        "-customer": ("-customer__name", "-issue_date"),
        "status": ("status", "-issue_date"),
        "-status": ("-status", "-issue_date"),
        "total": ("total", "-issue_date"),
        "-total": ("-total", "-issue_date"),
        "due": ("due_date", "-issue_date"),
        "-due": ("-due_date", "-issue_date"),
        "payment_date": ("-payment_date", "-created_at"),
        "-payment_date": ("payment_date", "created_at"),
    }
    default_sort = "payment_date" if doc_type == BillingDocument.DocumentType.RECEIPT else "date"
    documents, active_sort = apply_sort(documents, request.GET.get("sort"), sort_options, default_sort)
    pagination = paginate_queryset(request, documents)
    page_obj = pagination["page_obj"]

    invoice_base = sales_document_queryset_for(
        request.user, organization, membership=request.membership
    ).filter(document_type=BillingDocument.DocumentType.INVOICE)
    invoice_worklists = {}
    if doc_type == BillingDocument.DocumentType.INVOICE:
        invoice_worklists = {
            "unpaid": invoice_base.exclude(status__in=[BillingDocument.Status.PAID, BillingDocument.Status.VOID, BillingDocument.Status.SUPERSEDED, BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED]).count(),
            "overdue": invoice_base.filter(due_date__lt=today).exclude(status__in=[BillingDocument.Status.PAID, BillingDocument.Status.VOID, BillingDocument.Status.SUPERSEDED, BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED]).count(),
            "draft": invoice_base.filter(status=BillingDocument.Status.DRAFT).count(),
            "paid_month": invoice_base.filter(status=BillingDocument.Status.PAID, issue_date__year=today.year, issue_date__month=today.month).count(),
        }

    return render(
        request,
        "billing/document_list.html",
        {
            "documents": [
                _attach_invoice_list_state(organization, document)
                if doc_type == BillingDocument.DocumentType.INVOICE
                else _attach_quotation_list_state(organization, document)
                if doc_type == BillingDocument.DocumentType.QUOTATION
                else document
                for document in page_obj.object_list
            ],
            "doc_type": doc_type,
            "doc_type_display": doc_type_display,
            "active_sort": active_sort,
            "status_choices": (
                BillingDocument.invoice_status_choices()
                if doc_type == BillingDocument.DocumentType.INVOICE
                else BillingDocument.quotation_status_choices()
                if doc_type == BillingDocument.DocumentType.QUOTATION
                else BillingDocument.Status.choices
            ),
            "customer_catalog": organization.customers.order_by("name"),
            "invoice_worklists": invoice_worklists,
            **pagination,
        },
    )


@login_required
def document_detail(request, doc_type: str, pk: int):
    organization = require_organization(request)
    _require_billing_read(request)
    _require_valid_doc_type(doc_type)

    document = get_object_or_404(
        sales_document_queryset_for(request.user, organization, membership=request.membership).select_related("customer", "created_by", "source_quotation", "converted_invoice", "invoice", "superseded_by"),
        document_type=doc_type,
        pk=pk,
    )
    items = BillingLineItem.objects.filter(organization=organization, document=document).select_related(
        "product", "package"
    ).prefetch_related("serial_selections__stock_unit")
    has_receipt = False
    quotation_history = []
    quotation_comparison = None
    linked_subscription_period = None
    payment_summary = None
    payment_receipts = []
    can_register_payment = False
    invoice_action_state = None
    can_void_invoice = False
    can_reissue_invoice = False
    can_create_credit_note = False
    quotation_action_state = None
    invoice_supersession_details = None
    invoice_carry_forward = None
    invoice_account_summary = None
    invoice_payment_policy = None

    if document.document_type == BillingDocument.DocumentType.INVOICE:
        invoice_action_state = BillingService.get_invoice_action_state(organization=organization, invoice=document)
        invoice_payment_policy = BillingService.invoice_payment_policy(
            organization=organization, invoice=document
        )
        invoice_supersession_details = BillingService.get_invoice_supersession_details(organization=organization, invoice=document)
        payment_receipts = list(
            BillingDocument.objects.filter(
                organization=organization,
                document_type=BillingDocument.DocumentType.RECEIPT,
                invoice_id=document.id,
            )
            .select_related("created_by")
            .order_by("created_at", "id")
        )
        has_receipt = invoice_action_state["paid_total"] > Decimal("0.00")
        can_register_payment = invoice_action_state["can_register_payment"]
        can_void_invoice = invoice_action_state["can_void"]
        can_reissue_invoice = invoice_action_state["can_reissue"]
        can_create_credit_note = invoice_action_state["can_create_credit_note"]
        payment_summary = {
            "invoice_number": document.number,
            "invoice_total": document.total,
            "amount_paid": invoice_action_state["paid_total"],
            "credited_total": invoice_action_state["credited_total"],
            "outstanding_balance": invoice_action_state["remaining_balance"],
            "credit_capacity": invoice_action_state["credit_capacity"],
            "receipt_count": len(payment_receipts),
            "latest_receipt": payment_receipts[-1] if payment_receipts else None,
        }
        invoice_carry_forward = {
            "brought_forward": document.balance_brought_forward,
            "current_balance": invoice_action_state["remaining_balance"],
            "account_balance": BillingService.customer_open_invoice_balance(
                organization=organization,
                customer=document.customer,
            ),
        }
        invoice_account_summary = BillingService.invoice_account_summary(
            organization=organization, invoice=document
        )
        linked_subscription_period = (
            SubscriptionPeriod.objects.filter(organization=organization, invoice=document)
            .select_related("subscription", "subscription__customer", "subscription__package", "receipt")
            .first()
        )
    elif document.document_type == BillingDocument.DocumentType.RECEIPT and document.invoice_id:
        payment_receipts = list(
            BillingDocument.objects.filter(
                organization=organization,
                document_type=BillingDocument.DocumentType.RECEIPT,
                invoice_id=document.invoice_id,
            )
            .select_related("created_by")
            .order_by("created_at", "id")
        )
        source_invoice = document.invoice
        invoice_state = BillingService.get_invoice_action_state(organization=organization, invoice=source_invoice)
        payment_summary = {
            "invoice_number": source_invoice.number if source_invoice else None,
            "invoice_total": source_invoice.total if source_invoice else Decimal("0.00"),
            "amount_paid": document.total,
            "paid_total": invoice_state["paid_total"],
            "credited_total": invoice_state["credited_total"],
            "outstanding_balance": invoice_state["remaining_balance"],
            "receipt_count": len(payment_receipts),
            "latest_receipt": payment_receipts[-1] if payment_receipts else None,
        }
    if document.document_type == BillingDocument.DocumentType.QUOTATION:
        quotation_action_state = QuotationLifecycleService.get_action_state(organization=organization, quotation=document)
        quotation_history = list(
            BillingService.get_quotation_history(organization=organization, quotation_id=document.id)
            .select_related("created_by")
            .order_by("version_number", "created_at")
        )
        compare_to = request.GET.get("compare_to")
        if compare_to:
            try:
                quotation_comparison = BillingService.compare_quotation_versions(
                    organization=organization,
                    from_quotation_id=int(compare_to),
                    to_quotation_id=document.id,
                )
            except (ValueError, BillingServiceError):
                quotation_comparison = None
    return render(
        request,
        "billing/document_detail.html",
        {
            "document": document,
            "items": items,
            "doc_type": doc_type,
            "has_receipt": has_receipt,
            "payment_summary": payment_summary,
            "payment_receipts": payment_receipts,
            "can_register_payment": can_register_payment,
            "quotation_history": quotation_history,
            "quotation_comparison": quotation_comparison,
            "quotation_action_state": quotation_action_state,
            "invoice_action_state": invoice_action_state,
            "invoice_locked": invoice_action_state["is_locked"] if invoice_action_state else document.document_type == BillingDocument.DocumentType.INVOICE and document.status != BillingDocument.Status.DRAFT,
            "linked_subscription_period": linked_subscription_period,
            "can_void_invoice": can_void_invoice,
            "can_reissue_invoice": can_reissue_invoice,
            "can_create_credit_note": can_create_credit_note,
            "has_serialized_inventory_items": document.document_type == BillingDocument.DocumentType.INVOICE
            and document.items.filter(product__is_serialized=True).exists(),
            "has_inventory_items": document.items.filter(product__isnull=False).exists(),
            "invoice_supersession_details": invoice_supersession_details,
            "invoice_carry_forward": invoice_carry_forward,
            "invoice_account_summary": invoice_account_summary,
            "invoice_payment_policy": invoice_payment_policy,
            "can_resolve_subscription_issue": linked_subscription_period is not None
            and linked_subscription_period.status in {SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE}
            and (can_void_invoice or can_reissue_invoice),
        },
    )


@login_required
def document_create(request, doc_type: str):
    organization = require_organization(request)
    doc_type_display = _require_valid_doc_type(doc_type)

    if doc_type == BillingDocument.DocumentType.RECEIPT:
        require_permission(request, PermissionCode.PAYMENT_REGISTER)
        invoice_id = request.GET.get("invoice")
        if invoice_id:
            return redirect("billing:create_receipt_from_invoice", pk=invoice_id)
        messages.info(request, "Receipts are created from invoices. Select an invoice to register a payment.")
        return redirect("billing:document_list", doc_type=BillingDocument.DocumentType.INVOICE)
    if doc_type == BillingDocument.DocumentType.CREDIT_NOTE:
        _require_billing_write(request)
        messages.info(request, "Credit notes are created from issued invoices.")
        return redirect("billing:document_list", doc_type=BillingDocument.DocumentType.INVOICE)

    _require_billing_write(request)

    if request.method == "POST":
        form = BillingDocumentForm(request.POST, organization=organization, doc_type=doc_type)
        formset = BillingLineItemFormSet(request.POST, prefix="items", form_kwargs={"organization": organization})
        if form.is_valid() and formset.is_valid():
            try:
                document = BillingService.create_document(
                    organization=organization,
                    created_by=request.user,
                    document_type=doc_type,
                    customer_id=form.cleaned_data["customer"].id,
                    issue_date=form.cleaned_data["issue_date"],
                    due_date=form.cleaned_data.get("due_date"),
                    status=form.cleaned_data["status"],
                    currency=form.cleaned_data["currency"],
                    tax_rate=form.cleaned_data["tax_rate"],
                    notes=form.cleaned_data.get("notes") or "",
                    items=_extract_items(formset),
                    sale_pricing_category=form.cleaned_data["sale_pricing_category"],
                )
            except BillingServiceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"{document.get_document_type_display()} created.")
                return redirect("billing:document_detail", doc_type=doc_type, pk=document.pk)
    else:
        initial = {
            "issue_date": timezone.now().date(), "tax_rate": Decimal("18.00"), "currency": "TZS",
            "sale_pricing_category": BillingDocument.SalePricingCategory.STANDARD,
        }
        customer_id = request.GET.get("customer")
        if customer_id:
            try:
                initial["customer"] = int(customer_id)
                customer = organization.customers.filter(pk=initial["customer"], status="active").first()
                if customer is not None:
                    initial["tax_rate"] = BillingService.default_tax_rate_for_customer(customer)
            except (TypeError, ValueError):
                pass
        form = BillingDocumentForm(
            initial=initial,
            organization=organization,
            doc_type=doc_type,
        )
        formset = BillingLineItemFormSet(prefix="items", form_kwargs={"organization": organization})

    return render(
        request,
        "billing/document_form.html",
        _build_document_form_context(
            form=form,
            formset=formset,
            doc_type=doc_type,
            doc_type_display=doc_type_display,
        ),
    )


@login_required
def document_edit(request, doc_type: str, pk: int):
    organization = require_organization(request)
    _require_billing_write(request)
    _require_valid_doc_type(doc_type)

    document = get_object_or_404(
        sales_document_queryset_for(request.user, organization, membership=request.membership).select_related("customer", "created_by"),
        document_type=doc_type,
        pk=pk,
    )
    initial_items = list(document.items.filter(organization=organization).all())

    if doc_type == BillingDocument.DocumentType.QUOTATION:
        form_class = BillingDocumentForm
        form_kwargs = {"organization": organization, "doc_type": doc_type}
        quotation_state = QuotationLifecycleService.get_action_state(organization=organization, quotation=document)
        if not quotation_state["can_edit"]:
            messages.error(request, "Only the current draft quotation can be revised from this screen.")
            return redirect("billing:document_detail", doc_type=doc_type, pk=document.pk)
    elif doc_type == BillingDocument.DocumentType.INVOICE:
        form_class = DraftInvoiceEditForm
        form_kwargs = {}
    else:
        raise Http404("Editing is not supported for this document type.")

    if request.method == "POST":
        form = form_class(request.POST, **form_kwargs)
        formset = BillingLineItemFormSet(request.POST, prefix="items", form_kwargs={"organization": organization}, instance=document)
        if form.is_valid() and formset.is_valid():
            try:
                items = _extract_items(formset)
                if doc_type == BillingDocument.DocumentType.QUOTATION:
                    edited = BillingService.create_quotation_version(
                        organization=organization,
                        created_by=request.user,
                        quotation_id=document.id,
                        customer_id=form.cleaned_data["customer"].id,
                        issue_date=form.cleaned_data["issue_date"],
                        due_date=form.cleaned_data.get("due_date"),
                        status=form.cleaned_data["status"],
                        currency=form.cleaned_data["currency"],
                        tax_rate=form.cleaned_data["tax_rate"],
                        notes=form.cleaned_data.get("notes") or "",
                        items=items,
                        sale_pricing_category=form.cleaned_data["sale_pricing_category"],
                    )
                    messages.success(request, "Quotation version created.")
                else:
                    edited = BillingService.update_draft_invoice(
                        organization=organization,
                        performed_by=request.user,
                        invoice_id=document.id,
                        tax_rate=form.cleaned_data["tax_rate"],
                        status=form.cleaned_data["status"],
                        items=items,
                    )
                    messages.success(request, "Draft invoice updated.")
                return redirect("billing:document_detail", doc_type=doc_type, pk=edited.pk)
            except BillingServiceError as exc:
                messages.error(request, str(exc))
    else:
        if doc_type == BillingDocument.DocumentType.QUOTATION:
            form = form_class(
                initial={
                    "customer": document.customer,
                    "sale_pricing_category": document.sale_pricing_category,
                    "issue_date": document.issue_date,
                    "due_date": document.due_date,
                    "status": document.status,
                    "currency": document.currency,
                    "tax_rate": document.tax_rate,
                    "notes": document.notes,
                },
                **form_kwargs,
            )
        else:
            form = form_class(initial={"tax_rate": document.tax_rate, "status": document.status}, **form_kwargs)
        formset = BillingLineItemFormSet(prefix="items", form_kwargs={"organization": organization}, instance=document)

    return render(
        request,
        "billing/document_form.html",
        _build_document_form_context(
            form=form,
            formset=formset,
            doc_type=doc_type,
            doc_type_display=f"Edit {document.get_document_type_display()}",
            document=document,
            initial_items=initial_items,
        ),
    )


@login_required
def document_pdf(request, doc_type: str, pk: int):
    organization = require_organization(request)
    _require_billing_read(request)
    _require_valid_doc_type(doc_type)

    document = get_object_or_404(
        sales_document_queryset_for(request.user, organization, membership=request.membership).select_related("customer", "organization"),
        document_type=doc_type,
        pk=pk,
    )
    items = list(document.items.filter(organization=organization).select_related("product", "package"))

    branding = None
    try:
        branding = document.organization.branding
    except ObjectDoesNotExist:
        branding = None

    logo_data_uri = None
    if branding and getattr(branding, "logo", None):
        try:
            logo_data_uri = build_image_data_uri(branding.logo.path)
        except Exception:
            logo_data_uri = None

    safe_number = slugify(document.number) or str(document.pk)
    filename = f"{doc_type}-{safe_number}.pdf"
    as_attachment = request.GET.get("download", "1") != "0"

    template_name = "billing/document_print.html"
    if doc_type == BillingDocument.DocumentType.RECEIPT:
        template_name = "billing/receipt_print_tra.html"
    invoice_carry_forward = None
    invoice_account_summary = None
    if doc_type == BillingDocument.DocumentType.INVOICE:
        state = BillingService.get_invoice_action_state(organization=organization, invoice=document)
        invoice_carry_forward = {
            "brought_forward": document.balance_brought_forward,
            "current_balance": state["remaining_balance"],
            "account_balance": BillingService.customer_open_invoice_balance(
                organization=organization,
                customer=document.customer,
            ),
        }
        invoice_account_summary = BillingService.invoice_account_summary(
            organization=organization, invoice=document
        )
    if doc_type in {BillingDocument.DocumentType.QUOTATION, BillingDocument.DocumentType.INVOICE}:
        template_name = "billing/sales_document_print.html"
    return render_pdf_or_html(
        request=request,
        template_name=template_name,
        context={
            "document": document,
            "items": items,
            "LOGO_DATA_URI": logo_data_uri,
            "invoice_carry_forward": invoice_carry_forward,
            "invoice_account_summary": invoice_account_summary,
            "show_discount_column": any(item.discount_amount for item in items),
            "show_tax_column": bool(document.tax_amount),
        },
        filename=filename,
        as_attachment=as_attachment,
    )


@login_required
def create_invoice_from_quotation(request, pk: int):
    organization = require_organization(request)
    _require_billing_write(request)

    if request.method != "POST":
        raise PermissionDenied("POST required.")
    get_object_or_404(
        sales_document_queryset_for(request.user, organization, membership=request.membership),
        pk=pk, document_type=BillingDocument.DocumentType.QUOTATION,
    )

    try:
        invoice = BillingService.create_invoice_from_quotation(
            organization=organization,
            created_by=request.user,
            quotation_id=pk,
        )
    except BillingServiceError as exc:
        messages.error(request, str(exc))
        return redirect("billing:document_detail", doc_type="quotation", pk=pk)

    messages.success(request, "Invoice created from quotation.")
    return redirect("billing:document_detail", doc_type="invoice", pk=invoice.pk)


@login_required
def send_quotation(request, pk: int):
    organization = require_organization(request)
    _require_billing_write(request)
    return _build_quotation_action_form(
        request=request,
        organization=organization,
        pk=pk,
        action_title="Send quotation",
        submit_label="Mark as sent",
        action_url_name="billing:send_quotation",
        success_message="Quotation marked as sent.",
        form_kwargs={"action_label": "send", "placeholder": "Optional note about how or when the quotation was sent."},
        service_call=lambda quotation, form: QuotationLifecycleService.send(
            organization=organization,
            performed_by=request.user,
            quotation_id=quotation.id,
            reason=form.cleaned_data["reason"],
        ),
    )


@login_required
def accept_quotation(request, pk: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.QUOTATIONS_APPROVE)
    return _build_quotation_action_form(
        request=request,
        organization=organization,
        pk=pk,
        action_title="Accept quotation",
        submit_label="Mark as accepted",
        action_url_name="billing:accept_quotation",
        success_message="Quotation marked as accepted.",
        form_kwargs={"action_label": "accept", "placeholder": "Optional note about the customer's approval."},
        service_call=lambda quotation, form: QuotationLifecycleService.accept(
            organization=organization,
            performed_by=request.user,
            quotation_id=quotation.id,
            reason=form.cleaned_data["reason"],
        ),
    )


@login_required
def reject_quotation(request, pk: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.QUOTATIONS_REJECT)
    return _build_quotation_action_form(
        request=request,
        organization=organization,
        pk=pk,
        action_title="Reject quotation",
        submit_label="Mark as rejected",
        action_url_name="billing:reject_quotation",
        success_message="Quotation marked as rejected.",
        form_kwargs={"action_label": "reject", "placeholder": "Optional note about why the quotation was declined."},
        service_call=lambda quotation, form: QuotationLifecycleService.reject(
            organization=organization,
            performed_by=request.user,
            quotation_id=quotation.id,
            reason=form.cleaned_data["reason"],
        ),
    )


@login_required
def expire_quotation(request, pk: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.QUOTATIONS_CANCEL)
    return _build_quotation_action_form(
        request=request,
        organization=organization,
        pk=pk,
        action_title="Expire quotation",
        submit_label="Mark as expired",
        action_url_name="billing:expire_quotation",
        success_message="Quotation marked as expired.",
        form_kwargs={"action_label": "expire", "placeholder": "Optional note about why this quotation was expired."},
        service_call=lambda quotation, form: QuotationLifecycleService.expire(
            organization=organization,
            performed_by=request.user,
            quotation_id=quotation.id,
            reason=form.cleaned_data["reason"],
        ),
    )


@login_required
def void_invoice(request, pk: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.FINANCE_REVERSALS_MANAGE)

    return _build_invoice_action_form(
        request=request,
        organization=organization,
        pk=pk,
        form_class=InvoiceActionForm,
        form_kwargs={"action_label": "be voided", "placeholder": "Example: This invoice was issued for the wrong customer."},
        action_title="Void invoice",
        submit_label="Void invoice",
        action_url_name="billing:void_invoice",
        success_message="Invoice voided.",
        service_call=lambda invoice, form: BillingService.void_invoice(
            organization=organization,
            performed_by=request.user,
            invoice_id=invoice.id,
            reason=form.cleaned_data["reason"],
        ),
    )


@login_required
def reissue_invoice(request, pk: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.FINANCE_REVERSALS_MANAGE)

    return _build_invoice_action_form(
        request=request,
        organization=organization,
        pk=pk,
        form_class=InvoiceActionForm,
        form_kwargs={"action_label": "be reissued", "placeholder": "Example: Wrong package price was used on the original invoice."},
        action_title="Reissue invoice",
        submit_label="Create replacement draft",
        action_url_name="billing:reissue_invoice",
        success_message="Replacement draft invoice created.",
        service_call=lambda invoice, form: BillingService.reissue_invoice(
            organization=organization,
            performed_by=request.user,
            invoice_id=invoice.id,
            reason=form.cleaned_data["reason"],
        ),
    )


@login_required
def create_credit_note(request, pk: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.FINANCE_REVERSALS_MANAGE)

    invoice = get_object_or_404(
        sales_document_queryset_for(request.user, organization, membership=request.membership).select_related("customer"),
        document_type=BillingDocument.DocumentType.INVOICE,
        pk=pk,
    )
    state = BillingService.get_invoice_action_state(organization=organization, invoice=invoice)
    initial = {"issue_date": timezone.now().date(), "amount": state["credit_capacity"]}

    if request.method == "POST":
        form = CreditNoteCreateForm(request.POST)
        if form.is_valid():
            try:
                credit_note = BillingService.create_credit_note(
                    organization=organization,
                    performed_by=request.user,
                    invoice_id=invoice.id,
                    amount=form.cleaned_data["amount"],
                    reason=form.cleaned_data["reason"],
                    issue_date=form.cleaned_data.get("issue_date") or timezone.now().date(),
                )
            except BillingServiceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Credit note created.")
                return redirect("billing:document_detail", doc_type="credit_note", pk=credit_note.pk)
    else:
        form = CreditNoteCreateForm(initial=initial)

    return render(
        request,
        "billing/invoice_action_form.html",
        {
            "form": form,
            "invoice": invoice,
            "action_title": "Create credit note",
            "submit_label": "Create credit note",
            "action_url": reverse_lazy("billing:create_credit_note", kwargs={"pk": invoice.pk}),
            "state": state,
        },
    )


@login_required
def resolve_subscription_invoice_issue(request, period_id: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.FINANCE_REVERSALS_MANAGE)

    period = get_object_or_404(
        SubscriptionPeriod.objects.select_related(
            "subscription",
            "subscription__customer",
            "subscription__package",
            "invoice",
            "receipt",
        ),
        organization=organization,
        pk=period_id,
    )
    if period.invoice_id is None:
        messages.error(request, "This subscription period does not have an invoice to resolve.")
        return redirect(period.subscription.customer.get_absolute_url())

    invoice_state = BillingService.get_invoice_action_state(organization=organization, invoice=period.invoice)
    can_resolve = (
        period.status in {SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE}
        and period.receipt_id is None
        and (invoice_state["can_void"] or invoice_state["can_reissue"])
    )
    if not can_resolve:
        messages.error(request, "Only unpaid subscription invoices can be resolved here.")
        return redirect("billing:document_detail", doc_type="invoice", pk=period.invoice_id)

    if request.method == "POST":
        form = SubscriptionInvoiceIssueForm(request.POST)
        if form.is_valid():
            action = form.cleaned_data["action"]
            reason = form.cleaned_data["reason"]
            try:
                if action == SubscriptionInvoiceIssueForm.Action.REISSUE:
                    invoice = BillingService.reissue_invoice(
                        organization=organization,
                        performed_by=request.user,
                        invoice_id=period.invoice_id,
                        reason=reason,
                    )
                    messages.success(request, "Replacement draft invoice created. Review it before sending.")
                    return redirect("billing:document_detail", doc_type="invoice", pk=invoice.id)
                BillingService.void_subscription_invoice(
                    organization=organization,
                    performed_by=request.user,
                    period_id=period.id,
                    reason=reason,
                )
            except BillingServiceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Subscription invoice voided and the billing period was marked cancelled.")
                return redirect(period.subscription.customer.get_absolute_url())
    else:
        form = SubscriptionInvoiceIssueForm()

    return render(
        request,
        "billing/subscription_invoice_issue.html",
        {
            "form": form,
            "period": period,
            "invoice": period.invoice,
            "customer": period.subscription.customer,
        },
    )


@login_required
def create_receipt_from_invoice(request, pk: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.PAYMENT_REGISTER)

    invoice = get_object_or_404(
        sales_document_queryset_for(request.user, organization, membership=request.membership),
        document_type=BillingDocument.DocumentType.INVOICE,
        pk=pk,
    )

    existing_receipt = BillingDocument.objects.filter(
        organization=organization,
        document_type=BillingDocument.DocumentType.RECEIPT,
        invoice=invoice,
    ).order_by("-created_at").first()
    invoice_state = BillingService.get_invoice_action_state(organization=organization, invoice=invoice)
    if not invoice_state["can_register_payment"] and request.method != "POST":
        messages.error(request, "This invoice cannot accept a payment in its current state.")
        return redirect("billing:document_detail", doc_type="invoice", pk=invoice.id)

    if request.method == "POST":
        form = ReceiptCreateForm(request.POST, organization=organization, invoice=invoice)
        if form.is_valid():
            try:
                receipt = BillingService.create_receipt_from_invoice(
                    organization=organization,
                    created_by=request.user,
                    invoice_id=invoice.id,
                    amount_paid=form.cleaned_data["amount_paid"],
                    payment_date=form.cleaned_data["payment_date"],
                    payment_method=form.cleaned_data["payment_method"],
                    payment_reference=form.cleaned_data.get("payment_reference") or "",
                    notes=form.cleaned_data.get("notes") or "",
                )
            except BillingServiceError as exc:
                messages.error(request, str(exc))
            else:
                if existing_receipt is not None and receipt.id == existing_receipt.id:
                    messages.info(request, "This invoice already has a receipt. Opened the existing receipt.")
                else:
                    invoice.refresh_from_db(fields=["status"])
                    if invoice.status == BillingDocument.Status.PARTIALLY_PAID:
                        messages.success(request, "Receipt recorded and invoice is partially paid.")
                    else:
                        messages.success(request, "Receipt recorded and invoice is fully paid.")
                return redirect("billing:document_detail", doc_type="receipt", pk=receipt.pk)
    else:
        # Suggest an initial amount: prefer explicit `amount` query param, else
        # default to the outstanding balance (invoice total minus any recorded receipts).
        try:
            amount_q = request.GET.get("amount")
            if amount_q:
                initial_amount = Decimal(amount_q)
            else:
                initial_amount = BillingService.invoice_remaining_balance(organization=organization, invoice=invoice)
        except Exception:
            initial_amount = BillingService.invoice_remaining_balance(organization=organization, invoice=invoice)

        form = ReceiptCreateForm(
            organization=organization,
            invoice=invoice,
            initial={"payment_date": timezone.now().date(), "amount_paid": initial_amount},
        )

    return render(
        request,
        "billing/receipt_from_invoice.html",
        {
            "invoice": invoice,
            "form": form,
            "has_inventory_items": getattr(form, "has_inventory_items", False),
            "requires_full_payment": getattr(form, "requires_full_payment", False),
            "outstanding_balance": BillingService.invoice_remaining_balance(organization=organization, invoice=invoice),
            "credited_total": BillingService.invoice_credited_total(organization=organization, invoice=invoice),
        },
    )


@login_required
def renew_subscription(request, subscription_id: int):
    organization = require_organization(request)
    _require_billing_write(request)
    subscription = get_object_or_404(
        CustomerSubscription.objects.select_related("customer", "package"),
        organization=organization,
        pk=subscription_id,
    )

    next_start = first_day_of_month(timezone.now().date())
    if subscription.paid_through_date:
        next_start = first_day_of_month(subscription.paid_through_date)
        next_start = next_start.replace(day=1)
        from .services import add_months

        next_start = add_months(next_start, 1)

    if request.method == "POST":
        form = SubscriptionRenewalForm(request.POST, organization=organization, customer=subscription.customer)
        if form.is_valid():
            try:
                period = SubscriptionBillingService.renew(
                    organization=organization,
                    created_by=request.user,
                    subscription_id=form.cleaned_data["subscription"].id,
                    period_start=form.cleaned_data["period_start"],
                    months=form.cleaned_data["months"],
                    promotion_id=form.cleaned_data["promotion"].id if form.cleaned_data.get("promotion") else None,
                    due_date=form.cleaned_data.get("due_date"),
                    issue_invoice=form.cleaned_data["issue_invoice"],
                )
            except BillingServiceError as exc:
                messages.error(request, str(exc))
            else:
                if period.invoice_id:
                    messages.success(request, "Subscription invoice created.")
                    return redirect("billing:document_detail", doc_type="invoice", pk=period.invoice_id)
                messages.success(request, "Subscription period created.")
                return redirect(subscription.customer.get_absolute_url())
    else:
        form = SubscriptionRenewalForm(
            organization=organization,
            customer=subscription.customer,
            initial={
                "subscription": subscription,
                "period_start": next_start,
                "months": 1,
                "due_date": timezone.now().date(),
                "issue_invoice": True,
            },
        )

    return render(
        request,
        "billing/subscription_renewal.html",
        {"form": form, "subscription": subscription},
    )


@login_required
def cancel_subscription(request, subscription_id: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.FINANCE_REVERSALS_MANAGE)

    subscription = get_object_or_404(
        CustomerSubscription.objects.select_related("customer", "package", "site"),
        organization=organization,
        pk=subscription_id,
    )

    if subscription.status == CustomerSubscription.Status.CANCELLED:
        messages.info(request, "This subscription is already cancelled.")
        return redirect(subscription.customer.get_absolute_url())

    if request.method == "POST":
        form = CancelSubscriptionForm(request.POST)
        if form.is_valid():
            try:
                SubscriptionBillingService.cancel_subscription(
                    organization=organization,
                    performed_by=request.user,
                    subscription_id=subscription.id,
                    reason=form.cleaned_data["reason"],
                )
            except BillingServiceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Subscription cancelled.")
                return redirect(subscription.customer.get_absolute_url())
    else:
        form = CancelSubscriptionForm()

    return render(
        request,
        "billing/subscription_cancel.html",
        {"form": form, "subscription": subscription, "customer": subscription.customer},
    )


# ── Billing Sheet views ──────────────────────────────────────────────────────

@login_required
def billing_sheet_list(request):
    organization = require_organization(request)
    _require_finance_all(request)

    sheets = BillingSheet.objects.filter(organization=organization).select_related("customer", "invoice")

    q = request.GET.get("search")
    if q:
        sheets = sheets.filter(
            Q(reference_number__icontains=q)
            | Q(title__icontains=q)
            | Q(customer__name__icontains=q)
        )
    status = request.GET.get("status")
    if status:
        sheets = sheets.filter(status=status)
    customer_id = request.GET.get("customer")
    if customer_id:
        sheets = sheets.filter(customer_id=customer_id)

    sort_options = {
        "date": ("-created_at",),
        "-date": ("created_at",),
        "customer": ("customer__name", "-created_at"),
        "-customer": ("-customer__name", "-created_at"),
        "status": ("status", "-created_at"),
        "-status": ("-status", "-created_at"),
        "reference": ("reference_number",),
        "-reference": ("-reference_number",),
    }
    sheets, active_sort = apply_sort(sheets, request.GET.get("sort"), sort_options, "date")
    pagination = paginate_queryset(request, sheets)

    return render(
        request,
        "billing/billing_sheet_list.html",
        {
            "sheets": pagination["page_obj"].object_list,
            "status_choices": BillingSheet.Status.choices,
            "customer_catalog": organization.customers.filter(status="active").order_by("name"),
            "active_sort": active_sort,
            **pagination,
        },
    )


@login_required
def billing_sheet_create(request):
    organization = require_organization(request)
    _require_finance_all(request)

    if request.method == "POST":
        form = BillingSheetForm(request.POST, organization=organization)
        if form.is_valid():
            try:
                sheet = BillingSheetService.create_sheet(
                    organization=organization,
                    created_by=request.user,
                    customer_id=form.cleaned_data["customer"].id,
                    title=form.cleaned_data["title"],
                    notes=form.cleaned_data.get("notes") or "",
                )
            except BillingServiceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Billing sheet created.")
                return redirect("billing:billing_sheet_detail", pk=sheet.pk)
    else:
        customer_id = request.GET.get("customer")
        initial = {}
        if customer_id:
            try:
                initial["customer"] = int(customer_id)
            except (TypeError, ValueError):
                pass
        form = BillingSheetForm(organization=organization, initial=initial)

    return render(request, "billing/billing_sheet_form.html", {"form": form, "sheet": None})


@login_required
def billing_sheet_detail(request, pk: int):
    organization = require_organization(request)
    _require_finance_all(request)

    sheet = get_object_or_404(
        BillingSheet.objects.select_related("customer", "invoice", "created_by"),
        organization=organization,
        pk=pk,
    )
    items = sheet.items.all()

    return render(
        request,
        "billing/billing_sheet_detail.html",
        {
            "sheet": sheet,
            "items": items,
        },
    )


@login_required
def billing_sheet_edit(request, pk: int):
    organization = require_organization(request)
    _require_finance_all(request)

    sheet = get_object_or_404(
        BillingSheet,
        organization=organization,
        pk=pk,
    )
    if not sheet.is_open:
        messages.error(request, "Only OPEN billing sheets can be edited.")
        return redirect("billing:billing_sheet_detail", pk=pk)

    if request.method == "POST":
        form = BillingSheetForm(request.POST, instance=sheet, organization=organization)
        if form.is_valid():
            form.save()
            messages.success(request, "Billing sheet updated.")
            return redirect("billing:billing_sheet_detail", pk=pk)
    else:
        form = BillingSheetForm(instance=sheet, organization=organization)

    return render(request, "billing/billing_sheet_form.html", {"form": form, "sheet": sheet})


@login_required
def billing_item_add(request, sheet_pk: int):
    organization = require_organization(request)
    _require_finance_all(request)

    sheet = get_object_or_404(BillingSheet, organization=organization, pk=sheet_pk)
    if not sheet.is_open:
        messages.error(request, "This billing sheet is locked and cannot be modified.")
        return redirect("billing:billing_sheet_detail", pk=sheet_pk)

    if request.method == "POST":
        form = BillingItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.billing_sheet = sheet
            item.save()
            BillingSheet.objects.filter(pk=sheet_pk).update(updated_at=timezone.now())
            messages.success(request, "Item added.")
            return redirect("billing:billing_sheet_detail", pk=sheet_pk)
    else:
        form = BillingItemForm()

    return render(request, "billing/billing_item_form.html", {"form": form, "sheet": sheet, "item": None})


@login_required
def billing_item_edit(request, sheet_pk: int, item_pk: int):
    organization = require_organization(request)
    _require_finance_all(request)

    sheet = get_object_or_404(BillingSheet, organization=organization, pk=sheet_pk)
    if not sheet.is_open:
        messages.error(request, "This billing sheet is locked and cannot be modified.")
        return redirect("billing:billing_sheet_detail", pk=sheet_pk)

    item = get_object_or_404(BillingItem, billing_sheet=sheet, pk=item_pk)

    if request.method == "POST":
        form = BillingItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            BillingSheet.objects.filter(pk=sheet_pk).update(updated_at=timezone.now())
            messages.success(request, "Item updated.")
            return redirect("billing:billing_sheet_detail", pk=sheet_pk)
    else:
        form = BillingItemForm(instance=item)

    return render(request, "billing/billing_item_form.html", {"form": form, "sheet": sheet, "item": item})


@login_required
def billing_item_delete(request, sheet_pk: int, item_pk: int):
    organization = require_organization(request)
    _require_finance_all(request)

    if request.method != "POST":
        raise PermissionDenied("POST required.")

    sheet = get_object_or_404(BillingSheet, organization=organization, pk=sheet_pk)
    if not sheet.is_open:
        messages.error(request, "This billing sheet is locked and cannot be modified.")
        return redirect("billing:billing_sheet_detail", pk=sheet_pk)

    item = get_object_or_404(BillingItem, billing_sheet=sheet, pk=item_pk)
    item.delete()
    BillingSheet.objects.filter(pk=sheet_pk).update(updated_at=timezone.now())
    messages.success(request, "Item removed.")
    return redirect("billing:billing_sheet_detail", pk=sheet_pk)


@login_required
def billing_sheet_generate_invoice(request, pk: int):
    organization = require_organization(request)
    _require_finance_all(request)

    sheet = get_object_or_404(
        BillingSheet.objects.select_related("customer"),
        organization=organization,
        pk=pk,
    )
    if not sheet.is_open:
        messages.error(request, "Only OPEN billing sheets can be converted to an invoice.")
        return redirect("billing:billing_sheet_detail", pk=pk)

    if request.method == "POST":
        form = BillingSheetGenerateForm(request.POST)
        if form.is_valid():
            try:
                invoice = BillingSheetService.generate_invoice(
                    organization=organization,
                    performed_by=request.user,
                    sheet_id=pk,
                    due_date=form.cleaned_data.get("due_date"),
                )
            except BillingServiceError as exc:
                messages.error(request, str(exc))
                return redirect("billing:billing_sheet_detail", pk=pk)
            messages.success(request, "Invoice generated successfully.")
            return redirect("billing:document_detail", doc_type="invoice", pk=invoice.pk)
    else:
        form = BillingSheetGenerateForm()

    return render(
        request,
        "billing/billing_sheet_generate.html",
        {"form": form, "sheet": sheet},
    )

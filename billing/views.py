from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from django.views.generic import CreateView, ListView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin

from users.permissions import PermissionCode, require_permission
from users.tenancy import require_organization
from internetservices.listing import apply_sort, clean_page_size, page_context, paginate_queryset, positive_decimal

from .forms import (
    BillingDocumentForm,
    BillingLineItemFormSet,
    DraftInvoiceEditForm,
    PromotionForm,
    ReceiptCreateForm,
    SubscriptionInvoiceIssueForm,
    SubscriptionRenewalForm,
)
from .models import BillingDocument, BillingLineItem, CustomerSubscription, Promotion, SubscriptionPeriod
from .pdf import build_image_data_uri, render_pdf_or_html
from .services import BillingService, BillingServiceError, LineItemInput, SubscriptionBillingService, first_day_of_month


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
    require_permission(request, PermissionCode.TENANT_READ)


def _require_billing_write(request):
    require_permission(request, PermissionCode.BILLING_CREATE)


def _build_document_form_context(*, form, formset, doc_type: str, doc_type_display: str, **extra):
    primary_order = ("customer", "issue_date", "due_date", "status", "currency", "tax_rate")
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
        "product_catalog": empty_item_form.fields["product"].queryset.order_by("name"),
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


@login_required
def document_list(request, doc_type: str):
    organization = require_organization(request)
    _require_billing_read(request)
    doc_type_display = _require_valid_doc_type(doc_type)

    documents = BillingDocument.objects.filter(organization=organization, document_type=doc_type).select_related("customer", "invoice")
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
            documents = documents.exclude(status__in=[BillingDocument.Status.PAID, BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED])
        elif worklist == "overdue":
            documents = documents.filter(due_date__lt=today).exclude(status__in=[BillingDocument.Status.PAID, BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED])
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

    invoice_base = BillingDocument.objects.filter(organization=organization, document_type=BillingDocument.DocumentType.INVOICE)
    invoice_worklists = {}
    if doc_type == BillingDocument.DocumentType.INVOICE:
        invoice_worklists = {
            "unpaid": invoice_base.exclude(status__in=[BillingDocument.Status.PAID, BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED]).count(),
            "overdue": invoice_base.filter(due_date__lt=today).exclude(status__in=[BillingDocument.Status.PAID, BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED]).count(),
            "draft": invoice_base.filter(status=BillingDocument.Status.DRAFT).count(),
            "paid_month": invoice_base.filter(status=BillingDocument.Status.PAID, issue_date__year=today.year, issue_date__month=today.month).count(),
        }

    return render(
        request,
        "billing/document_list.html",
        {
            "documents": page_obj.object_list,
            "doc_type": doc_type,
            "doc_type_display": doc_type_display,
            "active_sort": active_sort,
            "status_choices": BillingDocument.Status.choices,
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
        BillingDocument.objects.select_related("customer"),
        organization=organization,
        document_type=doc_type,
        pk=pk,
    )
    items = BillingLineItem.objects.filter(organization=organization, document=document).select_related("product", "package")
    has_receipt = False
    quotation_history = []
    quotation_comparison = None
    linked_subscription_period = None
    if document.document_type == BillingDocument.DocumentType.INVOICE:
        has_receipt = BillingDocument.objects.filter(
            organization=organization,
            document_type=BillingDocument.DocumentType.RECEIPT,
            invoice_id=document.id,
        ).exists()
        linked_subscription_period = (
            SubscriptionPeriod.objects.filter(organization=organization, invoice=document)
            .select_related("subscription", "subscription__customer", "subscription__package", "receipt")
            .first()
        )
    if document.document_type == BillingDocument.DocumentType.QUOTATION:
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
            "quotation_history": quotation_history,
            "quotation_comparison": quotation_comparison,
            "invoice_locked": document.document_type == BillingDocument.DocumentType.INVOICE
            and document.status != BillingDocument.Status.DRAFT,
            "linked_subscription_period": linked_subscription_period,
            "can_resolve_subscription_issue": linked_subscription_period is not None
            and linked_subscription_period.status
            in {SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE}
            and document.status not in {BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED}
            and not has_receipt,
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
                )
            except BillingServiceError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"{document.get_document_type_display()} created.")
                return redirect("billing:document_detail", doc_type=doc_type, pk=document.pk)
    else:
        initial = {"issue_date": timezone.now().date(), "tax_rate": Decimal("18.00"), "currency": "TZS"}
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
        BillingDocument.objects.select_related("customer"),
        organization=organization,
        document_type=doc_type,
        pk=pk,
    )
    initial_items = list(document.items.filter(organization=organization).all())

    if doc_type == BillingDocument.DocumentType.QUOTATION:
        form_class = BillingDocumentForm
        form_kwargs = {"organization": organization, "doc_type": doc_type}
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
                    )
                    messages.success(request, "Quotation version created.")
                else:
                    edited = BillingService.update_draft_invoice(
                        organization=organization,
                        performed_by=request.user,
                        invoice_id=document.id,
                        tax_rate=form.cleaned_data["tax_rate"],
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
            form = form_class(initial={"tax_rate": document.tax_rate}, **form_kwargs)
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
        BillingDocument.objects.select_related("customer", "organization"),
        organization=organization,
        document_type=doc_type,
        pk=pk,
    )
    items = document.items.filter(organization=organization).select_related("product", "package")

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
    return render_pdf_or_html(
        request=request,
        template_name=template_name,
        context={"document": document, "items": items, "LOGO_DATA_URI": logo_data_uri},
        filename=filename,
        as_attachment=as_attachment,
    )


@login_required
def create_invoice_from_quotation(request, pk: int):
    organization = require_organization(request)
    _require_billing_write(request)

    if request.method != "POST":
        raise PermissionDenied("POST required.")

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
def cancel_invoice(request, pk: int):
    organization = require_organization(request)
    _require_billing_write(request)
    if request.method != "POST":
        raise PermissionDenied("POST required.")
    try:
        invoice = BillingService.cancel_invoice(organization=organization, performed_by=request.user, invoice_id=pk)
    except BillingServiceError as exc:
        messages.error(request, str(exc))
        return redirect("billing:document_detail", doc_type="invoice", pk=pk)
    messages.success(request, "Invoice cancelled.")
    return redirect("billing:document_detail", doc_type="invoice", pk=invoice.pk)


@login_required
def reissue_invoice(request, pk: int):
    organization = require_organization(request)
    _require_billing_write(request)
    if request.method != "POST":
        raise PermissionDenied("POST required.")
    try:
        invoice = BillingService.reissue_invoice(organization=organization, performed_by=request.user, invoice_id=pk)
    except BillingServiceError as exc:
        messages.error(request, str(exc))
        return redirect("billing:document_detail", doc_type="invoice", pk=pk)
    messages.success(request, "Replacement invoice created.")
    return redirect("billing:document_detail", doc_type="invoice", pk=invoice.pk)


@login_required
def create_credit_note(request, pk: int):
    organization = require_organization(request)
    _require_billing_write(request)
    if request.method != "POST":
        raise PermissionDenied("POST required.")
    try:
        credit_note = BillingService.create_credit_note(organization=organization, performed_by=request.user, invoice_id=pk)
    except BillingServiceError as exc:
        messages.error(request, str(exc))
        return redirect("billing:document_detail", doc_type="invoice", pk=pk)
    messages.success(request, "Credit note created.")
    return redirect("billing:document_detail", doc_type="credit_note", pk=credit_note.pk)


@login_required
def resolve_subscription_invoice_issue(request, period_id: int):
    organization = require_organization(request)
    _require_billing_write(request)

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

    can_resolve = (
        period.status in {SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE}
        and period.receipt_id is None
        and period.invoice.status != BillingDocument.Status.PAID
        and period.invoice.status not in {BillingDocument.Status.CANCELLED, BillingDocument.Status.REISSUED}
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
        BillingDocument,
        organization=organization,
        document_type=BillingDocument.DocumentType.INVOICE,
        pk=pk,
    )

    existing_receipt = BillingDocument.objects.filter(
        organization=organization,
        document_type=BillingDocument.DocumentType.RECEIPT,
        invoice=invoice,
    ).order_by("-created_at").first()

    if request.method == "POST":
        form = ReceiptCreateForm(request.POST, organization=organization, invoice=invoice)
        if form.is_valid():
            try:
                receipt = BillingService.create_receipt_from_invoice(
                    organization=organization,
                    created_by=request.user,
                    invoice_id=invoice.id,
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
                    messages.success(request, "Receipt created and invoice marked as paid.")
                return redirect("billing:document_detail", doc_type="receipt", pk=receipt.pk)
    else:
        form = ReceiptCreateForm(
            organization=organization,
            invoice=invoice,
            initial={"payment_date": timezone.now().date()},
        )

    return render(request, "billing/receipt_from_invoice.html", {"invoice": invoice, "form": form})


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

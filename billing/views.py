from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.text import slugify
from django.views.generic import CreateView, ListView, UpdateView
from django.contrib.auth.mixins import LoginRequiredMixin

from users.permissions import PermissionCode, require_permission
from users.tenancy import require_organization

from .forms import BillingDocumentForm, BillingLineItemFormSet, DraftInvoiceEditForm, PromotionForm, ReceiptCreateForm, SubscriptionRenewalForm
from .models import BillingDocument, BillingLineItem, CustomerSubscription, Promotion
from .pdf import build_image_data_uri, render_pdf_or_html
from .services import BillingService, BillingServiceError, LineItemInput, SubscriptionBillingService, first_day_of_month


DOC_TYPE_DISPLAY = dict(BillingDocument.DocumentType.choices)


class PromotionListView(LoginRequiredMixin, ListView):
    model = Promotion
    template_name = "billing/promotion_list.html"
    context_object_name = "promotions"

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        return Promotion.objects.filter(organization=organization).select_related("product", "package").order_by("-is_active", "name")


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

    documents = BillingDocument.objects.filter(organization=organization, document_type=doc_type).select_related("customer")
    if doc_type == BillingDocument.DocumentType.QUOTATION and request.GET.get("include_history") != "1":
        documents = documents.filter(is_current_version=True)
    documents = documents.order_by("-issue_date", "-created_at")
    paginator = Paginator(documents, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "billing/document_list.html",
        {
            "documents": page_obj.object_list,
            "page_obj": page_obj,
            "is_paginated": page_obj.has_other_pages(),
            "doc_type": doc_type,
            "doc_type_display": doc_type_display,
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
    if document.document_type == BillingDocument.DocumentType.INVOICE:
        has_receipt = BillingDocument.objects.filter(
            organization=organization,
            document_type=BillingDocument.DocumentType.RECEIPT,
            invoice_id=document.id,
        ).exists()
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
    return render_pdf_or_html(
        request=request,
        template_name="billing/document_print.html",
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
def create_receipt_from_invoice(request, pk: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.PAYMENT_REGISTER)

    invoice = get_object_or_404(
        BillingDocument,
        organization=organization,
        document_type=BillingDocument.DocumentType.INVOICE,
        pk=pk,
    )

    if request.method == "POST":
        form = ReceiptCreateForm(request.POST)
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
                messages.success(request, "Receipt created and invoice marked as paid.")
                return redirect("billing:document_detail", doc_type="receipt", pk=receipt.pk)
    else:
        form = ReceiptCreateForm(initial={"payment_date": timezone.now().date()})

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

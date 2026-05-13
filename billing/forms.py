from __future__ import annotations

from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from internetservices.tailwind import apply_tailwind

from .models import BillingDocument, BillingLineItem, CustomerSubscription, Promotion


class BillingDocumentForm(forms.ModelForm):
    class Meta:
        model = BillingDocument
        fields = ["customer", "issue_date", "due_date", "status", "currency", "tax_rate", "notes"]
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, organization=None, doc_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.fields["customer"].queryset = self.fields["customer"].queryset.filter(organization=organization)
        if doc_type == BillingDocument.DocumentType.INVOICE:
            allowed_statuses = {
                BillingDocument.Status.DRAFT,
                BillingDocument.Status.SENT,
                BillingDocument.Status.ISSUED,
            }
            self.fields["status"].choices = [
                choice for choice in self.fields["status"].choices if choice[0] in allowed_statuses
            ]
        self.fields["currency"].widget.attrs.update({"placeholder": "TZS", "maxlength": 10})
        self.fields["tax_rate"].widget.attrs.update({"min": "0", "step": "0.01"})
        self.fields["notes"].widget.attrs.update(
            {"rows": 4, "placeholder": "Add payment terms, installation notes, or any customer-facing remarks."}
        )
        apply_tailwind(self)


class BillingLineItemForm(forms.ModelForm):
    class Meta:
        model = BillingLineItem
        fields = [
            "product",
            "package",
            "description",
            "quantity",
            "unit_price",
            "billing_behavior",
            "pricing_mode",
            "discount_amount",
            "discount_reason",
            "promotion",
        ]
        widgets = {
            "description": forms.Textarea(
                attrs={
                    "rows": 2,
                    "placeholder": "Item description, commercial note, or any custom detail for this line.",
                }
            ),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.fields["product"].queryset = self.fields["product"].queryset.filter(organization=organization, is_active=True)
            self.fields["package"].queryset = self.fields["package"].queryset.filter(organization=organization, is_active=True)
            self.fields["promotion"].queryset = Promotion.objects.filter(organization=organization, is_active=True)
        self.fields["product"].required = False
        self.fields["package"].required = False
        self.fields["product"].empty_label = "Select product"
        self.fields["package"].empty_label = "Select package"
        self.fields["quantity"].initial = Decimal("1.00")
        self.fields["quantity"].widget.attrs.update({"min": "0.01", "step": "0.01"})
        self.fields["unit_price"].widget.attrs.update({"min": "0", "step": "0.01"})
        self.fields["discount_amount"].widget.attrs.update({"min": "0", "step": "0.01"})
        apply_tailwind(self)

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        package = cleaned.get("package")
        description = (cleaned.get("description") or "").strip()
        if product and package:
            raise forms.ValidationError("Select either a product or a package (not both).")
        if not product and not package and not description:
            raise forms.ValidationError("Provide a product, a package, or a description.")
        return cleaned


class BaseBillingLineItemFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        active_items = 0
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or not form.cleaned_data:
                continue
            if form.cleaned_data.get("DELETE"):
                continue

            product = form.cleaned_data.get("product")
            package = form.cleaned_data.get("package")
            description = (form.cleaned_data.get("description") or "").strip()
            quantity = form.cleaned_data.get("quantity")
            unit_price = form.cleaned_data.get("unit_price")
            discount_amount = form.cleaned_data.get("discount_amount") or Decimal("0.00")

            if not product and not package and not description:
                continue

            active_items += 1
            if quantity is None or quantity <= Decimal("0.00"):
                form.add_error("quantity", "Quantity must be greater than 0.")
            if unit_price is None or unit_price < Decimal("0.00"):
                form.add_error("unit_price", "Unit price cannot be negative.")
            if discount_amount < Decimal("0.00"):
                form.add_error("discount_amount", "Discount cannot be negative.")

        if active_items == 0:
            raise forms.ValidationError("Add at least one quotation item before saving.")


BillingLineItemFormSet = inlineformset_factory(
    BillingDocument,
    BillingLineItem,
    form=BillingLineItemForm,
    formset=BaseBillingLineItemFormSet,
    extra=1,
    can_delete=True,
)


class ReceiptCreateForm(forms.Form):
    payment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    payment_method = forms.CharField(max_length=50)
    payment_reference = forms.CharField(max_length=80, required=False, help_text="Optional transaction reference / idempotency key.")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, organization=None, invoice=None, **kwargs):
        self.organization = organization
        self.invoice = invoice
        super().__init__(*args, **kwargs)
        apply_tailwind(self)

    def clean_payment_reference(self):
        reference = (self.cleaned_data.get("payment_reference") or "").strip()
        if not reference or self.organization is None:
            return reference

        existing = BillingDocument.objects.unscoped().filter(
            organization=self.organization,
            payment_reference=reference,
        ).only("id", "document_type", "invoice_id").first()
        if existing is None:
            return reference
        if (
            self.invoice is not None
            and existing.document_type == BillingDocument.DocumentType.RECEIPT
            and existing.invoice_id == self.invoice.id
        ):
            return reference
        raise forms.ValidationError("This payment reference has already been used.")


class DraftInvoiceEditForm(forms.Form):
    tax_rate = forms.DecimalField(max_digits=5, decimal_places=2, initial=Decimal("18.00"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class SubscriptionRenewalForm(forms.Form):
    subscription = forms.ModelChoiceField(queryset=CustomerSubscription.objects.none())
    period_start = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    months = forms.IntegerField(min_value=1, max_value=24, initial=1)
    promotion = forms.ModelChoiceField(queryset=Promotion.objects.none(), required=False)
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), required=False)
    issue_invoice = forms.BooleanField(required=False, initial=True, label="Create invoice now")

    def __init__(self, *args, organization=None, customer=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            subscriptions = CustomerSubscription.objects.filter(
                organization=organization,
                status=CustomerSubscription.Status.ACTIVE,
            ).select_related("customer", "package")
            if customer is not None:
                subscriptions = subscriptions.filter(customer=customer)
            self.fields["subscription"].queryset = subscriptions
            self.fields["promotion"].queryset = Promotion.objects.filter(
                organization=organization,
                is_active=True,
                applies_to=Promotion.AppliesTo.PACKAGE,
            )
        apply_tailwind(self)


class SubscriptionInvoiceIssueForm(forms.Form):
    class Action:
        REISSUE = "reissue"
        VOID = "void"

    action = forms.ChoiceField(
        choices=[
            (Action.REISSUE, "Wrong amount, tax, package, or discount - reissue invoice"),
            (Action.VOID, "Invoice should not exist - void this billing period"),
        ],
        widget=forms.RadioSelect,
        label="What needs to happen?",
    )
    reason = forms.CharField(
        label="Reason",
        help_text="This is saved to the audit trail.",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Example: Invoice was created for the wrong month."}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if len(reason) < 5:
            raise forms.ValidationError("Add a short reason before resolving this invoice issue.")
        return reason


class PromotionForm(forms.ModelForm):
    class Meta:
        model = Promotion
        fields = [
            "name",
            "applies_to",
            "product",
            "package",
            "minimum_quantity",
            "minimum_months",
            "minimum_amount",
            "reward_type",
            "reward_value",
            "valid_from",
            "valid_until",
            "is_active",
        ]
        widgets = {
            "valid_from": forms.DateInput(attrs={"type": "date"}),
            "valid_until": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.fields["product"].queryset = self.fields["product"].queryset.filter(organization=organization)
            self.fields["package"].queryset = self.fields["package"].queryset.filter(organization=organization)
        apply_tailwind(self)

    def clean(self):
        cleaned = super().clean()
        valid_from = cleaned.get("valid_from")
        valid_until = cleaned.get("valid_until")
        if valid_from and valid_until and valid_until < valid_from:
            raise forms.ValidationError("Valid until cannot be earlier than valid from.")
        return cleaned

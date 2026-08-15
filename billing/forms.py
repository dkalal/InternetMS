from __future__ import annotations

from decimal import Decimal

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory

from internetservices.tailwind import apply_tailwind
from customers.models import CustomerSite

from .models import BillingDocument, BillingItem, BillingLineItem, BillingSheet, CustomerSubscription, Promotion


class BillingDocumentForm(forms.ModelForm):
    site = forms.ModelChoiceField(
        queryset=CustomerSite.objects.none(), required=False,
        help_text="Optional when every line concerns one customer site. Leave blank for account-wide or multi-site documents.",
    )

    class Meta:
        model = BillingDocument
        fields = ["customer", "site", "sale_pricing_category", "issue_date", "due_date", "status", "currency", "tax_rate", "notes"]
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, organization=None, doc_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.fields["customer"].queryset = self.fields["customer"].queryset.filter(organization=organization)
            self.fields["site"].queryset = CustomerSite.objects.filter(organization=organization, is_active=True).select_related("customer").order_by("customer__name", "-is_primary", "name")
            self.fields["site"].label_from_instance = lambda obj: f"{obj.customer.name} — {obj.name} ({obj.location})"
        if doc_type == BillingDocument.DocumentType.INVOICE:
            self.fields["status"].choices = BillingDocument.invoice_status_choices()
        elif doc_type == BillingDocument.DocumentType.QUOTATION:
            self.fields["status"].choices = BillingDocument.quotation_status_choices()
        self.fields["notes"].label = "Internal notes"
        self.fields["notes"].help_text = (
            "Visible to authorized staff only. Internal notes are never printed on customer invoices or quotations."
        )
        self.fields["currency"].widget.attrs.update({"placeholder": "TZS", "maxlength": 10})
        self.fields["sale_pricing_category"].choices = [
            choice for choice in BillingDocument.SalePricingCategory.choices
            if choice[0] != BillingDocument.SalePricingCategory.LEGACY_RETAIL
        ]
        self.fields["sale_pricing_category"].label = "Customer category"
        self.fields["sale_pricing_category"].help_text = "Use the customer's category automatically, or select an authorized transaction override."
        self.fields["tax_rate"].widget.attrs.update({"min": "0", "step": "0.01"})
        self.fields["notes"].widget.attrs.update(
            {"rows": 4, "placeholder": "Add staff-only context, approval notes, or operational follow-up."}
        )
        apply_tailwind(self)


class BillingLineItemForm(forms.ModelForm):
    class Meta:
        model = BillingLineItem
        fields = [
            "product",
            "package",
            "description",
            "unit_snapshot",
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
                    "rows": 3,
                    "placeholder": "Item name or description (e.g. Network troubleshooting, Cable installation)",
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
        self.fields["unit_snapshot"].label = "Unit"
        self.fields["unit_snapshot"].required = False
        self.fields["unit_snapshot"].widget.attrs.update({"maxlength": "50", "placeholder": "Unit"})
        self.fields["quantity"].widget.attrs.update({"min": "0.01", "step": "0.01"})
        self.fields["unit_price"].widget.attrs.update({"min": "0", "step": "0.01"})
        self.fields["discount_amount"].widget.attrs.update({"min": "0", "step": "0.01"})
        apply_tailwind(self)

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        package = cleaned.get("package")
        description = (cleaned.get("description") or "").strip()
        unit_snapshot = (cleaned.get("unit_snapshot") or "Unit").strip()
        cleaned["unit_snapshot"] = unit_snapshot
        if product and package:
            raise forms.ValidationError("Select either a product or a package (not both).")
        if not product and not package and not description:
            raise forms.ValidationError("Provide a product, a package, or a description.")
        if product and unit_snapshot and product.catalog_category_id:
            allowed = {unit.label for unit in product.catalog_category.allowed_units.filter(is_active=True)}
            if unit_snapshot not in allowed:
                self.add_error("unit_snapshot", "Select a unit allowed by the product category.")
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
    amount_paid = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        label="Amount paid",
        help_text="Enter the actual amount received. If less than the invoice total, the invoice will be marked partially paid.",
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
    )
    payment_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    payment_method = forms.ChoiceField(choices=[
        ('cash', 'Cash'),
        ('bank', 'Bank'),
        ('mobile_money', 'Mobile money'),
        ('card', 'Credit/Debit card'),
        ('other', 'Other'),
    ])
    payment_reference = forms.CharField(max_length=80, required=False, help_text="Optional transaction reference / idempotency key.")
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)

    def __init__(self, *args, organization=None, invoice=None, **kwargs):
        self.organization = organization
        self.invoice = invoice
        super().__init__(*args, **kwargs)
        self.requires_full_payment = False
        self.remaining_balance = None
        if invoice is not None and organization is not None:
            from .services import BillingService

            policy = BillingService.invoice_payment_policy(organization=organization, invoice=invoice)
            self.requires_full_payment = policy['requires_full_payment']
            self.remaining_balance = policy['remaining_balance']
            self.fields['amount_paid'].widget.attrs['max'] = str(self.remaining_balance)
        self.has_inventory_items = self.requires_full_payment
        if self.requires_full_payment:
            self.fields["amount_paid"].help_text = (
                "Inventory invoices require one complete payment. Enter the full outstanding balance."
            )
        apply_tailwind(self)

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get("customer")
        site = cleaned.get("site")
        if site and customer and site.customer_id != customer.id:
            self.add_error("site", "Select a site belonging to the document customer.")
        return cleaned

    def clean_amount_paid(self):
        amount = self.cleaned_data.get('amount_paid')
        if amount is None or self.remaining_balance is None:
            return amount
        if amount > self.remaining_balance:
            raise forms.ValidationError(
                f'Payment cannot exceed the remaining balance ({self.remaining_balance:,.2f}).'
            )
        if self.requires_full_payment and amount != self.remaining_balance:
            raise forms.ValidationError(
                f'Inventory sales require complete payment of {self.remaining_balance:,.2f}.'
            )
        return amount

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
    status = forms.ChoiceField(
        choices=[
            (BillingDocument.Status.DRAFT, "Draft"),
            (BillingDocument.Status.ISSUED, "Issued"),
        ],
        initial=BillingDocument.Status.DRAFT,
        help_text="Save as Draft while editing, or mark as Issued when the invoice is ready to send.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class SubscriptionRenewalForm(forms.Form):
    subscription = forms.ModelChoiceField(queryset=CustomerSubscription.objects.none())
    period_start = forms.DateField(
        label="Coverage starts",
        help_text="The system normalizes subscription coverage to the first day of this month.",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    months = forms.IntegerField(
        min_value=1,
        max_value=24,
        initial=1,
        label="Months to cover",
        help_text="The invoice and billing period will automatically store the resulting coverage end date.",
    )
    promotion = forms.ModelChoiceField(queryset=Promotion.objects.none(), required=False)
    due_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), required=False)
    issue_invoice = forms.BooleanField(required=False, initial=True, label="Create invoice now")

    def __init__(self, *args, organization=None, customer=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            subscriptions = CustomerSubscription.objects.filter(
                organization=organization,
                status=CustomerSubscription.Status.ACTIVE,
            ).select_related("customer", "package", "site")
            if customer is not None:
                subscriptions = subscriptions.filter(customer=customer)
            self.fields["subscription"].queryset = subscriptions
            self.fields["subscription"].label_from_instance = lambda obj: f"{obj.customer.name} - {obj.site.name if obj.site_id else 'Main Office'} - {obj.package.name}"
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


class CancelSubscriptionForm(forms.Form):
    reason = forms.CharField(
        label="Reason",
        help_text="Saved to the audit trail.",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Example: Package was assigned to the wrong customer."}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if len(reason) < 5:
            raise forms.ValidationError("Add a short reason before cancelling.")
        return reason


class InvoiceActionForm(forms.Form):
    reason = forms.CharField(
        label="Reason",
        help_text="This is saved to the audit trail.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, action_label: str = "continue", placeholder: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["placeholder"] = placeholder or f"Why does this invoice need to {action_label}?"
        apply_tailwind(self)

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if len(reason) < 5:
            raise forms.ValidationError("Add a short reason before continuing.")
        return reason


class CreditNoteCreateForm(InvoiceActionForm):
    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Enter the total amount to credit from the current remaining balance.",
        widget=forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
    )
    issue_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, action_label="credit", placeholder="Example: Discount approved after the invoice was issued.", **kwargs)


class QuotationActionForm(forms.Form):
    reason = forms.CharField(
        label="Notes",
        required=False,
        help_text="Optional note saved to the audit trail.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, action_label: str = "continue", placeholder: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].widget.attrs["placeholder"] = placeholder or f"Optional note for this {action_label} action."
        apply_tailwind(self)


class BillingSheetGenerateForm(forms.Form):
    due_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        label="Due date",
        help_text="Optional. Leave blank if no payment deadline applies.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class BillingSheetForm(forms.ModelForm):
    class Meta:
        model = BillingSheet
        fields = ["customer", "title", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.fields["customer"].queryset = self.fields["customer"].queryset.filter(
                organization=organization, status="active"
            )
        apply_tailwind(self)


class BillingItemForm(forms.ModelForm):
    class Meta:
        model = BillingItem
        fields = ["description", "quantity", "unit_price", "notes"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["quantity"].widget.attrs.update({"min": "0.01", "step": "0.01"})
        self.fields["unit_price"].widget.attrs.update({"min": "0", "step": "0.01"})
        apply_tailwind(self)

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        if quantity is not None and quantity <= Decimal("0.00"):
            raise forms.ValidationError("Quantity must be greater than 0.")
        return quantity

    def clean_unit_price(self):
        unit_price = self.cleaned_data.get("unit_price")
        if unit_price is not None and unit_price < Decimal("0.00"):
            raise forms.ValidationError("Unit price cannot be negative.")
        return unit_price


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

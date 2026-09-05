from datetime import date
from decimal import Decimal

from django import forms
from django.forms import BaseFormSet, formset_factory
from django.utils import timezone

from customers.models import Customer
from internetservices.tailwind import apply_tailwind

from .models import TechnicianPaymentRecord, TechnicianWorkReport


REPORT_FIELDS = (
    "work_title", "client_name", "customer", "work_location",
    "activity_description", "agreed_amount", "internal_notes",
)


class WorkReportForm(forms.ModelForm):
    class Meta:
        model = TechnicianWorkReport
        fields = REPORT_FIELDS
        widgets = {
            "activity_description": forms.Textarea(attrs={"rows": 6}),
            "internal_notes": forms.Textarea(attrs={"rows": 3}),
            "agreed_amount": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
        }
        labels = {
            "work_title": "Work title or type",
            "client_name": "Client or company name",
            "customer": "Linked customer (optional)",
            "activity_description": "Work description",
            "agreed_amount": "Total agreed amount for the complete job",
        }
        help_texts = {
            "client_name": "Optional. Add a client or company when there is one.",
            "customer": "Choose a customer only when this work relates to an existing tenant customer.",
            "activity_description": "Describe the complete job. Work-date notes can add optional daily detail.",
            "agreed_amount": "One private total for the complete job; visible only to you and authorized Managers.",
            "internal_notes": "Optional internal context for the reviewer.",
        }

    def __init__(self, *args, tenant, **kwargs):
        super().__init__(*args, **kwargs)
        # Model validation runs during ModelForm validation; bind the trusted
        # server-side tenant so cross-tenant customer checks are meaningful.
        if not self.instance.tenant_id:
            self.instance.tenant = tenant
        self.fields["customer"].queryset = Customer.all_objects.filter(
            tenant=tenant, is_deleted=False,
        ).order_by("name")
        self.fields["work_title"].widget.attrs.setdefault("placeholder", "Installation, maintenance, site survey…")
        self.fields["client_name"].widget.attrs.setdefault("placeholder", "Customer or company")
        self.fields["work_location"].widget.attrs.setdefault("placeholder", "Site, branch, area, or address")
        apply_tailwind(self)


class WorkDateForm(forms.Form):
    service_date = forms.DateField(
        required=False,
        label="Work date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    activity_note = forms.CharField(
        required=False,
        max_length=500,
        label="Work done that day (optional)",
        widget=forms.TextInput(attrs={"placeholder": "Optional daily detail"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["service_date"].widget.attrs["max"] = timezone.localdate().isoformat()
        apply_tailwind(self)


class BaseWorkDateFormSet(BaseFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        form.fields["DELETE"].widget = forms.HiddenInput()

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        seen = set()
        valid_dates = 0
        today = timezone.localdate()
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue
            service_date = form.cleaned_data.get("service_date")
            activity_note = (form.cleaned_data.get("activity_note") or "").strip()
            if not service_date:
                if activity_note:
                    form.add_error("service_date", "Choose a work date for this note.")
                continue
            valid_dates += 1
            if service_date > today:
                form.add_error("service_date", "Work dates cannot be in the future.")
            if service_date in seen:
                form.add_error("service_date", "This work date is already listed.")
            seen.add(service_date)
        if valid_dates == 0:
            raise forms.ValidationError("Add at least one work date.")


WorkDateFormSet = formset_factory(
    WorkDateForm,
    formset=BaseWorkDateFormSet,
    extra=0,
    can_delete=True,
)

class RejectionForm(forms.Form):
    reason = forms.CharField(
        label="Reason for correction",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Explain exactly what the Technician needs to correct."}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class ApprovedCorrectionForm(WorkReportForm):
    correction_reason = forms.CharField(
        label="Correction reason",
        help_text="Required. The prior approved values and this reason remain in immutable history.",
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class TechnicianPaymentForm(forms.ModelForm):
    confirm_adjusted_amount = forms.BooleanField(
        required=False,
        label="I approve this adjusted final amount",
    )

    class Meta:
        model = TechnicianPaymentRecord
        fields = (
            "amount_paid", "payment_date", "payment_method", "reference",
            "manager_note", "adjustment_reason",
        )
        widgets = {
            "amount_paid": forms.NumberInput(attrs={"min": "0.01", "step": "0.01"}),
            "payment_date": forms.DateInput(attrs={"type": "date"}),
            "manager_note": forms.Textarea(attrs={"rows": 3}),
            "adjustment_reason": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "amount_paid": "Amount paid",
            "reference": "Reference (optional)",
            "manager_note": "Manager note (optional)",
            "adjustment_reason": "Adjustment reason",
        }
        help_texts = {
            "payment_method": "Descriptive label only; JBMS does not validate or transfer money.",
            "adjustment_reason": "Required when the final amount differs from the approved agreed amount.",
        }

    def __init__(self, *args, report, **kwargs):
        super().__init__(*args, **kwargs)
        self.report = report
        self.fields["amount_paid"].initial = report.agreed_amount
        apply_tailwind(self)

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("amount_paid")
        if amount is not None and amount != self.report.agreed_amount:
            reason = (cleaned.get("adjustment_reason") or "").strip()
            if not reason:
                self.add_error("adjustment_reason", "Explain why the final paid amount differs.")
            if cleaned.get("confirm_adjusted_amount") is not True:
                self.add_error(
                    "confirm_adjusted_amount",
                    "Explicitly approve the adjusted final amount.",
                )
        return cleaned


class TechnicianPaymentBatchForm(forms.Form):
    payment_date = forms.DateField(
        label="Payment date", widget=forms.DateInput(attrs={"type": "date"}),
    )
    payment_method = forms.ChoiceField(
        label="Payment method", choices=TechnicianPaymentRecord.PaymentMethod.choices,
        help_text="Descriptive label only; JBMS does not validate or transfer money.",
    )
    method_description = forms.CharField(
        label="Describe other method", required=False, max_length=120,
    )
    reference = forms.CharField(label="Reference (optional)", required=False, max_length=200)
    manager_note = forms.CharField(
        label="Manager note (optional)", required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )

    def __init__(self, *args, reports, **kwargs):
        super().__init__(*args, **kwargs)
        self.reports = list(reports)
        if not self.is_bound:
            self.fields["payment_date"].initial = date.today()
        for report in self.reports:
            prefix = f"allocation_{report.pk}"
            self.fields[f"{prefix}_amount_paid"] = forms.DecimalField(
                label="Final paid amount", min_value=Decimal("0.01"),
                max_digits=14, decimal_places=2, initial=report.agreed_amount,
                widget=forms.NumberInput(attrs={"min": "0.01", "step": "0.01"}),
            )
            self.fields[f"{prefix}_adjustment_reason"] = forms.CharField(
                label="Adjustment reason", required=False,
                widget=forms.Textarea(attrs={"rows": 2}),
            )
            self.fields[f"{prefix}_confirm_adjusted_amount"] = forms.BooleanField(
                label="I approve this adjusted final amount", required=False,
            )
        apply_tailwind(self)

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("payment_method") == TechnicianPaymentRecord.PaymentMethod.OTHER
            and not (cleaned.get("method_description") or "").strip()
        ):
            self.add_error("method_description", "Describe the payment method.")
        for report in self.reports:
            prefix = f"allocation_{report.pk}"
            amount = cleaned.get(f"{prefix}_amount_paid")
            if amount is not None and amount != report.agreed_amount:
                if not (cleaned.get(f"{prefix}_adjustment_reason") or "").strip():
                    self.add_error(
                        f"{prefix}_adjustment_reason",
                        "Explain why this final amount differs.",
                    )
                if cleaned.get(f"{prefix}_confirm_adjusted_amount") is not True:
                    self.add_error(
                        f"{prefix}_confirm_adjusted_amount",
                        "Explicitly approve this adjusted final amount.",
                    )
        return cleaned

    def allocations(self):
        return [
            {
                "report_id": report.pk,
                "amount_paid": self.cleaned_data[f"allocation_{report.pk}_amount_paid"],
                "adjustment_reason": self.cleaned_data.get(
                    f"allocation_{report.pk}_adjustment_reason", "",
                ),
                "confirm_adjusted_amount": self.cleaned_data.get(
                    f"allocation_{report.pk}_confirm_adjusted_amount", False,
                ),
            }
            for report in self.reports
        ]


class PaymentDisputeForm(forms.Form):
    reason = forms.CharField(
        label="Dispute reason",
        min_length=5,
        strip=True,
        widget=forms.Textarea(attrs={
            "rows": 4,
            "placeholder": "Explain what is incorrect about this payment record.",
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class PaymentVoidForm(forms.Form):
    reason = forms.CharField(
        label="Reason for voiding",
        min_length=5,
        strip=True,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)

from django import forms

from customers.models import Customer
from internetservices.tailwind import apply_tailwind

from .models import TechnicianWorkReport


REPORT_FIELDS = (
    "work_title", "client_name", "customer", "service_date", "work_location",
    "activity_description", "agreed_amount", "internal_notes",
)


class WorkReportForm(forms.ModelForm):
    class Meta:
        model = TechnicianWorkReport
        fields = REPORT_FIELDS
        widgets = {
            "service_date": forms.DateInput(attrs={"type": "date"}),
            "activity_description": forms.Textarea(attrs={"rows": 6}),
            "internal_notes": forms.Textarea(attrs={"rows": 3}),
            "agreed_amount": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
        }
        labels = {
            "work_title": "Work title or type",
            "client_name": "Client or company name",
            "customer": "Linked customer (optional)",
            "activity_description": "Work completed",
            "agreed_amount": "Agreed Technician amount",
        }
        help_texts = {
            "customer": "Choose a customer only when this work relates to an existing tenant customer.",
            "agreed_amount": "Private: visible only to you and authorized Managers.",
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

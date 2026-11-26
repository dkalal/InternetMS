from django import forms
from django.core import signing
from uuid import uuid4

from customers.models import Customer
from internetservices.tailwind import apply_tailwind
from users.models import TenantMembership
from .assignment_services import EDIT_FIELDS
from .models import WorkAssignment


class AssignmentForm(forms.ModelForm):
    request_token = forms.CharField(widget=forms.HiddenInput, required=False)
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)
    reason = forms.CharField(required=False, label="Revision / reassignment reason", widget=forms.Textarea(attrs={"rows": 2}))

    class Meta:
        model = WorkAssignment
        fields = EDIT_FIELDS
        widgets = {
            "proposed_start": forms.DateInput(attrs={"type": "date"}),
            "proposed_end": forms.DateInput(attrs={"type": "date"}),
            "scope": forms.Textarea(attrs={"rows": 4}),
            "instructions": forms.Textarea(attrs={"rows": 2}),
            "management_notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, tenant, membership, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.tenant = tenant
        if not self.instance.pk:
            self.fields["request_token"].required = True
            self.fields["request_token"].initial = signing.dumps(
                {"tenant": tenant.pk, "creator": membership.pk, "key": str(uuid4())},
                salt="work-assignment-create",
            )
        self.fields["technician"].queryset = TenantMembership.objects.filter(
            tenant=tenant, base_role=TenantMembership.BaseRole.TECHNICIAN,
            is_active=True, user__is_active=True,
        ).select_related("user", "tenant")
        self.fields["customer"].queryset = Customer.all_objects.filter(tenant=tenant, is_deleted=False)
        self.fields["version"].initial = self.instance.version
        self.fields["management_notes"].help_text = "Private to management; never shared with the Technician."
        apply_tailwind(self)


class AssignmentActionForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    revision = forms.IntegerField(widget=forms.HiddenInput, required=False)
    action = forms.ChoiceField(choices=[("accept", "Accept offer"), ("decline", "Decline"), ("request_changes", "Request changes")])
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    proposed_amount = forms.DecimalField(required=False, max_digits=14, decimal_places=2, min_value=0)
    proposed_start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    proposed_end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, manager=False, status=None, **kwargs):
        super().__init__(*args, **kwargs)
        if manager:
            choices = [("cancel", "Cancel assignment")]
            if status == WorkAssignment.Status.DRAFT:
                choices.insert(0, ("send", "Send draft offer"))
            self.fields["action"].choices = choices
            for name in ("proposed_amount", "proposed_start", "proposed_end"):
                del self.fields[name]
        self.fields["reason"].help_text = "Required for cancellation, decline or a change request."
        if not manager:
            for name in ("proposed_amount", "proposed_start", "proposed_end"):
                self.fields[name].help_text = "Optional suggestion when requesting changes; it does not alter this offer."
        apply_tailwind(self)

    def clean(self):
        data = super().clean()
        if data.get("action") in {"cancel", "decline", "request_changes"} and not data.get("reason", "").strip():
            self.add_error("reason", "A reason is required.")
        return data

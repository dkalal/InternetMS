from django import forms
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from internetservices.tailwind import apply_tailwind
from custom_fields.forms import CustomFieldFormMixin
from services.models import Package

from .models import Customer, CustomerSite, InternetCustomer, InternetService


class CustomerForm(CustomFieldFormMixin, forms.ModelForm):
    custom_field_target_model = "customer"

    status_change_reason = forms.CharField(
        required=False,
        label='Status change reason',
        help_text='Required when changing customer status. This is saved to the audit trail.',
        widget=forms.Textarea(attrs={'rows': 3, 'data-status-reason': 'true'}),
    )
    packages = forms.ModelMultipleChoiceField(
        queryset=Package.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='Service packages',
        help_text='Choose the default packages for the primary site. Add sites from the customer page for separate offices.',
    )

    class Meta:
        model = Customer
        fields = [
            'name',
            'customer_type',
            'status',
            'pricing_tier',
            'email',
            'phone',
            'address',
            'location',
            'ip_address',
            'vlan_id',
            'tin_number',
            'vrn_number',
            'packages',
            'status_change_reason',
        ]
        widgets = {
            'customer_type': forms.Select(attrs={'id': 'customer-type-select'}),
        }
        help_texts = {
            'phone': 'Use an international format when possible, for example +255712345678.',
            'ip_address': 'IPv4 or IPv6 address assigned to this customer.',
            'vlan_id': 'Network VLAN or segment identifier, if applicable.',
            'tin_number': 'Taxpayer Identification Number, if available.',
            'vrn_number': 'VAT Registration Number for VAT-registered customers.',
            'pricing_tier': 'Default product pricing used by automatic POS, quotation, invoice, and API sales.',
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        self.original_status = self.instance.status if self.instance and self.instance.pk else None
        self._assigned_package_ids = set()
        if organization is not None:
            if self.instance and self.instance.pk:
                self._assigned_package_ids.update(
                    self.instance.packages.filter(organization=organization).values_list('pk', flat=True)
                )

                primary_site = (
                    self.instance.sites.filter(organization=organization, is_primary=True)
                    .only('pk')
                    .first()
                )
                if primary_site is not None:
                    self._assigned_package_ids.update(
                        primary_site.packages.filter(organization=organization).values_list('pk', flat=True)
                    )

                active_subscriptions = self.instance.subscriptions.filter(
                    organization=organization,
                    status='active',
                )
                if primary_site is not None:
                    active_subscriptions = active_subscriptions.filter(
                        Q(site=primary_site) | Q(site__isnull=True)
                    )
                else:
                    active_subscriptions = active_subscriptions.filter(site__isnull=True)
                self._assigned_package_ids.update(
                    active_subscriptions.values_list('package_id', flat=True)
                )

            self.fields['packages'].queryset = (
                Package.objects.filter(organization=organization)
                .filter(Q(is_active=True) | Q(pk__in=self._assigned_package_ids))
                .order_by('name', 'pk')
            )
            self.fields['packages'].label_from_instance = self._package_choice_label
        self.fields['name'].widget.attrs.setdefault('placeholder', 'Customer or business name')
        self.fields['location'].widget.attrs.setdefault('placeholder', 'Area, ward, street, or landmark')
        self.fields['vlan_id'].widget.attrs.setdefault('placeholder', 'VLAN 120')
        self.fields['tin_number'].widget.attrs.setdefault('placeholder', 'TIN')
        self.fields['vrn_number'].widget.attrs.setdefault('placeholder', 'VRN')
        apply_tailwind(self)

    @property
    def selected_package_ids(self):
        """Return normalized IDs for the custom package picker on bound and edit forms."""
        package_field = self.fields['packages']
        if self.is_bound and not package_field.disabled:
            field_name = self.add_prefix('packages')
            if hasattr(self.data, 'getlist'):
                values = self.data.getlist(field_name)
            else:
                values = self.data.get(field_name, [])
                if not isinstance(values, (list, tuple, set)):
                    values = [values] if values not in (None, '') else []
            return {str(value) for value in values}

        if self.instance and self.instance.pk:
            allowed_ids = set(package_field.queryset.values_list('pk', flat=True))
            return {str(value) for value in self._assigned_package_ids if value in allowed_ids}
        return set()

    @staticmethod
    def _package_choice_label(package):
        """Expose the commercial and technical package details at selection time."""
        speed = (package.speed or "Speed not specified").strip()
        return f"{package.name} | {speed} | TZS {package.monthly_fee:,.0f}/month"

    def clean_status_change_reason(self):
        reason = (self.cleaned_data.get('status_change_reason') or '').strip()
        new_status = self.cleaned_data.get('status')
        if self.original_status and new_status and self.original_status != new_status and not reason:
            raise forms.ValidationError('Add a reason before changing this customer status.')
        return reason


class InternetCustomerForm(forms.ModelForm):
    class Meta:
        model = InternetCustomer
        fields = ['package_type', 'start_date', 'end_date']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, customer_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False
        self.fields['package_type'].label = 'Connection type'
        self.fields['package_type'].help_text = (
            'Describes the installed connection. Service packages are selected separately.'
        )
        self.fields['start_date'].label = 'Service start date'
        self.fields['end_date'].label = 'Service end / renewal date'
        apply_tailwind(self)


class CustomerSiteForm(forms.ModelForm):
    packages = forms.ModelMultipleChoiceField(
        queryset=Package.objects.filter(is_active=True),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label='Service packages',
        help_text='Choose the packages active at this site.',
    )

    class Meta:
        model = CustomerSite
        fields = [
            'name',
            'location',
            'address',
            'ip_address',
            'vlan_id',
            'is_primary',
            'is_active',
            'notes',
            'packages',
        ]
        widgets = {
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        help_texts = {
            'is_primary': 'Mark one site as the default billing and service location.',
            'is_active': 'Inactive sites stay on record but are excluded from new billing work.',
        }

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization is not None:
            self.fields['packages'].queryset = Package.objects.filter(is_active=True, organization=organization)
            self.fields['packages'].label_from_instance = CustomerForm._package_choice_label
        self.fields['name'].widget.attrs.setdefault('placeholder', 'Main office, branch, or POP name')
        self.fields['location'].widget.attrs.setdefault('placeholder', 'Area, ward, street, or landmark')
        self.fields['vlan_id'].widget.attrs.setdefault('placeholder', 'VLAN 120')
        apply_tailwind(self)


class InternetServiceCreateForm(forms.ModelForm):
    package = forms.ModelChoiceField(
        queryset=Package.objects.none(), required=False,
        label="Initial service package",
        help_text="Optional. Assigning a package creates the first commercial subscription.",
    )
    subscription_start_date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}),
        label="Subscription start date",
    )

    class Meta:
        model = InternetService
        fields = ["site", "service_code", "name", "ip_address", "vlan_id", "installed_at", "technical_notes"]
        widgets = {
            "installed_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "technical_notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, organization, customer, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.customer = customer
        self.fields["site"].queryset = customer.sites.filter(
            tenant=organization, is_active=True,
        ).order_by("-is_primary", "name", "id")
        self.fields["package"].queryset = Package.objects.filter(
            tenant=organization, is_active=True,
        ).order_by("name", "id")
        self.fields["package"].label_from_instance = CustomerForm._package_choice_label
        self.fields["name"].initial = "Primary Internet Service"
        self.fields["service_code"].help_text = "Stable tenant-unique reference, for example CUST-104-FIBRE-01."
        apply_tailwind(self)

    def clean(self):
        cleaned = super().clean()
        site = cleaned.get("site")
        package = cleaned.get("package")
        start_date = cleaned.get("subscription_start_date")
        if site and (site.tenant_id != self.organization.id or site.customer_id != self.customer.id):
            self.add_error("site", "Select a site belonging to this customer and tenant.")
        if package and package.tenant_id != self.organization.id:
            self.add_error("package", "Select a package belonging to this tenant.")
        if package and not start_date:
            self.add_error("subscription_start_date", "Set the commercial start date for the selected package.")
        return cleaned


class ServicePackageChangeForm(forms.Form):
    package = forms.ModelChoiceField(queryset=Package.objects.none(), label="New package")
    effective_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    reason = forms.CharField(
        min_length=5, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Recorded in the audit trail. The current subscription remains historical.",
    )

    def __init__(self, *args, organization, service, **kwargs):
        super().__init__(*args, **kwargs)
        self.service = service
        current = service.current_subscription
        queryset = Package.objects.filter(tenant=organization, is_active=True)
        if current is not None:
            queryset = queryset.exclude(pk=current.package_id)
            self.fields["effective_date"].initial = max(timezone.localdate(), current.start_date + timedelta(days=1))
        self.fields["package"].queryset = queryset.order_by("name", "id")
        self.fields["package"].label_from_instance = CustomerForm._package_choice_label
        apply_tailwind(self)


class ServiceStatusChangeForm(forms.Form):
    reason = forms.CharField(
        min_length=5, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Required and retained in the audit trail.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)


class HardDeleteCustomerForm(forms.Form):
    confirm_phrase = forms.CharField(
        help_text="Type: DELETE <customer_id>",
        label="Confirmation phrase",
    )
    confirm_one = forms.BooleanField(label="I understand this permanently deletes the customer.")
    confirm_two = forms.BooleanField(label="I understand this cannot be undone.")

    def __init__(self, *args, customer_id: int, **kwargs):
        super().__init__(*args, **kwargs)
        self.customer_id = customer_id
        apply_tailwind(self)

    def clean_confirm_phrase(self):
        phrase = (self.cleaned_data.get("confirm_phrase") or "").strip()
        expected = f"DELETE {self.customer_id}"
        if phrase != expected:
            raise forms.ValidationError(f"Type exactly: {expected}")
        return phrase


class AnonymizeCustomerForm(forms.Form):
    confirm_one = forms.BooleanField(label="I understand this removes customer PII.")
    confirm_two = forms.BooleanField(label="I understand financial history is preserved.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_tailwind(self)

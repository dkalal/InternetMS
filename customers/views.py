from django.shortcuts import render, get_object_or_404, redirect
from datetime import timedelta
from django.views.generic import TemplateView, ListView, DetailView, CreateView, UpdateView, DeleteView
from django.views.generic.edit import FormView
from django.urls import reverse_lazy, reverse
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.db.models import Max, OuterRef, Q, Subquery, Sum
from django.utils import timezone

from billing.models import BillingDocument, CustomerSubscription, SubscriptionPeriod

from .models import Customer, InternetCustomer
from .forms import (
    CustomerForm,
    InternetCustomerForm,
    HardDeleteCustomerForm,
    AnonymizeCustomerForm,
)
from .services import CustomerService, CustomerServiceError
from users.permissions import PermissionCode, require_permission
from users.tenancy import require_organization
from internetservices.listing import apply_sort, clean_page_size, page_context


# --- Function-based view example ---
@login_required
def home_view(request):
    organization = require_organization(request)
    require_permission(request, PermissionCode.TENANT_READ)
    context = {
        'page_title': 'Home',
        'welcome_message': 'Welcome to our customer portal!',
        'featured_customers': Customer.objects.for_organization(organization).active()[:5],
    }
    return render(request, 'customers/home.html', context)

# --- Class-based TemplateView example ---
class AboutView(TemplateView):
    template_name = 'customers/about.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = 'About Us'
        context['mission_statement'] = 'Our mission is to provide excellent customer service.'
        context['team_members'] = [
            {'name': 'Justin Safari', 'position': 'CEO'},
            {'name': 'Rashid Mohamed', 'position': 'CTO'},
            {'name': 'Othman H', 'position': 'Lead Developer'},
        ]
        return context

# --- ListView example ---
class CustomerListView(LoginRequiredMixin, ListView):
    model = Customer
    template_name = 'customers/customer_list.html'
    context_object_name = 'customers'
    paginate_by = 25
    sort_options = {
        "created": ("-created_at", "-id"),
        "name": ("name", "id"),
        "-name": ("-name", "-id"),
        "status": ("status", "name"),
        "-status": ("-status", "name"),
        "type": ("customer_type", "name"),
        "-type": ("-customer_type", "name"),
        "billing": ("unpaid_amount", "name"),
        "-billing": ("-unpaid_amount", "name"),
        "paid_through": ("latest_paid_through", "name"),
        "-paid_through": ("-latest_paid_through", "name"),
    }

    def get_paginate_by(self, queryset):
        return clean_page_size(self.request.GET.get("page_size"), default=self.paginate_by)

    def _unpaid_customer_ids(self, organization):
        return SubscriptionPeriod.objects.filter(
            organization=organization,
            status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
        ).values("subscription__customer_id")

    def _due_soon_customer_ids(self, organization):
        today = timezone.localdate()
        month_end = today.replace(day=28)
        while True:
            next_day = month_end + timedelta(days=1)
            if next_day.month != month_end.month:
                break
            month_end = next_day
        return CustomerSubscription.objects.filter(
            organization=organization,
            status=CustomerSubscription.Status.ACTIVE,
        ).filter(
            Q(paid_through_date__isnull=True) | Q(paid_through_date__lte=month_end)
        ).values("customer_id")

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        queryset = (
            Customer.objects.for_organization(organization)
            .optimized_list()
            .prefetch_related("subscriptions__package", "subscriptions__periods")
        )
        unpaid_periods = SubscriptionPeriod.objects.filter(
            organization=organization,
            subscription__customer_id=OuterRef("pk"),
            status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
            invoice__isnull=False,
        ).order_by("-period_start", "-id")
        queryset = queryset.annotate(
            unpaid_amount=Sum(
                "subscriptions__periods__final_amount",
                filter=Q(
                    subscriptions__periods__organization=organization,
                    subscriptions__periods__status__in=[
                        SubscriptionPeriod.Status.INVOICED,
                        SubscriptionPeriod.Status.OVERDUE,
                    ],
                ),
                distinct=True,
            ),
            latest_unpaid_invoice_id=Subquery(unpaid_periods.values("invoice_id")[:1]),
            latest_paid_through=Max(
                "subscriptions__paid_through_date",
                filter=Q(subscriptions__organization=organization, subscriptions__status=CustomerSubscription.Status.ACTIVE),
            ),
        )
        
        # Filter by type
        customer_type = self.request.GET.get('type')
        if customer_type:
            queryset = queryset.filter(customer_type=customer_type)
        
        # Filter by status
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)

        worklist = self.request.GET.get('worklist')
        if worklist == 'unpaid':
            queryset = queryset.filter(id__in=self._unpaid_customer_ids(organization))
        elif worklist == 'due':
            queryset = queryset.filter(id__in=self._due_soon_customer_ids(organization))
        elif worklist == 'no_contact':
            queryset = queryset.filter(
                (Q(email__isnull=True) | Q(email="")) &
                (Q(phone__isnull=True) | Q(phone=""))
            )
        elif worklist == 'inactive':
            queryset = queryset.inactive()
        elif worklist == 'active':
            queryset = queryset.active()
        elif worklist == 'today':
            queryset = queryset.filter(
                Q(id__in=self._unpaid_customer_ids(organization)) |
                Q(id__in=self._due_soon_customer_ids(organization)) |
                ((Q(email__isnull=True) | Q(email="")) & (Q(phone__isnull=True) | Q(phone="")))
            )
        
        # Search functionality
        search_query = self.request.GET.get('search')
        if search_query:
            queryset = queryset.search(search_query)

        queryset, self.active_sort = apply_sort(
            queryset.distinct(),
            self.request.GET.get("sort"),
            self.sort_options,
            "created",
        )
        return queryset

    def _whatsapp_link(self, phone):
        if not phone:
            return ""
        digits = "".join(ch for ch in phone if ch.isdigit())
        if digits.startswith("0") and len(digits) == 10:
            digits = "255" + digits[1:]
        elif len(digits) == 9:
            digits = "255" + digits
        return f"https://wa.me/{digits}" if digits else ""

    def _enrich_customers(self, customers, organization):
        customer_ids = [customer.id for customer in customers]
        if not customer_ids:
            return

        unpaid_rows = (
            SubscriptionPeriod.objects.filter(
                organization=organization,
                subscription__customer_id__in=customer_ids,
                status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
            )
            .values("subscription__customer_id")
            .annotate(amount=Sum("final_amount"))
        )
        unpaid_amounts = {row["subscription__customer_id"]: row["amount"] for row in unpaid_rows}

        invoice_ids = (
            SubscriptionPeriod.objects.filter(
                organization=organization,
                subscription__customer_id__in=customer_ids,
                status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
                invoice__isnull=False,
            )
            .order_by("subscription__customer_id", "-period_start")
            .values_list("subscription__customer_id", "invoice_id")
        )
        latest_invoice_by_customer = {}
        for customer_id, invoice_id in invoice_ids:
            latest_invoice_by_customer.setdefault(customer_id, invoice_id)

        today = timezone.localdate()
        for customer in customers:
            subscriptions = list(customer.subscriptions.all())
            customer.primary_subscription = subscriptions[0] if subscriptions else None
            customer.unpaid_amount = getattr(customer, "unpaid_amount", None) or unpaid_amounts.get(customer.id)
            customer.latest_unpaid_invoice_id = getattr(customer, "latest_unpaid_invoice_id", None) or latest_invoice_by_customer.get(customer.id)
            customer.whatsapp_url = self._whatsapp_link(customer.phone)

            if customer.unpaid_amount:
                customer.billing_state = "unpaid"
                customer.billing_label = "Unpaid"
                customer.billing_note = f"Balance: {customer.unpaid_amount:,.0f} TZS"
                customer.primary_action = "Register receipt" if customer.latest_unpaid_invoice_id else "Renew"
            elif customer.primary_subscription and customer.primary_subscription.paid_through_date:
                paid_through = customer.primary_subscription.paid_through_date
                if paid_through < today:
                    customer.billing_state = "unpaid"
                    customer.billing_label = "Expired"
                    customer.billing_note = f"Paid through {paid_through:%b %d, %Y}"
                    customer.primary_action = "Renew"
                elif paid_through.month == today.month and paid_through.year == today.year:
                    customer.billing_state = "due"
                    customer.billing_label = "Due soon"
                    customer.billing_note = f"Paid through {paid_through:%b %d, %Y}"
                    customer.primary_action = "Renew"
                else:
                    customer.billing_state = "paid"
                    customer.billing_label = "Paid"
                    customer.billing_note = f"Paid through {paid_through:%b %d, %Y}"
                    customer.primary_action = "View"
            elif customer.primary_subscription:
                customer.billing_state = "due"
                customer.billing_label = "Not paid"
                customer.billing_note = "No payment recorded"
                customer.primary_action = "Renew"
            elif not customer.email and not customer.phone:
                customer.billing_state = "incomplete"
                customer.billing_label = "Incomplete"
                customer.billing_note = "Missing contact details"
                customer.primary_action = "Complete profile"
            else:
                customer.billing_state = "neutral"
                customer.billing_label = "No subscription"
                customer.billing_note = "No active package billing"
                customer.primary_action = "View"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = require_organization(self.request)
        context['page_title'] = 'Customer List'
        base = Customer.objects.for_organization(organization)
        self._enrich_customers(context['customers'], organization)

        unpaid_customer_ids = self._unpaid_customer_ids(organization)
        due_soon_customer_ids = self._due_soon_customer_ids(organization)
        no_contact = base.filter(
            (Q(email__isnull=True) | Q(email="")) &
            (Q(phone__isnull=True) | Q(phone=""))
        )
        context['total_customers'] = base.count()
        context['active_customers'] = base.active().count()
        context['inactive_customers'] = base.inactive().count()
        context['suspended_customers'] = base.suspended().count()
        context['overdue_customers'] = base.filter(id__in=unpaid_customer_ids).distinct().count()
        context['due_soon_customers'] = base.filter(id__in=due_soon_customer_ids).distinct().count()
        context['no_contact_customers'] = no_contact.count()
        context['today_customers'] = base.filter(
            Q(id__in=unpaid_customer_ids) |
            Q(id__in=due_soon_customer_ids) |
            ((Q(email__isnull=True) | Q(email="")) & (Q(phone__isnull=True) | Q(phone="")))
        ).distinct().count()
        context['estimated_receivable'] = (
            SubscriptionPeriod.objects.filter(
                organization=organization,
                status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
            ).aggregate(total=Sum("final_amount"))["total"]
            or 0
        )
        query_params = self.request.GET.copy()
        query_params.pop("page", None)
        context["querystring"] = query_params.urlencode()
        context["active_worklist"] = self.request.GET.get("worklist", "")
        context["active_sort"] = getattr(self, "active_sort", self.request.GET.get("sort", "created"))
        if context.get("page_obj"):
            context.update(page_context(self.request, context["page_obj"], page_size=self.get_paginate_by(self.object_list)))
        return context

# --- DetailView example ---
class CustomerDetailView(LoginRequiredMixin, DetailView):
    model = Customer
    template_name = 'customers/customer_detail.html'
    context_object_name = 'customer'

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        return (
            Customer.objects.for_organization(organization)
            .select_related('internet_profile')
            .prefetch_related('packages')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = require_organization(self.request)
        customer = self.get_object()
        context['billing_documents'] = (
            BillingDocument.objects.filter(organization=organization, customer=customer)
            .order_by('-issue_date', '-created_at')[:10]
        )
        context['subscriptions'] = (
            CustomerSubscription.objects.filter(organization=organization, customer=customer)
            .select_related("package", "promotion")
            .prefetch_related("periods__invoice", "periods__receipt")
        )
        context['subscription_periods'] = (
            SubscriptionPeriod.objects.filter(organization=organization, subscription__customer=customer)
            .select_related("subscription", "subscription__package", "invoice", "receipt", "promotion")
            .order_by("-period_start")[:8]
        )
        context['packages'] = customer.packages.all()
        try:
            context['internet_profile'] = customer.internet_profile
        except InternetCustomer.DoesNotExist:
            context['internet_profile'] = None
        return context

# --- CreateView example ---
class CustomerCreateView(LoginRequiredMixin, CreateView):
    model = Customer
    form_class = CustomerForm
    template_name = 'customers/customer_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'internet_form' in kwargs:
            context['internet_form'] = kwargs['internet_form']
            return context

        customer_type = None
        if self.request.method == 'POST':
            customer_type = self.request.POST.get('customer_type') or None
            context['internet_form'] = InternetCustomerForm(self.request.POST, customer_type=customer_type)
        else:
            context['internet_form'] = InternetCustomerForm(customer_type=customer_type)
        return context

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.CUSTOMER_CREATE)
        customer_type = form.cleaned_data.get('customer_type')

        internet_form = InternetCustomerForm(self.request.POST, customer_type=customer_type)
        if customer_type == 'internet' and not internet_form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, internet_form=internet_form))

        try:
            customer_instance = form.save(commit=False)
            internet_customer_instance = None
            if customer_type == "internet":
                internet_customer_instance = internet_form.save(commit=False)

            customer = CustomerService.upsert_customer(
                organization=organization,
                actor=self.request.user,
                customer_instance=customer_instance,
                packages=form.cleaned_data.get("packages"),
                customer_type=customer_type,
                existing_internet_profile=None,
                internet_profile_instance=internet_customer_instance,
                status_change_reason=form.cleaned_data.get("status_change_reason", ""),
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.render_to_response(self.get_context_data(form=form, internet_form=internet_form))

        self.object = customer
        messages.success(self.request, f'Customer {customer.name} created successfully.')
        return redirect(customer.get_absolute_url())

# --- UpdateView example ---
class CustomerUpdateView(LoginRequiredMixin, UpdateView):
    model = Customer
    form_class = CustomerForm
    template_name = 'customers/customer_form.html'

    def get_queryset(self):
        organization = require_organization(self.request)
        return Customer.objects.for_organization(organization)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        customer = self.get_object()
        if customer.customer_type != 'internet':
            if 'packages' in form.fields:
                form.fields['packages'].disabled = True
        return form

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['organization'] = require_organization(self.request)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        customer = self.get_object()

        if 'internet_form' in kwargs:
            context['internet_form'] = kwargs['internet_form']
            return context

        customer_type = customer.customer_type
        if self.request.method == 'POST':
            customer_type = self.request.POST.get('customer_type') or customer.customer_type

        internet_instance = None
        try:
            internet_instance = customer.internet_profile
        except InternetCustomer.DoesNotExist:
            internet_instance = None

        if self.request.method == 'POST':
            context['internet_form'] = InternetCustomerForm(self.request.POST, instance=internet_instance, customer_type=customer_type)
        else:
            context['internet_form'] = InternetCustomerForm(instance=internet_instance, customer_type=customer_type)

        return context

    def form_valid(self, form):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.CUSTOMER_CREATE)
        customer_type = form.cleaned_data.get('customer_type')

        internet_instance = None
        try:
            internet_instance = self.get_object().internet_profile
        except InternetCustomer.DoesNotExist:
            internet_instance = None

        internet_form = InternetCustomerForm(self.request.POST, instance=internet_instance, customer_type=customer_type)
        if customer_type == 'internet' and not internet_form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, internet_form=internet_form))

        try:
            customer_instance = form.save(commit=False)
            internet_customer_instance = None
            if customer_type == "internet":
                internet_customer_instance = internet_form.save(commit=False)

            customer = CustomerService.upsert_customer(
                organization=organization,
                actor=self.request.user,
                customer_instance=customer_instance,
                packages=form.cleaned_data.get("packages"),
                customer_type=customer_type,
                existing_internet_profile=internet_instance,
                internet_profile_instance=internet_customer_instance,
                status_change_reason=form.cleaned_data.get("status_change_reason", ""),
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.render_to_response(self.get_context_data(form=form, internet_form=internet_form))

        self.object = customer
        messages.success(self.request, f'Customer {customer.name} updated successfully.')
        return redirect(customer.get_absolute_url())

# --- DeleteView example ---
class CustomerDeleteView(LoginRequiredMixin, DeleteView):
    model = Customer
    template_name = 'customers/customer_confirm_delete.html'
    success_url = reverse_lazy('customer-list')

    def get_queryset(self):
        organization = require_organization(self.request)
        return Customer.objects.for_organization(organization)

    def post(self, request, *args, **kwargs):
        customer = self.get_object()
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.CUSTOMER_ARCHIVE, obj=customer)
        try:
            CustomerService.soft_delete_customer(
                organization=organization,
                actor=self.request.user,
                customer_id=customer.id,
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return redirect(customer.get_absolute_url())

        messages.success(self.request, f'Customer {customer.name} archived successfully.')
        return redirect(self.success_url)


@login_required
@require_POST
def restore_customer(request, pk: int):
    organization = require_organization(request)
    require_permission(request, PermissionCode.CUSTOMER_ARCHIVE)
    try:
        CustomerService.restore_customer(
            organization=organization,
            actor=request.user,
            customer_id=pk,
        )
    except CustomerServiceError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Customer restored.")
    return redirect(reverse("customer-detail", args=[pk]))


class CustomerAnonymizeView(LoginRequiredMixin, FormView):
    template_name = "customers/customer_confirm_anonymize.html"
    form_class = AnonymizeCustomerForm

    def dispatch(self, request, *args, **kwargs):
        self.organization = require_organization(request)
        self.customer = get_object_or_404(Customer.all_objects, organization=self.organization, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["customer"] = self.customer
        return context

    def form_valid(self, form):
        require_permission(self.request, PermissionCode.CUSTOMER_ARCHIVE, obj=self.customer)
        try:
            CustomerService.anonymize_customer(
                organization=self.organization,
                actor=self.request.user,
                customer_id=self.customer.id,
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)

        messages.success(self.request, "Customer anonymized.")
        return redirect(self.customer.get_absolute_url())


class CustomerHardDeleteView(LoginRequiredMixin, FormView):
    template_name = "customers/customer_confirm_hard_delete.html"

    def dispatch(self, request, *args, **kwargs):
        self.organization = require_organization(request)
        self.customer = get_object_or_404(Customer.all_objects, organization=self.organization, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["customer_id"] = self.customer.id
        return kwargs

    def get_form_class(self):
        return HardDeleteCustomerForm

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["customer"] = self.customer
        return context

    def form_valid(self, form):
        require_permission(self.request, PermissionCode.CUSTOMER_ARCHIVE, obj=self.customer)
        try:
            CustomerService.hard_delete_customer(
                organization=self.organization,
                actor=self.request.user,
                customer_id=self.customer.id,
                confirm_phrase=form.cleaned_data["confirm_phrase"],
                confirm_one=form.cleaned_data["confirm_one"],
                confirm_two=form.cleaned_data["confirm_two"],
            )
        except CustomerServiceError as exc:
            messages.error(self.request, str(exc))
            return self.form_invalid(form)

        messages.success(self.request, "Customer permanently deleted.")
        return redirect(reverse("customer-list"))

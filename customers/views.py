from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import TemplateView, ListView, DetailView, CreateView, UpdateView, DeleteView
from django.views.generic.edit import FormView
from django.urls import reverse_lazy, reverse
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

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
    paginate_by = 10

    def get_queryset(self):
        organization = require_organization(self.request)
        require_permission(self.request, PermissionCode.TENANT_READ)
        queryset = (
            Customer.objects.for_organization(organization)
            .optimized_list()
            .prefetch_related("subscriptions__package", "subscriptions__periods")
        )
        
        # Filter by type
        customer_type = self.request.GET.get('type')
        if customer_type:
            queryset = queryset.filter(customer_type=customer_type)
        
        # Filter by status
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        
        # Search functionality
        search_query = self.request.GET.get('search')
        if search_query:
            queryset = queryset.search(search_query)
        
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        organization = require_organization(self.request)
        context['page_title'] = 'Customer List'
        base = Customer.objects.for_organization(organization)
        context['total_customers'] = base.count()
        context['active_customers'] = base.active().count()
        context['inactive_customers'] = base.inactive().count()
        context['suspended_customers'] = base.suspended().count()
        context['overdue_customers'] = SubscriptionPeriod.objects.filter(
            organization=organization,
            status__in=[SubscriptionPeriod.Status.INVOICED, SubscriptionPeriod.Status.OVERDUE],
        ).values("subscription__customer_id").distinct().count()
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

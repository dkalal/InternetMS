from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Exists, F, Max, OuterRef, Q, Subquery, Sum
from django.http import Http404, HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie

from billing.models import BillingDocument
from billing.services import BillingService, BillingServiceError
from products.models import Product, ProductCategory
from users.permissions import PermissionCode, has_tenant_permission, require_permission
from users.tenancy import require_organization

from .forms import (
    CartForm,
    CartLineForm,
    InventoryImportForm,
    InventorySettingsForm,
    ProductCategoryForm,
    PurchaseForm,
    PurchaseLinesFormSet,
    StockAdjustmentForm,
    SupplierForm,
    SupplierPaymentForm,
)
from .imports import commit_import, template_workbook, validate_workbook, workbook_bytes
from .models import (
    Cart,
    CartLine,
    CartSerialSelection,
    DocumentSerialSelection,
    ImportJob,
    HistoricalInventoryRecord,
    InventoryBalance,
    InventorySale,
    InventorySaleLine,
    InventorySettings,
    Purchase,
    PurchaseLine,
    StockMovement,
    StockUnit,
    Supplier,
)
from .services import CartService, InventoryError, InventoryService, audit, money


def _scope(request, permission):
    organization = require_organization(request)
    require_permission(request, permission)
    return organization


def _cart_workspace(cart):
    """Build the same pricing/totals data for HTML and POS JSON responses."""
    lines = list(cart.lines.select_related('product').prefetch_related('serial_selections__stock_unit'))
    for line in lines:
        _, line.pos_pricing_mode = CartService.line_pricing(
            product=line.product, quantity=line.quantity, customer=cart.customer,
            sale_pricing_category=cart.sale_pricing_category,
        )
    subtotal = sum((line.line_total for line in lines), Decimal('0.00'))
    taxable_subtotal = sum(
        (line.line_total for line in lines if not line.product_id or line.product.tax_eligible),
        Decimal('0.00'),
    )
    _, tax, grand_total = BillingService.compute_totals(
        tax_rate=cart.tax_rate,
        line_items=lines,
        discount_amount=cart.discount_amount,
    )
    return {
        'cart': cart, 'lines': lines, 'subtotal': subtotal,
        'taxable_subtotal': taxable_subtotal, 'tax': tax,
        'grand_total': grand_total,
    }


def _is_pos_request(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest' and 'application/json' in request.headers.get('Accept', '')


def _pos_response(request, cart, *, message='', level='success', status=200):
    workspace = _cart_workspace(cart)
    return JsonResponse({
        'ok': status < 400,
        'message': message,
        'level': level,
        'cart_html': render_to_string('inventory/includes/pos_cart_lines.html', workspace, request=request),
        'checkout_html': render_to_string('inventory/includes/pos_checkout.html', workspace, request=request),
        'subtotal': str(workspace['subtotal']),
        'taxable_subtotal': str(workspace['taxable_subtotal']),
        'discount': str(cart.discount_amount),
        'tax': str(workspace['tax']),
        'tax_rate': str(cart.tax_rate),
        'grand_total': str(workspace['grand_total']),
        'line_count': len(workspace['lines']),
        'lines': [
            {
                'product_id': line.product_id,
                'quantity': str(line.quantity),
                'unit_price': str(line.unit_price),
                'line_total': str(line.line_total),
                'pricing_mode': line.pos_pricing_mode,
            }
            for line in workspace['lines']
        ],
    }, status=status)


@login_required
def dashboard(request):
    organization = _scope(request, PermissionCode.STOCK_VIEW)
    finance_all = has_tenant_permission(
        request.user, organization, PermissionCode.FINANCE_SALES_VIEW_ALL,
        membership=request.membership,
    )
    balances = list(InventoryBalance.objects.filter(tenant=organization).select_related('product'))
    settings_obj, _ = InventorySettings.objects.get_or_create(
        organization=organization, tenant=organization,
        defaults={'walk_in_customer_label': 'Walk-in Customer'},
    )
    cutoff = timezone.now() - timedelta(days=settings_obj.dead_stock_days)
    sold_product_ids = set(StockMovement.objects.filter(
        tenant=organization, movement_type=StockMovement.MovementType.SALE_OUT, created_at__gte=cutoff
    ).values_list('product_id', flat=True))
    fast_cutoff = timezone.now() - timedelta(days=settings_obj.fast_moving_days)
    fast_count = sum(
        1 for row in StockMovement.objects.filter(
            tenant=organization, movement_type=StockMovement.MovementType.SALE_OUT, created_at__gte=fast_cutoff
        ).values('product_id').annotate(units=Sum('quantity'))
        if -row['units'] >= settings_obj.fast_moving_min_units
    )
    stock_products = Product.objects.filter(
        tenant=organization,
        item_type=Product.ItemType.PHYSICAL,
        track_stock=True,
    )
    context = {
        'product_count': Product.objects.filter(tenant=organization).count(),
        'total_stock_units': sum((balance.quantity for balance in balances), Decimal('0.00')),
        'low_stock_count': stock_products.filter(
            quantity__gt=0, quantity__lte=F('reorder_threshold')
        ).count(),
        'out_of_stock_count': stock_products.filter(quantity__lte=0).count(),
        'dead_stock_count': sum(
            1 for balance in balances if balance.quantity > 0 and balance.product_id not in sold_product_ids
        ),
        'fast_moving_count': fast_count,
        'draft_cart_count': Cart.objects.filter(tenant=organization, status=Cart.Status.DRAFT).count(),
    }
    if finance_all:
        context['total_selling_value'] = sum(
            (balance.quantity * balance.product.selling_price for balance in balances), Decimal('0.00')
        )
        context['total_technician_value'] = sum(
            (balance.quantity * balance.product.effective_technician_price for balance in balances), Decimal('0.00')
        )
        context['total_wholesale_value'] = sum(
            (
                balance.quantity * balance.product.price_for_sale_category(
                    sale_pricing_category=Product.PricingMode.WHOLESALE,
                    quantity=balance.quantity,
                )
                for balance in balances
            ),
            Decimal('0.00'),
        )
        context['draft_purchase_count'] = Purchase.objects.filter(tenant=organization, status=Purchase.Status.DRAFT).count()
        context['recent_movements'] = StockMovement.objects.filter(tenant=organization).select_related('product', 'created_by')[:10]
        context['recent_purchases'] = Purchase.objects.filter(tenant=organization).select_related(
            'supplier', 'created_by'
        ).annotate(item_count=Count('lines'))[:5]
    if has_tenant_permission(request.user, organization, PermissionCode.COST_REPORT_VIEW, membership=request.membership):
        context['total_stock_value'] = sum((balance.total_value for balance in balances), Decimal('0.00'))
    return render(request, 'inventory/dashboard.html', context)


@login_required
def category_list(request):
    organization = _scope(request, PermissionCode.PRODUCT_VIEW)
    query = request.GET.get('q', '').strip()
    categories = ProductCategory.objects.filter(tenant=organization).annotate(
        product_count=Count('products'),
    )
    if query:
        categories = categories.filter(Q(name__icontains=query) | Q(description__icontains=query))
    categories = categories.order_by('name')
    category_stats = {
        'total': categories.count(),
        'active': categories.filter(is_active=True).count(),
        'products': sum(category.product_count for category in categories),
    }
    return render(request, 'inventory/category_list.html', {
        'categories': categories, 'query': query, 'category_stats': category_stats,
    })


@login_required
def category_form(request, pk=None):
    organization = _scope(request, PermissionCode.CATEGORY_MANAGE)
    category = get_object_or_404(ProductCategory.objects.filter(tenant=organization), pk=pk) if pk else None
    old = {
        'name': category.name, 'description': category.description, 'measure_unit': category.measure_unit,
        'icon': category.icon, 'is_active': category.is_active,
    } if category else {}
    form = ProductCategoryForm(request.POST or None, instance=category, organization=organization)
    if request.method == 'POST' and form.is_valid():
        form.instance.organization = form.instance.tenant = organization
        obj = form.save()
        audit(organization=organization, actor=request.user, action='inventory.category.saved', obj=obj, old_value=old, new_value={
            'name': obj.name, 'description': obj.description, 'measure_unit': obj.measure_unit,
            'icon': obj.icon, 'is_active': obj.is_active,
        })
        messages.success(request, 'Category saved.')
        return redirect('inventory:category_list')
    return render(request, 'inventory/category_form.html', {
        'form': form, 'category': category, 'title': 'Edit category' if category else 'Add category',
        'cancel_url': 'inventory:category_list', 'submit_label': 'Save category',
    })


@login_required
def supplier_list(request):
    organization = _scope(request, PermissionCode.SUPPLIER_MANAGE)
    query = request.GET.get('q', '').strip()
    suppliers = Supplier.objects.filter(tenant=organization).annotate(
        confirmed_purchase_count=Count('purchases', filter=Q(purchases__status=Purchase.Status.CONFIRMED)),
        last_purchase_date=Max('purchases__purchase_date', filter=Q(purchases__status=Purchase.Status.CONFIRMED)),
    )
    if query:
        suppliers = suppliers.filter(
            Q(company_name__icontains=query)
            | Q(contact_person__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
            | Q(tin_vrn__icontains=query)
        )
    return render(request, 'inventory/supplier_list.html', {'suppliers': suppliers, 'query': query})


@login_required
def supplier_form(request, pk=None):
    organization = _scope(request, PermissionCode.SUPPLIER_MANAGE)
    supplier = get_object_or_404(Supplier.objects.filter(tenant=organization), pk=pk) if pk else None
    old = {'company_name': supplier.company_name, 'is_active': supplier.is_active} if supplier else {}
    form = SupplierForm(request.POST or None, instance=supplier, organization=organization)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.organization = obj.tenant = organization
        if obj.created_by_id is None:
            obj.created_by = request.user
        obj.save()
        audit(organization=organization, actor=request.user, action='inventory.supplier.saved', obj=obj, old_value=old, new_value={'company_name': obj.company_name, 'is_active': obj.is_active})
        messages.success(request, 'Supplier saved.')
        return redirect('inventory:supplier_list')
    return render(request, 'inventory/supplier_form.html', {
        'form': form, 'supplier': supplier, 'title': 'Edit supplier' if supplier else 'New supplier',
    })


@login_required
def supplier_detail(request, pk):
    organization = _scope(request, PermissionCode.SUPPLIER_MANAGE)
    supplier = get_object_or_404(Supplier.objects.filter(tenant=organization), pk=pk)
    return render(request, 'inventory/supplier_detail.html', {'supplier': supplier})


@login_required
def supplier_payment_create(request, pk):
    organization = _scope(request, PermissionCode.SUPPLIER_MANAGE)
    supplier = get_object_or_404(Supplier.objects.filter(tenant=organization), pk=pk)
    form = SupplierPaymentForm(request.POST or None, organization=organization)
    if request.method == 'POST' and form.is_valid():
        payment = form.save(commit=False)
        payment.organization = payment.tenant = organization
        payment.supplier = supplier
        payment.created_by = request.user
        payment.save()
        audit(organization=organization, actor=request.user, action='inventory.supplier_payment.recorded', obj=payment, new_value={'supplier_id': supplier.pk, 'amount': str(payment.amount)})
        messages.success(request, 'Supplier payment record saved. This does not post to a general ledger.')
        return redirect('inventory:supplier_detail', pk=supplier.pk)
    return render(request, 'inventory/form.html', {
        'form': form, 'title': f'Record payment — {supplier.company_name}',
        'subtitle': 'This is an operational supplier record and does not post to a general ledger.',
        'supplier': supplier, 'submit_label': 'Record payment',
    })


@login_required
def purchase_list(request):
    organization = _scope(request, PermissionCode.PURCHASE_VIEW)
    purchases = Purchase.objects.filter(tenant=organization).select_related(
        'supplier', 'created_by', 'confirmed_by'
    ).annotate(item_count=Count('lines'))
    status = request.GET.get('status')
    if status in Purchase.Status.values:
        purchases = purchases.filter(status=status)
    query = request.GET.get('q', '').strip()
    if query:
        purchases = purchases.filter(
            Q(reference_number__icontains=query)
            | Q(supplier__company_name__icontains=query)
            | Q(created_by__username__icontains=query)
        )
    return render(request, 'inventory/purchase_list.html', {
        'purchases': purchases, 'statuses': Purchase.Status.choices, 'status': status or '', 'query': query,
    })


@login_required
def purchase_create(request):
    organization = _scope(request, PermissionCode.PURCHASE_CONFIRM)
    purchase = Purchase(organization=organization, tenant=organization, created_by=request.user)
    form = PurchaseForm(request.POST or None, instance=purchase, organization=organization)
    formset = PurchaseLinesFormSet(request.POST or None, instance=purchase, organization=organization)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            purchase = form.save(commit=False)
            purchase.organization = purchase.tenant = organization
            purchase.created_by = request.user
            purchase.save()
            formset.instance = purchase
            formset.save()
            # Keep the persisted draft total aligned with its authoritative
            # lines. Confirmation repeats this calculation under a lock.
            total = sum((line.line_total for line in purchase.lines.all()), Decimal('0.00'))
            Purchase.objects.filter(pk=purchase.pk).update(total_cost=total.quantize(Decimal('0.01')))
            audit(organization=organization, actor=request.user, action='inventory.purchase.created', obj=purchase, new_value={'reference_number': purchase.reference_number})
        messages.success(request, 'Purchase draft saved. Confirm it after reviewing all lines.')
        return redirect('inventory:purchase_detail', pk=purchase.pk)
    product_meta = {
        str(product.pk): {
            'serialized': product.is_serialized,
            'expiry': product.track_expiry,
            'sku': product.sku,
        }
        for product in Product.objects.filter(
            tenant=organization, is_active=True, item_type=Product.ItemType.PHYSICAL, track_stock=True
        ).order_by('name')
    }
    return render(request, 'inventory/purchase_form.html', {
        'form': form, 'formset': formset, 'title': 'Receive stock', 'product_meta': product_meta,
    })


@login_required
def purchase_edit(request, pk):
    organization = _scope(request, PermissionCode.PURCHASE_CONFIRM)
    purchase = get_object_or_404(Purchase.objects.filter(tenant=organization), pk=pk)
    if purchase.status != Purchase.Status.DRAFT:
        messages.error(request, 'Only draft purchases can be edited.')
        return redirect('inventory:purchase_detail', pk=purchase.pk)
    form = PurchaseForm(request.POST or None, instance=purchase, organization=organization)
    formset = PurchaseLinesFormSet(request.POST or None, instance=purchase, organization=organization)
    if request.method == 'POST' and form.is_valid() and formset.is_valid():
        with transaction.atomic():
            purchase = form.save(commit=False)
            purchase.organization = purchase.tenant = organization
            purchase.save()
            formset.instance = purchase
            formset.save()
            total = sum((line.line_total for line in purchase.lines.all()), Decimal('0.00'))
            Purchase.objects.filter(pk=purchase.pk).update(total_cost=total.quantize(Decimal('0.01')))
            audit(organization=organization, actor=request.user, action='inventory.purchase.updated', obj=purchase)
        messages.success(request, 'Purchase draft updated.')
        return redirect('inventory:purchase_detail', pk=purchase.pk)
    product_meta = {str(product.pk): {'serialized': product.is_serialized, 'expiry': product.track_expiry, 'sku': product.sku}
                    for product in Product.objects.filter(tenant=organization, is_active=True, item_type=Product.ItemType.PHYSICAL, track_stock=True).order_by('name')}
    return render(request, 'inventory/purchase_form.html', {'form': form, 'formset': formset, 'title': 'Edit purchase draft', 'product_meta': product_meta})


@login_required
def purchase_cancel(request, pk):
    organization = _scope(request, PermissionCode.PURCHASE_CONFIRM)
    if request.method != 'POST':
        raise Http404
    try:
        purchase = InventoryService.cancel_purchase(organization=organization, purchase_id=pk, actor=request.user)
    except InventoryError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f'Purchase {purchase.reference_number} cancelled. Stock was not changed.')
    return redirect('inventory:purchase_detail', pk=pk)


@login_required
@ensure_csrf_cookie
@never_cache
def purchase_detail(request, pk):
    organization = _scope(request, PermissionCode.PURCHASE_VIEW)
    purchase = get_object_or_404(Purchase.objects.filter(tenant=organization).select_related('supplier', 'created_by', 'confirmed_by'), pk=pk)
    # Lines are the source of truth for a draft receipt. This also renders a
    # correct total for historical drafts that predate draft-total syncing.
    purchase_lines = list(purchase.lines.select_related('product'))
    line_items_total = sum((line.line_total for line in purchase_lines), Decimal('0.00'))
    return render(request, 'inventory/purchase_detail.html', {
        'purchase': purchase,
        'purchase_lines': purchase_lines,
        'line_items_total': line_items_total,
    })


@login_required
def purchase_confirm(request, pk):
    organization = _scope(request, PermissionCode.PURCHASE_CONFIRM)
    if request.method != 'POST':
        raise Http404
    try:
        purchase = InventoryService.confirm_purchase(organization=organization, purchase_id=pk, actor=request.user)
        messages.success(request, f'Purchase {purchase.reference_number} confirmed and stock received.')
    except (InventoryError, BillingServiceError) as exc:
        messages.error(request, str(exc))
    return redirect('inventory:purchase_detail', pk=pk)


@login_required
def stock_list(request):
    organization = _scope(request, PermissionCode.STOCK_VIEW)
    last_movement = StockMovement.objects.filter(
        tenant=organization, product_id=OuterRef('product_id')
    ).order_by('-created_at').values('created_at')[:1]
    balances = InventoryBalance.objects.filter(tenant=organization).select_related(
        'product', 'product__catalog_category'
    ).annotate(last_movement_at=Subquery(last_movement))
    query = request.GET.get('q', '').strip()
    state = request.GET.get('state', '')
    if query:
        balances = balances.filter(Q(product__name__icontains=query) | Q(product__sku__icontains=query))
    if state == 'low':
        balances = balances.filter(quantity__gt=0, quantity__lte=F('product__reorder_threshold'))
    elif state == 'out':
        balances = balances.filter(quantity=0)
    elif state == 'serialized':
        balances = balances.filter(product__is_serialized=True)
    elif state == 'expiring':
        cutoff = timezone.now().date() + timedelta(days=30)
        balances = balances.filter(
            product__track_expiry=True,
            product__stock_units__status=StockUnit.Status.AVAILABLE,
            product__stock_units__expiry_date__lte=cutoff,
        ).distinct()
    return render(request, 'inventory/stock_list.html', {
        'balances': balances.order_by('product__name'), 'query': query, 'state': state,
    })


@login_required
def movement_list(request):
    organization = _scope(request, PermissionCode.STOCK_MOVEMENT_VIEW)
    movements = StockMovement.objects.filter(tenant=organization).select_related(
        'product', 'created_by', 'purchase_line__purchase', 'billing_line__document', 'adjustment'
    )
    product_id = request.GET.get('product')
    if product_id:
        movements = movements.filter(product_id=product_id)
    query = request.GET.get('q', '').strip()
    movement_type = request.GET.get('type', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    if query:
        movements = movements.filter(Q(product__name__icontains=query) | Q(product__sku__icontains=query))
    if movement_type in StockMovement.MovementType.values:
        movements = movements.filter(movement_type=movement_type)
    if date_from:
        movements = movements.filter(created_at__date__gte=date_from)
    if date_to:
        movements = movements.filter(created_at__date__lte=date_to)
    page_obj = Paginator(movements, 50).get_page(request.GET.get('page'))
    return render(request, 'inventory/movement_list.html', {
        'movements': page_obj.object_list,
        'page_obj': page_obj,
        'products': Product.objects.filter(tenant=organization, track_stock=True).order_by('name'),
        'movement_types': StockMovement.MovementType.choices,
        'product_id': str(product_id or ''),
        'query': query,
        'movement_type': movement_type,
        'date_from': date_from,
        'date_to': date_to,
    })


@login_required
def stock_adjust(request):
    organization = _scope(request, PermissionCode.STOCK_ADJUST)
    initial = {'product': request.GET.get('product')} if request.method == 'GET' and request.GET.get('product') else None
    form = StockAdjustmentForm(request.POST or None, organization=organization, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            InventoryService.adjust_stock(
                organization=organization,
                product_id=form.cleaned_data['product'].pk,
                quantity_delta=form.cleaned_data['quantity_delta'],
                reason=form.cleaned_data['reason'],
                notes=form.cleaned_data['notes'],
                serial_numbers=form.serial_list(),
                actor=request.user,
            )
            messages.success(request, 'Stock adjustment recorded.')
            return redirect('inventory:stock_list')
        except InventoryError as exc:
            form.add_error(None, str(exc))
    products = list(form.fields['product'].queryset.select_related('inventory_balance'))
    stock_levels = {str(product.pk): str(product.available_stock) for product in products}
    return render(request, 'inventory/stock_adjust.html', {
        'form': form, 'title': 'Stock adjustment', 'stock_levels': stock_levels,
    })


@login_required
def cart_list(request):
    organization = _scope(request, PermissionCode.CART_MANAGE)
    carts = Cart.objects.filter(tenant=organization).select_related(
        'customer', 'created_by', 'quotation', 'invoice'
    ).annotate(item_count=Count('lines'))
    status = request.GET.get('status', '').strip()
    query = request.GET.get('q', '').strip()
    if status in Cart.Status.values:
        carts = carts.filter(status=status)
    if query:
        cart_search = Q(customer__name__icontains=query) | Q(walk_in_name__icontains=query)
        if query.isdigit():
            cart_search |= Q(pk=int(query))
        carts = carts.filter(cart_search)
    return render(request, 'inventory/cart_list.html', {
        'carts': carts, 'statuses': Cart.Status.choices, 'status': status, 'query': query,
    })


@login_required
def cart_create(request):
    organization = _scope(request, PermissionCode.CART_MANAGE)
    if request.method != 'POST':
        raise Http404

    # A sale must start as an editable draft, but a navigation request must
    # never create one. Reuse only the operator's untouched draft so a double
    # click or refresh cannot leave duplicate empty carts behind.
    with transaction.atomic():
        cart = Cart.objects.select_for_update(of=('self',)).filter(
            tenant=organization,
            created_by=request.user,
            status=Cart.Status.DRAFT,
            customer__isnull=True,
            walk_in_name='',
            discount_amount=Decimal('0.00'),
            tax_rate=Decimal('0.00'),
            notes='',
            lines__isnull=True,
        ).order_by('-updated_at', '-id').first()
        if cart is None:
            cart = Cart.objects.create(
                organization=organization, tenant=organization, created_by=request.user,
            )
            audit(organization=organization, actor=request.user, action='inventory.cart.created', obj=cart)
    return redirect('inventory:cart_detail', pk=cart.pk)


@login_required
@ensure_csrf_cookie
def cart_detail(request, pk):
    organization = _scope(request, PermissionCode.CART_MANAGE)
    cart = get_object_or_404(Cart.objects.filter(tenant=organization).select_related('customer', 'quotation', 'invoice'), pk=pk)
    cart_form = CartForm(request.POST or None, instance=cart, organization=organization)
    if request.method == 'POST':
        if cart.status != Cart.Status.DRAFT:
            messages.error(request, 'Only draft carts can be edited.')
            return redirect('inventory:cart_detail', pk=cart.pk)
        if cart_form.is_valid():
            try:
                with transaction.atomic():
                    cart_form.save()
                    CartService.refresh_cart_prices(cart=cart)
                    # Validate the persisted, repriced cart before committing.
                    _cart_workspace(cart)
            except BillingServiceError as exc:
                cart.refresh_from_db()
                cart_form.add_error('discount_amount', str(exc))
            else:
                if _is_pos_request(request):
                    return _pos_response(request, cart, message='Sale details saved.')
                messages.success(request, 'Cart draft saved. Stock remains unchanged.')
                return redirect('inventory:cart_detail', pk=cart.pk)
        if _is_pos_request(request):
            return JsonResponse({
                'ok': False,
                'message': 'Check the highlighted sale details and try again.',
                'errors': cart_form.errors.get_json_data(),
            }, status=422)
    workspace = _cart_workspace(cart)
    lines = workspace['lines']
    query = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '').strip()
    existing_line = CartLine.objects.filter(cart=cart, product_id=OuterRef('pk'))
    catalog = Product.objects.filter(tenant=organization, is_active=True).select_related(
        'catalog_category', 'inventory_balance'
    ).annotate(
        in_cart=Exists(existing_line),
        cart_quantity=Subquery(existing_line.values('quantity')[:1]),
    ).order_by('name')
    if query:
        catalog = catalog.filter(Q(name__icontains=query) | Q(sku__icontains=query) | Q(brand__icontains=query) | Q(model_number__icontains=query))
    if category_id.isdigit():
        catalog = catalog.filter(catalog_category_id=category_id)
    catalog = list(catalog[:50])
    for product in catalog:
        product.pos_price, product.pos_pricing_mode = CartService.line_pricing(
            product=product, quantity=Decimal('1.00'), customer=cart.customer,
            sale_pricing_category=cart.sale_pricing_category,
        )
    return render(request, 'inventory/cart_detail.html', {
        'cart': cart, 'cart_form': cart_form, **workspace,
        'catalog': catalog, 'query': query, 'category_id': category_id,
        'categories': ProductCategory.objects.filter(tenant=organization, is_active=True).order_by('name'),
        'can_register_payment': has_tenant_permission(request.user, organization, PermissionCode.PAYMENT_REGISTER, membership=request.membership),
    })


@login_required
def cart_line_form(request, cart_pk, line_pk=None):
    organization = _scope(request, PermissionCode.CART_MANAGE)
    cart = get_object_or_404(Cart.objects.filter(tenant=organization, status=Cart.Status.DRAFT), pk=cart_pk)
    line = get_object_or_404(CartLine, pk=line_pk, cart=cart) if line_pk else None
    initial = {'product': request.GET.get('product')} if not line and request.GET.get('product') else None
    form = CartLineForm(
        request.POST or None, instance=line, organization=organization, initial=initial,
        sale_pricing_category=cart.sale_pricing_category,
    )
    selected_product_id = request.POST.get('product') if request.method == 'POST' else (
        request.GET.get('product') or (line.product_id if line else None)
    )
    selected_product = Product.objects.filter(tenant=organization, pk=selected_product_id).first() if selected_product_id else None
    if request.method == 'POST' and form.is_valid():
        unit_price, _ = CartService.line_pricing(
            product=form.cleaned_data['product'], quantity=form.cleaned_data['quantity'], customer=cart.customer,
            sale_pricing_category=cart.sale_pricing_category,
        )
        if form.cleaned_data['discount_amount'] > form.cleaned_data['quantity'] * unit_price:
            form.add_error('discount_amount', 'Discount cannot exceed the effective sale price.')
            return render(request, 'inventory/cart_line_form.html', {
                'form': form, 'cart': cart, 'line': line, 'selected_product': selected_product,
                'title': 'Edit cart item' if line else 'Add cart item',
            })
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.cart = cart
            obj.unit_price = unit_price
            obj.save()
            CartSerialSelection.objects.filter(cart_line=obj).delete()
            CartSerialSelection.objects.bulk_create([
                CartSerialSelection(tenant=organization, cart_line=obj, stock_unit=unit) for unit in form.cleaned_data.get('serial_units', [])
            ])
        messages.success(request, 'Cart item saved. Stock remains unchanged until full payment.')
        return redirect('inventory:cart_detail', pk=cart.pk)
    return render(request, 'inventory/cart_line_form.html', {
        'form': form, 'cart': cart, 'line': line, 'selected_product': selected_product,
        'title': 'Edit cart item' if line else 'Add cart item',
    })


@login_required
def cart_line_adjust(request, cart_pk):
    """Fast POS quantity controls for standard, non-serialized catalog items."""
    organization = _scope(request, PermissionCode.CART_MANAGE)
    if request.method != 'POST':
        raise Http404
    product_id = request.POST.get('product', '')
    direction = request.POST.get('direction', 'add')
    if direction not in {'add', 'increase', 'decrease'} or not product_id.isdigit():
        raise Http404

    with transaction.atomic():
        cart = get_object_or_404(
            Cart.objects.select_for_update().filter(tenant=organization, status=Cart.Status.DRAFT), pk=cart_pk
        )
        product = get_object_or_404(Product.objects.select_for_update().filter(tenant=organization, is_active=True), pk=product_id)
        if product.is_serialized:
            if _is_pos_request(request):
                return JsonResponse({
                    'ok': False, 'message': 'This item needs serial-number selection before it can be added.',
                    'redirect_url': f'/inventory/carts/{cart.pk}/items/new/?product={product.pk}',
                }, status=422)
            messages.info(request, 'Select serial numbers before adding this serialized product.')
            return redirect('inventory:cart_line_create', cart_pk=cart.pk)
        line = CartLine.objects.select_for_update().filter(cart=cart, product=product).first()
        if direction == 'decrease':
            if line is None:
                return redirect('inventory:cart_detail', pk=cart.pk)
            if line.quantity <= Decimal('1.00'):
                line.delete()
            else:
                line.quantity -= Decimal('1.00')
                line.unit_price, _ = CartService.line_pricing(product=product, quantity=line.quantity, customer=cart.customer, sale_pricing_category=cart.sale_pricing_category)
                line.save(update_fields=['quantity', 'unit_price', 'updated_at'])
        else:
            quantity = (line.quantity if line else Decimal('0.00')) + Decimal('1.00')
            if product.item_type == Product.ItemType.PHYSICAL and product.track_stock and quantity > product.available_stock:
                if _is_pos_request(request):
                    return _pos_response(request, cart, message=f'Only {product.available_stock} {product.measure_unit} of {product.name} are available.', level='warning', status=409)
                messages.warning(request, f'Only {product.available_stock} {product.measure_unit} of {product.name} are available.')
            elif line:
                line.quantity = quantity
                line.unit_price, _ = CartService.line_pricing(product=product, quantity=quantity, customer=cart.customer, sale_pricing_category=cart.sale_pricing_category)
                line.save(update_fields=['quantity', 'unit_price', 'updated_at'])
            else:
                unit_price, _ = CartService.line_pricing(product=product, quantity=quantity, customer=cart.customer, sale_pricing_category=cart.sale_pricing_category)
                CartLine.objects.create(cart=cart, product=product, quantity=quantity, unit_price=unit_price)
    if _is_pos_request(request):
        return _pos_response(request, cart)
    return redirect('inventory:cart_detail', pk=cart.pk)


@login_required
def cart_line_delete(request, cart_pk, line_pk):
    organization = _scope(request, PermissionCode.CART_MANAGE)
    if request.method != 'POST':
        raise Http404
    cart = get_object_or_404(Cart.objects.filter(tenant=organization, status=Cart.Status.DRAFT), pk=cart_pk)
    get_object_or_404(CartLine, pk=line_pk, cart=cart).delete()
    return redirect('inventory:cart_detail', pk=cart.pk)


@login_required
def cart_abandon(request, pk):
    organization = _scope(request, PermissionCode.CART_MANAGE)
    if request.method != 'POST':
        raise Http404
    get_object_or_404(Cart.objects.filter(tenant=organization), pk=pk)
    try:
        _cart, changed = CartService.abandon(
            organization=organization,
            cart_id=pk,
            actor=request.user,
        )
    except InventoryError as exc:
        messages.error(request, str(exc))
    else:
        if changed:
            messages.success(request, 'Sale discarded. Its audit record was preserved and no stock was changed.')
        else:
            messages.info(request, 'This sale was already discarded.')
    return redirect('inventory:cart_list')


@login_required
def cart_convert(request, pk, target):
    organization = _scope(request, PermissionCode.CART_MANAGE)
    if request.method != 'POST':
        raise Http404
    permission = PermissionCode.BILLING_CREATE
    require_permission(request, permission)
    try:
        document = CartService.convert(organization=organization, cart_id=pk, target=target, actor=request.user)
        messages.success(request, f'Cart converted to {target} {document.number}.')
        if target == BillingDocument.DocumentType.INVOICE and has_tenant_permission(request.user, organization, PermissionCode.PAYMENT_REGISTER, membership=request.membership):
            return redirect('billing:create_receipt_from_invoice', pk=document.pk)
        return redirect('billing:document_detail', doc_type=target, pk=document.pk)
    except (InventoryError, BillingServiceError) as exc:
        messages.error(request, str(exc))
        return redirect('inventory:cart_detail', pk=pk)


REPORT_NAMES = {
    'stock-valuation': 'Stock valuation', 'purchases': 'Purchase report', 'sales': 'Sales report',
    'gross-profit': 'Gross profit', 'best-selling': 'Best-selling products', 'fast-moving': 'Fast-moving products',
    'slow-moving': 'Slow-moving products', 'dead-stock': 'Dead stock', 'low-stock': 'Low-stock products',
    'out-of-stock': 'Out-of-stock products', 'movements': 'Stock movements', 'serialized': 'Serialized stock history',
    'historical-purchases': 'Historical purchases (record only)',
    'historical-sales': 'Historical sales (record only)',
}
COST_REPORT_NAMES = {'stock-valuation', 'purchases', 'gross-profit', 'movements', 'historical-purchases'}

REPORT_DESCRIPTIONS = {
    'stock-valuation': 'Current movement-backed quantity and recorded acquisition value.',
    'purchases': 'Confirmed stock receipts in the selected period.',
    'sales': 'Fully paid inventory sales with stock already deducted.',
    'gross-profit': 'Operational gross profit: net item revenue less recorded stock cost, not accounting net profit.',
    'best-selling': 'Products ranked by units sold in the configured fast-moving period.',
    'fast-moving': 'Products meeting the configured high-sales threshold.',
    'slow-moving': 'Products at or below the configured low-sales threshold.',
    'dead-stock': 'Stock on hand with no sale movement during the configured dead-stock period.',
    'low-stock': 'Products at or below their reorder threshold but still available.',
    'out-of-stock': 'Stock-tracked products with no available units.',
    'movements': 'Immutable stock in, sale, adjustment, and opening-balance records.',
    'serialized': 'Lifecycle history for individually tracked serial numbers.',
    'historical-purchases': 'Imported reference-only purchase history; it does not affect live stock.',
    'historical-sales': 'Imported reference-only sales history; it does not affect live stock.',
}


def _report_rows(organization, report_name):
    settings_obj, _ = InventorySettings.objects.get_or_create(organization=organization, tenant=organization)
    if report_name in {'stock-valuation', 'low-stock', 'out-of-stock', 'dead-stock'}:
        balances = InventoryBalance.objects.filter(tenant=organization).select_related('product')
        if report_name == 'low-stock':
            balances = balances.filter(quantity__gt=0, quantity__lte=F('product__reorder_threshold'))
        elif report_name == 'out-of-stock':
            balances = balances.filter(quantity=0)
        elif report_name == 'dead-stock':
            cutoff = timezone.now() - timedelta(days=settings_obj.dead_stock_days)
            sold = StockMovement.objects.filter(tenant=organization, movement_type='sale_out', created_at__gte=cutoff).values('product_id')
            balances = balances.filter(quantity__gt=0).exclude(product_id__in=sold)
        if report_name == 'stock-valuation':
            return ['SKU', 'Product', 'Quantity', 'Average cost', 'Value', 'Threshold'], [
                [b.product.sku, b.product.name, b.quantity, b.average_cost, money(b.quantity * b.average_cost), b.product.reorder_threshold] for b in balances
            ]
        return ['SKU', 'Product', 'Quantity', 'Threshold'], [
            [b.product.sku, b.product.name, b.quantity, b.product.reorder_threshold] for b in balances
        ]
    if report_name == 'purchases':
        lines = PurchaseLine.objects.filter(purchase__tenant=organization, purchase__status=Purchase.Status.CONFIRMED).select_related('purchase__supplier', 'product')
        return ['Date', 'Reference', 'Supplier', 'SKU', 'Product', 'Quantity', 'Unit cost', 'Total'], [
            [l.purchase.purchase_date, l.purchase.reference_number, l.purchase.supplier.company_name, l.product.sku, l.product.name, l.quantity, l.unit_cost, l.line_total] for l in lines
        ]
    if report_name in {'sales', 'gross-profit'}:
        lines = InventorySaleLine.objects.filter(sale__tenant=organization, sale__stock_deducted=True).select_related('sale__invoice', 'billing_line__product')
        if report_name == 'sales':
            return ['Date', 'Invoice', 'SKU', 'Item', 'Quantity', 'Net revenue'], [
                [l.sale.completed_at.date(), l.sale.invoice.number, l.billing_line.product.sku, l.billing_line.product.name, l.billing_line.quantity, l.net_revenue]
                for l in lines
            ]
        return ['Date', 'Invoice', 'SKU', 'Item', 'Quantity', 'Net revenue', 'Cost', 'Gross profit'], [
            [l.sale.completed_at.date(), l.sale.invoice.number, l.billing_line.product.sku, l.billing_line.product.name, l.billing_line.quantity, l.net_revenue, l.cost_total, l.gross_profit]
            for l in lines
        ]
    if report_name == 'movements':
        movements = StockMovement.objects.filter(tenant=organization).select_related('product')
        return ['Date', 'SKU', 'Product', 'Type', 'Quantity', 'Balance after', 'Unit cost'], [[m.created_at, m.product.sku, m.product.name, m.get_movement_type_display(), m.quantity, m.balance_after, m.unit_cost] for m in movements]
    if report_name == 'serialized':
        units = StockUnit.objects.filter(tenant=organization).select_related('product', 'sold_billing_line__document')
        return ['SKU', 'Product', 'Serial', 'Status', 'Batch', 'Expiry', 'Invoice'], [[u.product.sku, u.product.name, u.serial_number, u.get_status_display(), u.batch_reference, u.expiry_date, u.sold_billing_line.document.number if u.sold_billing_line_id else ''] for u in units]
    if report_name in {'historical-purchases', 'historical-sales'}:
        record_type = HistoricalInventoryRecord.RecordType.PURCHASE if report_name == 'historical-purchases' else HistoricalInventoryRecord.RecordType.SALE
        records = HistoricalInventoryRecord.objects.filter(tenant=organization, record_type=record_type)
        return ['Date', 'Reference', 'SKU', 'Description', 'Quantity', 'Unit amount', 'Total', 'Notes'], [
            [r.record_date, r.reference, r.sku, r.description, r.quantity, r.unit_amount, r.total_amount, r.notes] for r in records
        ]
    days = settings_obj.fast_moving_days if report_name in {'best-selling', 'fast-moving'} else settings_obj.slow_moving_days
    cutoff = timezone.now() - timedelta(days=days)
    totals = (
        StockMovement.objects.filter(tenant=organization, movement_type='sale_out', created_at__gte=cutoff)
        .values('product__sku', 'product__name').annotate(raw_units=Sum('quantity')).order_by('raw_units')
    )
    rows = [[row['product__sku'], row['product__name'], -row['raw_units'], days] for row in totals]
    if report_name == 'fast-moving':
        rows = [row for row in rows if row[2] >= settings_obj.fast_moving_min_units]
    elif report_name == 'slow-moving':
        rows = [row for row in rows if row[2] <= settings_obj.slow_moving_max_units]
    return ['SKU', 'Product', 'Units sold', 'Period days'], rows


@login_required
def report(request, report_name='stock-valuation'):
    if report_name not in REPORT_NAMES:
        raise Http404
    sensitive = report_name in COST_REPORT_NAMES
    organization = _scope(request, PermissionCode.COST_REPORT_VIEW if sensitive else PermissionCode.SALES_REPORT_VIEW)
    headers, rows = _report_rows(organization, report_name)
    search = request.GET.get('q', '').strip().lower()
    if search:
        rows = [row for row in rows if any(search in str(value or '').lower() for value in row)]
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    if report_name in {'purchases', 'sales', 'gross-profit', 'movements', 'historical-purchases', 'historical-sales'}:
        if date_from:
            rows = [row for row in rows if str(row[0])[:10] >= date_from]
        if date_to:
            rows = [row for row in rows if str(row[0])[:10] <= date_to]
    if request.GET.get('export') == 'xlsx':
        require_permission(request, PermissionCode.INVENTORY_EXPORT)
        payload = workbook_bytes(headers, rows, title=REPORT_NAMES[report_name])
        response = HttpResponse(payload, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="{report_name}.xlsx"'
        return response
    page_obj = Paginator(rows, 50).get_page(request.GET.get('page'))
    return render(request, 'inventory/report.html', {
        'report_name': report_name,
        'title': REPORT_NAMES[report_name],
        'report_names': (
            REPORT_NAMES
            if has_tenant_permission(request.user, organization, PermissionCode.COST_REPORT_VIEW, membership=request.membership)
            else {slug: label for slug, label in REPORT_NAMES.items() if slug not in COST_REPORT_NAMES}
        ),
        'description': REPORT_DESCRIPTIONS[report_name], 'result_count': len(rows),
        'headers': headers, 'rows': page_obj.object_list, 'page_obj': page_obj,
        'query': request.GET.get('q', ''), 'date_from': date_from, 'date_to': date_to,
    })


@login_required
def export_records(request, record_type):
    organization = _scope(request, PermissionCode.INVENTORY_EXPORT)
    if record_type == 'products':
        headers = ['SKU', 'Name', 'Type', 'Category', 'Brand', 'Model', 'Selling price', 'Technician price', 'Wholesale price', 'Active']
        rows = [[
            p.sku, p.name, p.get_item_type_display(),
            p.catalog_category.name if p.catalog_category_id else p.get_category_display(),
            p.brand, p.model_number, p.selling_price, p.technician_price, p.wholesale_price, p.is_active,
        ] for p in Product.objects.filter(tenant=organization).select_related('catalog_category').order_by('name')]
    elif record_type == 'suppliers':
        require_permission(request, PermissionCode.SUPPLIER_MANAGE)
        headers = ['Company', 'Contact', 'Phone', 'Email', 'Address', 'TIN/VRN', 'Active']
        rows = [[s.company_name, s.contact_person, s.phone, s.email, s.physical_address, s.tin_vrn, s.is_active] for s in Supplier.objects.filter(tenant=organization).order_by('company_name')]
    else:
        raise Http404
    response = HttpResponse(workbook_bytes(headers, rows, title=record_type.title()), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{record_type}.xlsx"'
    return response


@login_required
def invoice_serials(request, pk):
    organization = _scope(request, PermissionCode.BILLING_CREATE)
    invoice = get_object_or_404(
        BillingDocument.objects.filter(
            tenant=organization, document_type=BillingDocument.DocumentType.INVOICE,
            status__in=[BillingDocument.Status.DRAFT, BillingDocument.Status.ISSUED],
        ).prefetch_related('items__product'),
        pk=pk,
    )
    lines = list(invoice.items.filter(product__is_serialized=True).select_related('product').order_by('id'))
    line_options = []
    for line in lines:
        selected_ids = set(DocumentSerialSelection.objects.filter(tenant=organization, billing_line=line).values_list('stock_unit_id', flat=True))
        units = StockUnit.objects.filter(
            tenant=organization, product=line.product, status=StockUnit.Status.AVAILABLE
        ).order_by('serial_number')
        line_options.append({'line': line, 'units': units, 'selected_ids': selected_ids})
    if request.method == 'POST':
        errors = []
        selections = {}
        for entry in line_options:
            line = entry['line']
            ids = request.POST.getlist(f'line_{line.pk}')
            if len(ids) != int(line.quantity):
                errors.append(f'{line.product.name} requires exactly {int(line.quantity)} serial numbers.')
                continue
            units = list(StockUnit.objects.filter(
                tenant=organization, product=line.product, status=StockUnit.Status.AVAILABLE, pk__in=ids
            ))
            if len(units) != len(ids):
                errors.append(f'A selected serial for {line.product.name} is unavailable.')
            selections[line.pk] = units
        if not errors:
            with transaction.atomic():
                DocumentSerialSelection.objects.filter(tenant=organization, billing_line__in=lines).delete()
                DocumentSerialSelection.objects.bulk_create([
                    DocumentSerialSelection(
                        organization=organization, tenant=organization, billing_line_id=line_id, stock_unit=unit
                    ) for line_id, units in selections.items() for unit in units
                ])
                audit(organization=organization, actor=request.user, action='inventory.invoice.serials_selected', obj=invoice)
            messages.success(request, 'Serial selections saved. Availability will be checked again at full payment.')
            return redirect('billing:document_detail', doc_type='invoice', pk=invoice.pk)
        for error in errors:
            messages.error(request, error)
    return render(request, 'inventory/invoice_serials.html', {'invoice': invoice, 'line_options': line_options})


@login_required
def import_data(request):
    organization = _scope(request, PermissionCode.INVENTORY_IMPORT)
    form = InventoryImportForm(request.POST or None, request.FILES or None, organization=organization)
    job = None
    if request.method == 'POST' and form.is_valid():
        job = validate_workbook(
            organization=organization, actor=request.user, import_type=form.cleaned_data['import_type'], uploaded_file=form.cleaned_data['workbook']
        )
        if job.error_count:
            messages.error(request, 'Validation found errors. Nothing was imported.')
        else:
            messages.success(request, 'Validation succeeded. Review and commit the import.')
    return render(request, 'inventory/import.html', {'form': form, 'job': job, 'recent_jobs': ImportJob.objects.filter(tenant=organization)[:10]})


@login_required
def import_commit(request, pk):
    organization = _scope(request, PermissionCode.INVENTORY_IMPORT)
    if request.method != 'POST':
        raise Http404
    try:
        job = commit_import(organization=organization, actor=request.user, job_id=pk)
        messages.success(request, f'Imported {job.row_count} rows successfully.')
    except (ValueError, InventoryError) as exc:
        messages.error(request, str(exc))
    return redirect('inventory:import_data')


@login_required
def import_template(request, import_type):
    _scope(request, PermissionCode.INVENTORY_IMPORT)
    try:
        payload = template_workbook(import_type)
    except ValueError:
        raise Http404
    response = HttpResponse(payload, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="{import_type}-template.xlsx"'
    return response


@login_required
def settings_edit(request):
    organization = _scope(request, PermissionCode.CATEGORY_MANAGE)
    settings_obj, _ = InventorySettings.objects.get_or_create(organization=organization, tenant=organization)
    form = InventorySettingsForm(request.POST or None, instance=settings_obj, organization=organization)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.organization = obj.tenant = organization
        obj.updated_by = request.user
        obj.save()
        audit(organization=organization, actor=request.user, action='inventory.settings.updated', obj=obj)
        messages.success(request, 'Inventory settings saved.')
        return redirect('inventory:dashboard')
    return render(request, 'inventory/form.html', {
        'form': form, 'title': 'Inventory settings',
        'subtitle': 'Configure operational thresholds used by dashboard alerts and movement reports.',
        'cancel_url': 'inventory:dashboard', 'submit_label': 'Save settings',
    })

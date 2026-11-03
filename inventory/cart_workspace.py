"""Stateless sale workspaces; persist a draft only with its first valid change."""
import uuid

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from audit.models import AuditLog
from users.permissions import PermissionCode, has_tenant_permission
from .cart_lifecycle import genuinely_empty, visible_carts
from .models import Cart
from .services import CartService, InventoryError, audit

SALT = 'inventory.cart-workspace.v1'


def new_token(*, organization, actor):
    return signing.dumps({'tenant': organization.pk, 'actor': actor.pk, 'nonce': uuid.uuid4().hex}, salt=SALT)


def workspace_key(*, token, organization, actor):
    try:
        payload = signing.loads(token, salt=SALT, max_age=86400)
        if payload['tenant'] != organization.pk or payload['actor'] != actor.pk:
            raise PermissionDenied('Sale workspace unavailable.')
        return uuid.UUID(hex=payload['nonce']).hex
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise PermissionDenied('This sale workspace expired or is unavailable. Start a new sale.')


def existing_cart(*, key, organization, actor, membership=None):
    event = AuditLog.objects.unscoped().filter(
        tenant=organization, organization=organization, actor=actor,
        object_type='CartWorkspace', object_id=key, action='inventory.cart.materialized',
    ).first()
    if event is None:
        return None
    cart = visible_carts(organization=organization, actor=actor, membership=membership).filter(
        pk=event.new_value['cart_id'], created_by=actor,
    ).first()
    if cart is None:
        raise PermissionDenied('The original sale is unavailable. Start a new sale.')
    return cart


@transaction.atomic
def materialize(*, token, organization, actor, membership, action, data):
    from .forms import CartForm

    if not has_tenant_permission(actor, organization, PermissionCode.CART_MANAGE, membership=membership):
        raise PermissionDenied('Cannot start a sale.')
    key = workspace_key(token=token, organization=organization, actor=actor)
    get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
    cart = existing_cart(key=key, organization=organization, actor=actor, membership=membership)
    if cart is not None:
        return cart
    if action not in {'add', 'line', 'details'}:
        raise InventoryError('Unsupported sale action.')
    cart = Cart.objects.create(tenant=organization, organization=organization, created_by=actor)
    audit(organization=organization, actor=actor, action='inventory.cart.created', obj=cart,
          new_value={'created_by_id': actor.pk, 'status': cart.status},
          metadata={'source': 'sale_workspace', 'first_action': action,
                    'actor_username': actor.get_username(), 'membership_id': membership.pk})
    if action in {'add', 'line'}:
        incoming = data.copy()
        if action == 'add':
            incoming['quantity'] = '1'
            incoming['discount_amount'] = '0'
        CartService.save_line(organization=organization, cart_id=cart.pk, data=incoming,
                              actor=actor, membership=membership)
    else:
        form = CartForm(data, instance=cart, organization=organization, membership=membership)
        if not form.is_valid():
            raise ValidationError(dict(form.errors))
        cart = CartService.save_details(organization=organization, cart_id=cart.pk, data=data,
                                        actor=actor, membership=membership)
        if genuinely_empty(cart):
            raise InventoryError('No draft was saved. Add an item, customer, or sale details first.')
    AuditLog.objects.create(
        tenant=organization, organization=organization, actor=actor, performed_by=actor,
        action='inventory.cart.materialized', object_type='CartWorkspace', object_id=key,
        new_value={'cart_id': cart.pk}, metadata={'membership_id': membership.pk},
    )
    return cart

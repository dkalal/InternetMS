from users.permissions import PermissionCode, has_tenant_permission


def inventory_access(request):
    user = getattr(request, 'user', None)
    tenant = getattr(request, 'tenant', None)
    membership = getattr(request, 'membership', None)
    allowed = lambda code: has_tenant_permission(user, tenant, code, membership=membership)
    return {
        'INVENTORY_CAN_VIEW_COSTS': allowed(PermissionCode.COST_REPORT_VIEW),
        'INVENTORY_CAN_MANAGE_PRODUCTS': allowed(PermissionCode.PRODUCT_MANAGE),
        'INVENTORY_CAN_MANAGE_CATEGORIES': allowed(PermissionCode.CATEGORY_MANAGE),
        'INVENTORY_CAN_MANAGE_STOCK': allowed(PermissionCode.STOCK_ADJUST),
        'INVENTORY_CAN_VIEW_STOCK': allowed(PermissionCode.STOCK_VIEW),
        'INVENTORY_CAN_VIEW_MOVEMENTS': allowed(PermissionCode.STOCK_MOVEMENT_VIEW),
        'INVENTORY_CAN_VIEW_PURCHASES': allowed(PermissionCode.PURCHASE_VIEW),
        'INVENTORY_CAN_MANAGE_PURCHASES': allowed(PermissionCode.PURCHASE_CONFIRM),
        'INVENTORY_CAN_MANAGE_SUPPLIERS': allowed(PermissionCode.SUPPLIER_MANAGE),
        'INVENTORY_CAN_MANAGE_CARTS': allowed(PermissionCode.CART_MANAGE),
        'INVENTORY_CAN_IMPORT': allowed(PermissionCode.INVENTORY_IMPORT),
        'INVENTORY_CAN_EXPORT': allowed(PermissionCode.INVENTORY_EXPORT),
    }

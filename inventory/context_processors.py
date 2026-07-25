from users.permissions import PermissionCode, user_has_permission


def inventory_access(request):
    user = getattr(request, 'user', None)
    return {
        'INVENTORY_CAN_VIEW_COSTS': user_has_permission(user, PermissionCode.COST_REPORT_VIEW),
        'INVENTORY_CAN_MANAGE_PRODUCTS': user_has_permission(user, PermissionCode.PRODUCT_MANAGE),
        'INVENTORY_CAN_MANAGE_CATEGORIES': user_has_permission(user, PermissionCode.CATEGORY_MANAGE),
        'INVENTORY_CAN_MANAGE_STOCK': user_has_permission(user, PermissionCode.STOCK_ADJUST),
        'INVENTORY_CAN_VIEW_STOCK': user_has_permission(user, PermissionCode.STOCK_VIEW),
        'INVENTORY_CAN_VIEW_MOVEMENTS': user_has_permission(user, PermissionCode.STOCK_MOVEMENT_VIEW),
        'INVENTORY_CAN_VIEW_PURCHASES': user_has_permission(user, PermissionCode.PURCHASE_VIEW),
        'INVENTORY_CAN_MANAGE_PURCHASES': user_has_permission(user, PermissionCode.PURCHASE_CONFIRM),
        'INVENTORY_CAN_MANAGE_SUPPLIERS': user_has_permission(user, PermissionCode.SUPPLIER_MANAGE),
        'INVENTORY_CAN_MANAGE_CARTS': user_has_permission(user, PermissionCode.CART_MANAGE),
        'INVENTORY_CAN_IMPORT': user_has_permission(user, PermissionCode.INVENTORY_IMPORT),
        'INVENTORY_CAN_EXPORT': user_has_permission(user, PermissionCode.INVENTORY_EXPORT),
    }

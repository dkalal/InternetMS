from django import template


register = template.Library()


@register.filter
def get_item(mapping, key):
    if mapping is None:
        return None
    try:
        return mapping[key]
    except Exception:
        return None

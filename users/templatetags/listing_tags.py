from django import template

from internetservices.listing import build_querystring

register = template.Library()


@register.simple_tag(takes_context=True)
def query_update(context, **updates):
    return build_querystring(context["request"].GET, remove=("page",), **updates)


@register.simple_tag(takes_context=True)
def sort_url(context, sort):
    return "?" + build_querystring(context["request"].GET, remove=("page",), sort=sort)


@register.simple_tag(takes_context=True)
def page_url(context, page_number):
    return "?" + build_querystring(context["request"].GET, page=page_number)

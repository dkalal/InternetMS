from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import QueryDict


DEFAULT_PAGE_SIZE = 25
PAGE_SIZE_OPTIONS = (10, 25, 50, 100)


@dataclass(frozen=True)
class PageSize:
    value: int
    selected: bool
    url: str


def positive_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError):
        return None
    return parsed if parsed >= 0 else None


def clean_page_size(value: str | None, *, default: int = DEFAULT_PAGE_SIZE) -> int:
    try:
        page_size = int(value or default)
    except (TypeError, ValueError):
        return default
    return page_size if page_size in PAGE_SIZE_OPTIONS else default


def build_querystring(params: QueryDict, *, remove: Iterable[str] = (), **updates) -> str:
    query = params.copy()
    for key in remove:
        query.pop(key, None)
    for key, value in updates.items():
        query.pop(key, None)
        if value not in (None, ""):
            query[key] = str(value)
    return query.urlencode()


def apply_sort(queryset: QuerySet, sort_value: str | None, allowed: dict[str, tuple[str, ...]], default: str):
    selected = sort_value if sort_value in allowed else default
    return queryset.order_by(*allowed[selected]), selected


def paginate_queryset(request, queryset: QuerySet, *, default_page_size: int = DEFAULT_PAGE_SIZE):
    page_size = clean_page_size(request.GET.get("page_size"), default=default_page_size)
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    start_index = page_obj.start_index() if paginator.count else 0
    end_index = page_obj.end_index() if paginator.count else 0

    page_sizes = [
        PageSize(
            value=size,
            selected=size == page_size,
            url="?" + build_querystring(request.GET, remove=("page",), page_size=size),
        )
        for size in PAGE_SIZE_OPTIONS
    ]

    return {
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "page_size": page_size,
        "page_size_options": page_sizes,
        "page_range": list(paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1)),
        "result_count": paginator.count,
        "start_index": start_index,
        "end_index": end_index,
        "querystring": build_querystring(request.GET, remove=("page",)),
    }


def page_context(request, page_obj, *, page_size: int):
    paginator = page_obj.paginator
    return {
        "page_size": page_size,
        "page_size_options": [
            PageSize(
                value=size,
                selected=size == page_size,
                url="?" + build_querystring(request.GET, remove=("page",), page_size=size),
            )
            for size in PAGE_SIZE_OPTIONS
        ],
        "page_range": list(paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1)),
        "result_count": paginator.count,
        "start_index": page_obj.start_index() if paginator.count else 0,
        "end_index": page_obj.end_index() if paginator.count else 0,
        "querystring": build_querystring(request.GET, remove=("page",)),
    }

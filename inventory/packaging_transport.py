"""Bounded form transport only; owning forms/services remain authoritative."""
import json
import re
from products.packaging import PackagingError

SPECS = {
    'counted_payload': (r'counted-(?:(?:TOTAL|INITIAL|MIN_NUM|MAX_NUM)_FORMS|(?:0|[1-9][0-9]{0,3})-(?:packaging|quantity|sealed|supplier_identifier|expiry_date|DELETE))', 14004, 2000000),
    'container_payload': (r'container_[1-9][0-9]{0,17}', 2000, 250000),
    'selection_payload': (r'line_[1-9][0-9]{0,17}_container_[1-9][0-9]{0,17}', 2000, 250000),
}


def expand_fields(data, payload_name):
    if data is None or payload_name not in data:
        return data
    pattern, maximum, text_limit = SPECS[payload_name]
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate fields')
            result[key] = value
        return result
    try:
        if hasattr(data, 'getlist') and len(data.getlist(payload_name)) != 1:
            raise ValueError('Duplicate payload fields')
        raw = data.get(payload_name)
        if not isinstance(raw, str) or len(raw) > text_limit:
            raise ValueError('Oversized payload')
        values = json.loads(raw, object_pairs_hook=unique_pairs)
        if not isinstance(values, dict) or len(values) > maximum:
            raise ValueError('Invalid field count')
        if any(not isinstance(key, str) or not re.fullmatch(pattern, key) or not isinstance(value, str)
               or len(value) > 1024 for key, value in values.items()):
            raise ValueError('Invalid field')
        if any(re.fullmatch(pattern, key) for key in data):
            raise ValueError('Ambiguous direct and encoded fields')
    except (ValueError, TypeError, RecursionError) as exc:
        raise PackagingError('Physical-piece form data is invalid or too large. Review the selected pieces and submit again.') from exc
    expanded = data.copy()
    del expanded[payload_name]
    for key, value in values.items():
        expanded[key] = value
    return expanded

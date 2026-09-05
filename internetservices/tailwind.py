from __future__ import annotations

from django import forms


INPUT_CLASSES = (
    "block min-h-10 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm "
    "text-slate-900 shadow-sm placeholder:text-slate-400 "
    "focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/30 "
    "disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500"
)

SELECT_CLASSES = (
    "jims-select block min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm "
    "focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/30"
)

SELECT_MULTIPLE_CLASSES = (
    "jims-select-multiple block min-h-28 w-full rounded-lg border border-slate-300 bg-white p-2 text-sm "
    "text-slate-900 shadow-sm focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/30"
)

TEXTAREA_CLASSES = (
    "block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm "
    "placeholder:text-slate-400 focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/30"
)

CHECKBOX_CLASSES = "h-5 w-5 rounded border-slate-300 text-brand-600 focus:ring-brand-600/30"
CHECKBOX_CHOICE_CLASSES = "jims-choice-input"
RADIO_CHOICE_CLASSES = "jims-radio-input"


def _merge_class(existing: str | None, added: str) -> str:
    if not existing:
        return added
    if added in existing:
        return existing
    return f"{existing} {added}"


def apply_tailwind(form: forms.BaseForm) -> None:
    """
    Mutates widgets in-place by adding Tailwind classes.
    Keep templates simple: render `{{ field }}` and it will be styled.
    """

    for bound_name, field in form.fields.items():
        widget = field.widget

        # Choice groups are container widgets whose attrs are also copied to
        # every generated input. They must never inherit full-width text-input
        # sizing; templates can safely iterate their native inputs instead.
        if isinstance(widget, forms.CheckboxSelectMultiple):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), CHECKBOX_CHOICE_CLASSES)
            continue

        if isinstance(widget, forms.RadioSelect):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), RADIO_CHOICE_CLASSES)
            continue

        if isinstance(widget, (forms.CheckboxInput,)):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), CHECKBOX_CLASSES)
            continue

        if isinstance(widget, forms.SelectMultiple):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), SELECT_MULTIPLE_CLASSES)
            continue

        if isinstance(widget, forms.Select):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), SELECT_CLASSES)
            if isinstance(field, forms.ModelChoiceField):
                widget.attrs.setdefault("data-searchable-select", "true")
                widget.attrs.setdefault("data-search-label", str(field.label or bound_name).strip())
                widget.attrs.setdefault("data-search-placeholder", f"Search {str(field.label or bound_name).lower()}...")
            continue

        if isinstance(widget, (forms.Textarea,)):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), TEXTAREA_CLASSES)
            continue

        if isinstance(widget, (forms.DateInput, forms.DateTimeInput, forms.EmailInput, forms.NumberInput, forms.TextInput, forms.URLInput, forms.PasswordInput)):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), INPUT_CLASSES)
            continue

        # Fallback for anything else with a class attr.
        widget.attrs["class"] = _merge_class(widget.attrs.get("class"), INPUT_CLASSES)

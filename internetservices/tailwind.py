from __future__ import annotations

from django import forms


INPUT_CLASSES = (
    "block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm "
    "text-slate-900 shadow-sm placeholder:text-slate-400 "
    "focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/30 "
    "disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500"
)

SELECT_CLASSES = (
    "block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm "
    "focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/30"
)

TEXTAREA_CLASSES = (
    "block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm "
    "placeholder:text-slate-400 focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/30"
)

CHECKBOX_CLASSES = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-600/30"


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

        if isinstance(widget, (forms.CheckboxInput,)):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), CHECKBOX_CLASSES)
            continue

        if isinstance(widget, (forms.Select, forms.SelectMultiple)):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), SELECT_CLASSES)
            continue

        if isinstance(widget, (forms.Textarea,)):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), TEXTAREA_CLASSES)
            continue

        if isinstance(widget, (forms.DateInput, forms.DateTimeInput, forms.EmailInput, forms.NumberInput, forms.TextInput, forms.URLInput, forms.PasswordInput)):
            widget.attrs["class"] = _merge_class(widget.attrs.get("class"), INPUT_CLASSES)
            continue

        # Fallback for anything else with a class attr.
        widget.attrs["class"] = _merge_class(widget.attrs.get("class"), INPUT_CLASSES)

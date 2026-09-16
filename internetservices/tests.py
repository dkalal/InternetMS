from pathlib import Path
from decimal import Decimal

from django import forms
from django.contrib.staticfiles import finders
from django.template import Context, Template
from django.template.loader import get_template
from django.test import SimpleTestCase

from users.models import Organization

from .tailwind import apply_tailwind
from .number_display import compact_decimal


class SelectDesignSystemTests(SimpleTestCase):
    class ExampleForm(forms.Form):
        status = forms.ChoiceField(choices=(("active", "Active"), ("inactive", "Inactive")))
        organization = forms.ModelChoiceField(queryset=Organization.objects.none())
        organizations = forms.ModelMultipleChoiceField(queryset=Organization.objects.none())
        permissions = forms.MultipleChoiceField(
            choices=(("view", "View"), ("edit", "Edit")), widget=forms.CheckboxSelectMultiple,
        )
        decision = forms.ChoiceField(
            choices=(("approve", "Approve"), ("reject", "Reject")), widget=forms.RadioSelect,
        )

    def test_small_choices_stay_native_and_entity_choices_become_searchable(self):
        form = self.ExampleForm()
        apply_tailwind(form)

        self.assertIn("jims-select", form.fields["status"].widget.attrs["class"])
        self.assertNotIn("data-searchable-select", form.fields["status"].widget.attrs)
        self.assertIn("jims-select", form.fields["organization"].widget.attrs["class"])
        self.assertEqual(form.fields["organization"].widget.attrs["data-searchable-select"], "true")
        self.assertIn("jims-select-multiple", form.fields["organizations"].widget.attrs["class"])
        self.assertNotIn("data-searchable-select", form.fields["organizations"].widget.attrs)

    def test_checkbox_and_radio_groups_never_receive_text_input_dimensions(self):
        form = self.ExampleForm()
        apply_tailwind(form)

        checkbox_class = form.fields["permissions"].widget.attrs["class"]
        radio_class = form.fields["decision"].widget.attrs["class"]
        self.assertEqual(checkbox_class, "jims-choice-input")
        self.assertEqual(radio_class, "jims-radio-input")
        self.assertNotIn("w-full", checkbox_class)
        self.assertNotIn("min-h-10", radio_class)

    def test_shared_runtime_covers_template_and_dynamically_inserted_selects(self):
        script = Path(finders.find("inventory/js/jims-ui.js")).read_text(encoding="utf-8")
        stylesheet = Path(finders.find("inventory/css/jims-ui.css")).read_text(encoding="utf-8")

        self.assertIn('root.querySelectorAll("select").forEach(initNativeSelect)', script)
        self.assertIn('select.classList.add("jims-select")', script)
        self.assertIn('new MutationObserver(function (mutations)', script)
        self.assertIn('status.setAttribute("aria-live", "polite")', script)
        self.assertIn('window.visualViewport.addEventListener("resize", queuePosition)', script)
        self.assertIn(".jims-select:focus-visible", stylesheet)
        self.assertIn('.jims-select[aria-invalid="true"]', stylesheet)
        self.assertIn("@media (forced-colors: active)", stylesheet)
        self.assertIn('root.querySelectorAll("[data-choice-group]").forEach(initChoiceGroup)', script)
        self.assertIn(".jims-choice-card:has(input:checked)", stylesheet)


class StylesheetCompatibilityTests(SimpleTestCase):
    def setUp(self):
        stylesheet_path = finders.find("inventory/css/jims-ui.css")
        self.assertIsNotNone(stylesheet_path)
        self.stylesheet = Path(stylesheet_path).read_text(encoding="utf-8")

    def test_webkit_fallbacks_precede_standard_filter_and_mask_properties(self):
        for value in ("blur(2px)", "blur(8px)"):
            prefixed = f"-webkit-backdrop-filter: {value};"
            standard = f"backdrop-filter: {value};"
            self.assertIn(f"{prefixed}\n  {standard}", self.stylesheet)

        for value in (
            "linear-gradient(to right, transparent, #000 28%)",
            "linear-gradient(to right, transparent, #000 24%)",
        ):
            prefixed = f"-webkit-mask-image: {value};"
            standard = f"mask-image: {value};"
            self.assertIn(f"{prefixed}\n  {standard}", self.stylesheet)

    def test_scroll_regions_use_accessible_cross_browser_fallbacks(self):
        self.assertNotIn("scrollbar-gutter:", self.stylesheet)
        self.assertNotIn("scrollbar-width:", self.stylesheet)
        self.assertNotIn("scrollbar-color:", self.stylesheet)
        self.assertGreaterEqual(self.stylesheet.count("overflow-y: scroll;"), 4)


class NumberDisplayTests(SimpleTestCase):
    def test_compact_decimal_removes_only_insignificant_zeroes_and_groups_digits(self):
        self.assertEqual(compact_decimal(Decimal("1250.000000")), "1,250")
        self.assertEqual(compact_decimal(Decimal("1250.500000")), "1,250.5")
        self.assertEqual(compact_decimal(Decimal("0.000001")), "0.000001")
        self.assertEqual(compact_decimal(Decimal("-0.000000")), "0")
        self.assertEqual(compact_decimal(Decimal("655.737705"), max_places=2), "655.74")

    def test_quantity_template_filter_is_safe_for_numbers_text_and_none(self):
        template = Template(
            "{% load number_display %}{{ whole|quantity_display }}|"
            "{{ fraction|quantity_display }}|{{ label|number_display }}|{{ missing|number_display }}"
        )
        rendered = template.render(Context({
            "whole": Decimal("10.000000"),
            "fraction": Decimal("10.250000"),
            "label": "001",
            "missing": None,
        }))
        self.assertEqual(rendered, "10|10.25|001|")

    def test_six_decimal_form_inputs_are_compact_but_keep_meaningful_precision(self):
        class QuantityForm(forms.Form):
            quantity = forms.DecimalField(max_digits=16, decimal_places=6, initial=Decimal("12.500000"))
            exact = forms.DecimalField(max_digits=16, decimal_places=6, initial=Decimal("0.000001"))
            amount = forms.DecimalField(max_digits=12, decimal_places=2, initial=Decimal("100.00"))

        form = QuantityForm()
        apply_tailwind(form)

        self.assertIn('value="12.5"', form["quantity"].as_widget())
        self.assertIn('value="0.000001"', form["exact"].as_widget())
        self.assertIn('value="100.00"', form["amount"].as_widget())
        self.assertIn('inputmode="decimal"', form["quantity"].as_widget())

    def test_all_quantity_templates_compile_with_the_shared_filter(self):
        for template_name in (
            "inventory/dashboard.html",
            "inventory/stock_list.html",
            "inventory/movement_list.html",
            "inventory/purchase_detail.html",
            "inventory/includes/pos_cart_lines.html",
            "inventory/invoice_serials.html",
            "inventory/report.html",
            "products/product_list.html",
            "products/product_detail.html",
            "products/product_confirm_delete.html",
            "billing/billing_sheet_detail.html",
            "billing/document_detail.html",
            "billing/includes/print_items_table.html",
            "billing/sales_document_print.html",
            "billing/receipt_print_tra.html",
            "billing/promotion_list.html",
        ):
            with self.subTest(template_name=template_name):
                self.assertIsNotNone(get_template(template_name))

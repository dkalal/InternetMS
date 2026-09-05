from pathlib import Path

from django import forms
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

from users.models import Organization

from .tailwind import apply_tailwind


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

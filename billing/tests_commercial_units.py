from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.core.exceptions import ValidationError
from inventory.tests_packaging_receipts import ReceiptFixture
from .commercial_units import unit_token, resolve_unit

@patch('products.packaging.workflows_ready', return_value=True)
class DirectBillingUnitTests(ReceiptFixture, TestCase):
    def test_package_and_base_units_resolve_without_stock_mutation(self, ready):
        token = unit_token(self.org, self.product, self.package.pk)
        values = resolve_unit(self.org, self.product, token, '2')
        self.assertEqual(values['base_quantity'], Decimal('610'))
        self.assertEqual(values['selected_unit_snapshot'], 'Box')
        base = resolve_unit(self.org, self.product, unit_token(self.org, self.product, None), '50')
        self.assertEqual(base['base_quantity'], Decimal('50'))
        self.assertEqual(self.product.stock_containers.count(), 0)

    def test_forged_and_stale_selections_fail_closed(self, ready):
        token = unit_token(self.org, self.product, self.package.pk)
        with self.assertRaises(ValidationError):
            resolve_unit(self.org, self.product, token + 'forged', '1')
        self.product.packaging_version += 1
        with self.assertRaises(ValidationError):
            resolve_unit(self.org, self.product, token, '1')

    def test_direct_form_passes_selected_package_to_document_input(self, ready):
        from .forms import BillingLineItemForm
        from .models import BillingLineItem
        form = BillingLineItemForm(data={
            'product': self.product.pk, 'quantity': '2', 'unit_price': '900',
            'commercial_unit': unit_token(self.org, self.product, self.package.pk),
            'unit_snapshot': 'forged unit', 'description': 'Cable',
            'billing_behavior': BillingLineItem.BillingBehavior.ONE_TIME,
            'pricing_mode': BillingLineItem.PricingMode.RETAIL, 'discount_amount': '0',
        }, organization=self.org)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['product_packaging_id'], self.package.pk)
        self.assertEqual(form.cleaned_data['unit_snapshot'], 'Box')
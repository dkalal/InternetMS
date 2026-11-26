from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from django.test import TestCase
from django.urls import reverse
from products.models import Product, ProductPackaging
from .tests_packaging_foundation import PackagingFixture
from .models import InventoryBalance, StockContainer, StockMovement


@patch('products.packaging.workflows_ready', return_value=True)
class PackagingAdministrationTests(PackagingFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.actor)

    def test_configure_and_zero_stock_activation_through_authorized_forms(self, ready):
        url = reverse('product-packaging', args=[self.product.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'action': 'configure', 'configuration-unit': self.box.pk,
            'configuration-factor': '305', 'configuration-standard_price': '900'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ProductPackaging.objects.get().factor, Decimal('305'))
        self.product.refresh_from_db()
        response = self.client.post(url, {'action': 'activate', 'activation-request_key': str(uuid4()),
            'activation-expected_quantity': '0', 'activation-expected_average_cost': '1',
            'activation-expected_version': str(self.product.packaging_version), 'activation-reason': 'Verified empty warehouse',
            'counted-TOTAL_FORMS': '0', 'counted-INITIAL_FORMS': '0', 'counted-MIN_NUM_FORMS': '0', 'counted-MAX_NUM_FORMS': '2000'})
        self.assertEqual(response.status_code, 302, response.content[:1000])
        self.product.refresh_from_db()
        self.assertTrue(self.product.packaging_enabled)
        self.assertEqual(StockContainer.objects.count(), 0)

    def test_existing_legacy_base_balance_preparation_is_explicit_and_value_preserving(self, ready):
        from .packaging import prepare_existing_balance
        self.configure()
        Product.objects.unscoped().filter(pk=self.product.pk).update(quantity=610, stock=610)
        values = dict(organization=self.org, actor=self.actor, product_id=self.product.pk,
            expected_quantity='610', expected_average_cost='1', reason='Verified legacy base stock before physical count')
        balance = prepare_existing_balance(**values)
        self.assertEqual(balance.quantity, Decimal('610'))
        self.assertEqual(balance.average_cost, Decimal('1'))
        self.assertEqual(prepare_existing_balance(**values).pk, balance.pk)
        self.assertEqual(StockMovement.objects.count(), 1)
        self.change(expected_quantity='610', containers=[{'quantity': '305', 'packaging_id': ProductPackaging.objects.get().pk, 'sealed': True} for _ in range(2)])
        balance.refresh_from_db()
        self.assertEqual(balance.quantity, Decimal('610'))
        self.assertEqual(balance.total_value, Decimal('610'))
        self.assertEqual(StockMovement.objects.count(), 1)
        self.assertEqual(StockContainer.objects.count(), 2)

    def test_unknown_action_and_foreign_product_are_rejected_without_mutation(self, ready):
        response = self.client.post(reverse('product-packaging', args=[self.product.pk]), {'action': 'destroy_all'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Choose a valid packaging action')
        foreign = Product.objects.create(tenant=self.other, organization=self.other, name='Foreign', sku='FOREIGN', quantity=0, stock=0, buying_price=1, selling_price=3)
        self.assertEqual(self.client.get(reverse('product-packaging', args=[foreign.pk])).status_code, 404)
        self.assertEqual(ProductPackaging.objects.count(), 0)

    def test_large_counted_activation_uses_bounded_payload_under_small_global_field_limit(self, ready):
        import json
        package = self.configure()
        self.stock('300')
        self.product.refresh_from_db()
        counted = {'counted-TOTAL_FORMS': '300', 'counted-INITIAL_FORMS': '0',
            'counted-MIN_NUM_FORMS': '0', 'counted-MAX_NUM_FORMS': '2000'}
        for index in range(300):
            counted.update({f'counted-{index}-quantity': '1', f'counted-{index}-packaging': '',
                f'counted-{index}-supplier_identifier': '', f'counted-{index}-expiry_date': ''})
        with self.settings(DATA_UPLOAD_MAX_NUMBER_FIELDS=50):
            response = self.client.post(reverse('product-packaging', args=[self.product.pk]), {
                'action': 'activate', 'counted_payload': json.dumps(counted), 'activation-request_key': str(uuid4()),
                'activation-expected_quantity': '300', 'activation-expected_average_cost': '1',
                'activation-expected_version': str(self.product.packaging_version), 'activation-reason': 'Counted 300 separate physical pieces'})
        self.assertEqual(response.status_code, 302, response.content[:1000])
        self.assertEqual(StockContainer.objects.count(), 300)
        self.assertEqual(InventoryBalance.objects.get(product=self.product).quantity, Decimal('300'))


class PackagingReleaseCapabilityTests(PackagingFixture, TestCase):
    def test_release_capability_never_automatically_activates_existing_products(self):
        from products.packaging import workflows_ready
        self.assertTrue(workflows_ready())
        self.configure()
        self.product.refresh_from_db()
        self.assertFalse(self.product.packaging_enabled)
        self.assertEqual(StockContainer.objects.count(), 0)
        self.change()
        self.product.refresh_from_db()
        self.assertTrue(self.product.packaging_enabled)
        self.assertEqual(self.product.quantity, Decimal('0'))
        self.assertEqual(StockContainer.objects.count(), 0)

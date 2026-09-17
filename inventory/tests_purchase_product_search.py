from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile


User = get_user_model()


class PurchaseProductSearchTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Search Tenant', slug='purchase-search-tenant')
        self.other_organization = Organization.objects.create(name='Other Search Tenant', slug='other-purchase-search-tenant')
        self.admin = User.objects.create_user(username='purchase-search-admin', password='pass')
        UserAccessProfile.objects.create(
            user=self.admin,
            tenant=self.organization,
            role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.sales = User.objects.create_user(username='purchase-search-sales', password='pass')
        UserAccessProfile.objects.create(
            user=self.sales,
            tenant=self.organization,
            role=UserAccessProfile.Role.TENANT_STAFF,
        )
        self.unit = UnitOfMeasure.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name='Unit',
            symbol='pc',
        )
        self.category = ProductCategory.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name='Equipment',
            default_unit=self.unit,
            measure_unit='pc',
        )
        self.product = self.make_product(
            organization=self.organization,
            category=self.category,
            unit=self.unit,
            name='MikroTik Router',
            sku='MKT-RB5009',
            brand='MikroTik',
            model='RB5009',
            serialized=True,
        )
        other_unit = UnitOfMeasure.objects.create(
            organization=self.other_organization,
            tenant=self.other_organization,
            name='Unit',
            symbol='pc',
        )
        other_category = ProductCategory.objects.create(
            organization=self.other_organization,
            tenant=self.other_organization,
            name='Private Equipment',
            default_unit=other_unit,
            measure_unit='pc',
        )
        self.other_product = self.make_product(
            organization=self.other_organization,
            category=other_category,
            unit=other_unit,
            name='Private Router',
            sku='PRIVATE-RTR-1',
        )
        self.url = reverse('inventory:purchase_product_search')

    def make_product(
        self,
        *,
        organization,
        category,
        unit,
        name,
        sku,
        brand='',
        model='',
        serialized=False,
        item_type=Product.ItemType.PHYSICAL,
        track_stock=True,
        active=True,
    ):
        return Product.objects.create(
            organization=organization,
            tenant=organization,
            catalog_category=category,
            sales_unit=unit,
            name=name,
            sku=sku,
            brand=brand,
            model_number=model,
            item_type=item_type,
            track_stock=track_stock,
            is_active=active,
            is_serialized=serialized,
            quantity=Decimal('0.000000'),
            stock=0,
            buying_price=Decimal('100.000000'),
            selling_price=Decimal('150.00'),
        )

    def test_authentication_and_purchase_permission_are_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.client.login(username='purchase-search-sales', password='pass')
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_endpoint_is_read_only(self):
        self.client.login(username='purchase-search-admin', password='pass')
        self.assertEqual(self.client.post(self.url, {'q': 'router'}).status_code, 405)

    def test_results_are_tenant_scoped_and_include_combobox_metadata(self):
        self.client.login(username='purchase-search-admin', password='pass')
        response = self.client.get(self.url, {'q': 'router'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row['id'] for row in payload['results']], [self.product.pk])
        self.assertNotContains(response, 'PRIVATE-RTR-1')
        self.assertEqual(payload['results'][0]['unit'], 'pc')
        self.assertTrue(payload['results'][0]['serialized'])
        self.assertFalse(payload['results'][0]['expiry'])

    def test_search_matches_name_sku_brand_and_model(self):
        self.client.login(username='purchase-search-admin', password='pass')
        for query in ('MikroTik', 'MKT-RB5009', 'RB5009'):
            with self.subTest(query=query):
                ids = [row['id'] for row in self.client.get(self.url, {'q': query}).json()['results']]
                self.assertEqual(ids, [self.product.pk])

    def test_inactive_service_and_non_stock_products_are_excluded(self):
        self.make_product(
            organization=self.organization,
            category=self.category,
            unit=self.unit,
            name='Inactive Router',
            sku='INACTIVE-RTR',
            active=False,
        )
        self.make_product(
            organization=self.organization,
            category=self.category,
            unit=self.unit,
            name='Installation Service',
            sku='SERVICE-1',
            item_type=Product.ItemType.SERVICE,
            track_stock=False,
        )
        self.make_product(
            organization=self.organization,
            category=self.category,
            unit=self.unit,
            name='Untracked Item',
            sku='UNTRACKED-1',
            track_stock=False,
        )
        self.client.login(username='purchase-search-admin', password='pass')

        ids = [row['id'] for row in self.client.get(self.url).json()['results']]
        self.assertEqual(ids, [self.product.pk])

    def test_results_are_paginated_and_bounded(self):
        for number in range(25):
            self.make_product(
                organization=self.organization,
                category=self.category,
                unit=self.unit,
                name=f'Accessory {number:02d}',
                sku=f'SEARCH-ACC-{number:02d}',
            )
        self.client.login(username='purchase-search-admin', password='pass')

        first = self.client.get(self.url, {'q': 'Accessory', 'page': '1'}).json()
        second = self.client.get(self.url, {'q': 'Accessory', 'page': '2'}).json()
        invalid_page = self.client.get(self.url, {'q': 'Accessory', 'page': 'invalid'}).json()

        self.assertEqual(len(first['results']), 20)
        self.assertTrue(first['pagination']['more'])
        self.assertEqual(len(second['results']), 5)
        self.assertFalse(second['pagination']['more'])
        self.assertEqual(invalid_page['pagination']['page'], 1)

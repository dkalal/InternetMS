import json
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from users.models import Organization, UserAccessProfile
from products.models import Product, UnitOfMeasure
from inventory.models import StockMovement
from audit.models import AuditLog


class ProductWorkspaceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Grid', slug='grid')
        self.user = get_user_model().objects.create_user(username='grid', password='pass')
        UserAccessProfile.objects.create(user=self.user, tenant=self.org, role=UserAccessProfile.Role.TENANT_ADMIN)
        self.unit = UnitOfMeasure.objects.create(tenant=self.org, organization=self.org, name='Piece', symbol='pc')
        self.client.force_login(self.user)
        self.url = reverse('product-create')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.token = response.context['batch_token']

    def row(self, id='r1', **overrides):
        values = dict(name='Router', sku='', item_type='physical', sales_unit=str(self.unit.pk),
                      buying_price='10', selling_price='20', technician_price='', category='other',
                      track_stock=True, is_active=True, tax_eligible=True, reorder_threshold='0.00',
                      wholesale_min_quantity='1')
        values.update(overrides)
        return {'id': id, 'values': values}

    def post(self, rows, **files):
        return self.client.post(self.url, {'batch_token': self.token, 'rows': json.dumps(rows), **files})

    def test_multiple_same_name_automatic_skus_and_no_stock(self):
        response = self.post([self.row(), self.row('r2')])
        self.assertEqual(response.status_code, 200, response.content)
        products = Product.objects.unscoped().filter(tenant=self.org)
        self.assertEqual(products.count(), 2)
        self.assertEqual(products.values('sku').distinct().count(), 2)
        self.assertFalse(StockMovement.objects.unscoped().filter(tenant=self.org).exists())
        self.assertEqual(AuditLog.objects.unscoped().filter(tenant=self.org, action='inventory.product.created').count(), 2)

    def test_invalid_row_rolls_back_entire_batch(self):
        response = self.post([self.row(), self.row('r2', selling_price='5')])
        self.assertEqual(response.status_code, 400)
        self.assertIn('r2', response.json()['errors'])
        self.assertFalse(Product.objects.unscoped().filter(tenant=self.org).exists())

    def test_explicit_collision_and_replay(self):
        self.assertEqual(self.post([self.row(sku='a'), self.row('r2', sku='A')]).status_code, 400)
        data = [self.row(sku='a')]
        self.assertEqual(self.post(data).status_code, 200)
        self.assertTrue(self.post(data).json()['replayed'])
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.org).count(), 1)

    def test_foreign_unit_and_partial_row(self):
        other = Organization.objects.create(name='Other grid', slug='other-grid')
        unit = UnitOfMeasure.objects.create(tenant=other, organization=other, name='Other')
        self.assertEqual(self.post([self.row(sales_unit=str(unit.pk))]).status_code, 400)
        self.assertEqual(self.post([self.row(name='')]).status_code, 400)

    def test_limits_and_invalid_picture(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.assertEqual(self.post([self.row('r'+str(i)) for i in range(201)]).status_code, 400)
        response = self.post([self.row()], r1=SimpleUploadedFile('bad.png', b'not an image', content_type='image/png'))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Product.objects.unscoped().filter(tenant=self.org).exists())

    def test_two_hundred_text_rows(self):
        response = self.post([self.row('r'+str(i), name='Product '+str(i)) for i in range(200)])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.org).count(), 200)

    def test_blank_rows_ignored_and_partial_rows_rejected(self):
        from products.workspace import workspace_form
        form = workspace_form(self.org, self.user)
        blank = {name: (form[name].value() if form[name].value() is not None else '') for name in form.fields if name != 'image'}
        blank = json.loads(json.dumps(blank, default=str))
        # Match CompactDecimalInput's actual browser values, not Decimal reprs.
        blank['reorder_threshold'] = '0'
        blank['wholesale_min_quantity'] = '1'
        response = self.post([self.row(), {'id': 'blank', 'values': blank}])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Product.objects.unscoped().filter(tenant=self.org).count(), 1)

    def test_required_custom_fields(self):
        from custom_fields.models import CustomFieldDefinition, CustomFieldValue
        from products.workspace import workspace_form
        CustomFieldDefinition.objects.create(organization=self.org, target_model='product', key='warranty',
                                            label='Warranty', field_type='text', required=True)
        response = self.post([self.row()])
        self.assertEqual(response.status_code, 400)
        field = workspace_form(self.org, self.user).custom_field_names[0]
        self.assertIn(field, response.json()['errors']['r1'])
        self.assertEqual(self.post([self.row(**{field: '12 months'})]).status_code, 200)
        self.assertEqual(CustomFieldValue.objects.filter(tenant=self.org).count(), 1)

    def test_service_and_serial_rules(self):
        response = self.post([self.row(item_type='service', is_serialized=True, track_expiry=True)])
        self.assertEqual(response.status_code, 200, response.content)
        product = Product.objects.unscoped().get(tenant=self.org)
        self.assertFalse(product.track_stock)
        self.assertFalse(product.is_serialized)
        self.assertFalse(product.track_expiry)

    def test_valid_image_and_failed_save_cleanup(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from unittest.mock import patch
        from django.test import override_settings
        from django.core.exceptions import ValidationError
        from products.tests import product_image_upload
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            with patch('products.workspace.audit', side_effect=ValidationError('Audit failed')):
                response = self.post([self.row()], r1=product_image_upload())
            self.assertEqual(response.status_code, 400)
            self.assertFalse(Product.objects.unscoped().filter(tenant=self.org).exists())
            self.assertEqual([p for p in Path(directory).rglob('*') if p.is_file()], [])
            self.assertEqual(self.post([self.row()], r1=product_image_upload()).status_code, 200)
            product = Product.objects.unscoped().get(tenant=self.org)
            self.assertTrue(product.image.name.endswith('.webp'))

    def test_existing_sku_collision(self):
        Product.objects.create(tenant=self.org, organization=self.org, name='Existing', sku='TAKEN',
                               quantity=0, buying_price=10, selling_price=20, sales_unit=self.unit)
        response = self.post([self.row(sku='taken')])
        self.assertEqual(response.status_code, 400)
        self.assertIn('sku', response.json()['errors']['r1'])

    def test_token_and_permission_are_enforced(self):
        from django.core import signing
        forged = signing.dumps({'tenant': self.org.pk + 1, 'actor': self.user.pk, 'key': 'forged'}, salt='product-batch')
        response = self.client.post(self.url, {'batch_token': forged, 'rows': json.dumps([self.row()])})
        self.assertEqual(response.status_code, 400)
        staff = get_user_model().objects.create_user(username='grid-staff', password='pass')
        UserAccessProfile.objects.create(user=staff, tenant=self.org, role=UserAccessProfile.Role.TENANT_STAFF)
        self.client.force_login(staff)
        self.assertEqual(self.post([self.row()]).status_code, 403)
        self.assertFalse(Product.objects.unscoped().filter(tenant=self.org).exists())

    def test_name_brand_one_editor_and_legacy_classification_is_not_exposed(self):
        from products.forms import ProductForm
        response = self.client.get(self.url)
        self.assertContains(response, 'Name / brand')
        self.assertNotContains(response, 'Legacy classification')
        self.assertNotContains(response, 'data-detail="brand"')
        self.assertNotContains(response, 'data-detail="category"')
        self.assertEqual(self.post([self.row(name='TP-Link Router')]).status_code, 200)
        product = Product.objects.unscoped().get(tenant=self.org)
        product.brand = 'TP-Link'
        product.category = 'hardware'
        product.save()
        unbound = ProductForm(instance=product, organization=self.org, actor=self.user)
        self.assertEqual(unbound.initial['name'], 'TP-Link Router')
        self.assertTrue(unbound.fields['brand'].widget.is_hidden)
        values = self.row(name='Dell Router')['values']
        form = ProductForm(values, instance=product, organization=self.org, actor=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.name, 'Dell Router')
        self.assertEqual(updated.brand, 'TP-Link')

    def test_edit_initial_combines_legacy_brand_without_rewriting_the_record(self):
        from products.forms import ProductForm
        product = Product.objects.create(tenant=self.org, organization=self.org, name='Router', brand='TP-Link',
                                         sku='LEGACY-NAME', quantity=0, buying_price=10, selling_price=20, sales_unit=self.unit)
        form = ProductForm(instance=product, organization=self.org, actor=self.user)
        self.assertEqual(form.initial['name'], 'TP-Link Router')
        product.refresh_from_db()
        self.assertEqual(product.name, 'Router')

    def test_inline_custom_field_uses_server_tenant_and_exposes_grid_button(self):
        from custom_fields.models import CustomFieldDefinition
        other = Organization.objects.create(name='Foreign field tenant', slug='foreign-field-tenant')
        response = self.client.get(self.url)
        self.assertContains(response, 'data-grid-custom-field-open')
        self.assertContains(response, 'data-custom-field-grid')
        response = self.client.post(reverse('custom-field-inline-create'), {
            'organization': other.pk, 'target_model': 'product', 'key': 'grid_warranty',
            'label': 'Warranty', 'field_type': 'text', 'required': 'on', 'default_value': '12 months',
            'display_order': '0', 'is_active': 'on', 'show_on_create': 'on', 'show_on_edit': 'on', 'show_on_detail': 'on',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        definition = CustomFieldDefinition.objects.get(key='grid_warranty')
        self.assertEqual(definition.tenant, self.org)
        self.assertContains(response, '12 months')
        self.assertTrue(response.json()['definition']['show_on_create'])
        staff = get_user_model().objects.create_user(username='custom-field-denied', password='pass')
        UserAccessProfile.objects.create(user=staff, tenant=self.org, role=UserAccessProfile.Role.TENANT_STAFF)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(reverse('custom-field-inline-create'), {'target_model': 'product'}).status_code, 403)

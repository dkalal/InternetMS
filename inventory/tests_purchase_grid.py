from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from products.models import Product, ProductCategory, UnitOfMeasure
from users.models import Organization, UserAccessProfile

from .forms import PurchaseLineForm
from .models import Purchase, PurchaseLine, Supplier


User = get_user_model()


class CompactPurchaseGridTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name='Grid Tenant', slug='purchase-grid-tenant')
        self.other_organization = Organization.objects.create(name='Other Grid Tenant', slug='other-purchase-grid-tenant')
        self.admin = User.objects.create_user(username='purchase-grid-admin', password='pass')
        UserAccessProfile.objects.create(
            user=self.admin,
            tenant=self.organization,
            role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.unit = UnitOfMeasure.objects.create(
            organization=self.organization, tenant=self.organization, name='Unit', symbol='pc'
        )
        self.category = ProductCategory.objects.create(
            organization=self.organization,
            tenant=self.organization,
            name='Grid Equipment',
            default_unit=self.unit,
            measure_unit='pc',
        )
        self.product = self.make_product(self.organization, self.category, self.unit, 'Router A', 'GRID-RTR-A')
        self.unselected_product = self.make_product(
            self.organization, self.category, self.unit, 'Router B', 'GRID-RTR-B'
        )
        other_unit = UnitOfMeasure.objects.create(
            organization=self.other_organization, tenant=self.other_organization, name='Unit', symbol='pc'
        )
        other_category = ProductCategory.objects.create(
            organization=self.other_organization,
            tenant=self.other_organization,
            name='Private Grid Equipment',
            default_unit=other_unit,
            measure_unit='pc',
        )
        self.other_product = self.make_product(
            self.other_organization, other_category, other_unit, 'Private Router', 'PRIVATE-GRID-RTR'
        )
        self.supplier = Supplier.objects.create(
            organization=self.organization,
            tenant=self.organization,
            company_name='Grid Supplier',
            created_by=self.admin,
        )
        self.client.login(username='purchase-grid-admin', password='pass')

    def make_product(self, organization, category, unit, name, sku):
        return Product.objects.create(
            organization=organization,
            tenant=organization,
            catalog_category=category,
            sales_unit=unit,
            name=name,
            sku=sku,
            item_type=Product.ItemType.PHYSICAL,
            track_stock=True,
            is_active=True,
            quantity=Decimal('0.000000'),
            stock=0,
            buying_price=Decimal('100.000000'),
            selling_price=Decimal('150.00'),
        )

    def payload(self, product):
        return {
            'action': 'save_review',
            'supplier': self.supplier.pk,
            'reference_number': 'GRID-PURCHASE-001',
            'auto_generated_reference': '',
            'purchase_date': date.today().isoformat(),
            'notes': '',
            'lines-TOTAL_FORMS': '1',
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
            'lines-0-product': product.pk,
            'lines-0-quantity': '2',
            'lines-0-unit_cost': '100',
            'lines-0-batch_reference': '',
            'lines-0-expiry_date': '',
            'lines-0-serial_numbers': '',
        }

    def test_unbound_form_does_not_load_the_tenant_catalog(self):
        form = PurchaseLineForm(organization=self.organization)
        self.assertFalse(form.fields['product'].queryset.exists())
        self.assertNotIn('data-searchable-select', form.fields['product'].widget.attrs)

    def test_submitted_tenant_product_is_loaded_and_validated(self):
        form = PurchaseLineForm(
            data={
                'product': self.product.pk,
                'quantity': '2',
                'unit_cost': '100',
                'batch_reference': '',
                'expiry_date': '',
                'serial_numbers': '',
            },
            organization=self.organization,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(list(form.fields['product'].queryset), [self.product])

    def test_remote_selected_product_saves_through_the_normal_formset(self):
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(self.product))
        purchase = Purchase.objects.get(reference_number='GRID-PURCHASE-001')
        self.assertRedirects(response, reverse('inventory:purchase_detail', args=[purchase.pk]))
        self.assertEqual(purchase.lines.get().product, self.product)

    def test_cross_tenant_product_remains_invalid(self):
        response = self.client.post(reverse('inventory:purchase_create'), self.payload(self.other_product))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select a valid choice')
        self.assertFalse(Purchase.objects.filter(tenant=self.organization).exists())

    def test_edit_workspace_embeds_only_selected_product_metadata(self):
        purchase = Purchase.objects.create(
            organization=self.organization,
            tenant=self.organization,
            supplier=self.supplier,
            reference_number='GRID-EDIT-001',
            purchase_date=date.today(),
            created_by=self.admin,
        )
        PurchaseLine.objects.create(
            purchase=purchase,
            product=self.product,
            quantity=Decimal('2.000000'),
            unit_cost=Decimal('100.000000'),
        )

        response = self.client.get(reverse('inventory:purchase_edit', args=[purchase.pk]))

        self.assertContains(response, 'data-product-search-url=')
        self.assertContains(response, 'data-purchase-product-select=')
        self.assertContains(response, 'data-open-bulk-products')
        self.assertContains(response, 'data-bulk-product-dialog')
        self.assertContains(response, 'data-purchase-workspace')
        self.assertContains(response, 'data-purchase-items-panel')
        self.assertContains(response, 'pb-64 sm:pb-24')
        self.assertContains(response, 'GRID-RTR-A')
        self.assertNotContains(response, 'GRID-RTR-B')
        self.assertNotContains(response, 'PRIVATE-GRID-RTR')

    def test_remote_combobox_reliably_hides_its_backing_product_select(self):
        script_path = Path(__file__).parent / 'static/inventory/js/inventory-ui.js'
        script = script_path.read_text(encoding='utf-8')

        self.assertIn('select.hidden = true;', script)
        self.assertIn('select.style.setProperty("display", "none", "important");', script)
        self.assertIn('select.setAttribute("aria-hidden", "true");', script)

    def test_purchase_items_panel_does_not_clip_remote_search_results(self):
        template = (Path(__file__).parent.parent / 'templates/inventory/purchase_form.html').read_text(encoding='utf-8')

        self.assertIn('class="jims-panel" data-purchase-items-panel', template)
        self.assertNotIn('class="jims-panel overflow-hidden" data-purchase-items-panel', template)

    def test_high_volume_workspace_saves_many_remote_selected_products_as_one_draft(self):
        products = [self.product, self.unselected_product]
        for number in range(3, 26):
            products.append(self.make_product(
                self.organization,
                self.category,
                self.unit,
                f'Bulk Product {number:02d}',
                f'GRID-BULK-{number:02d}',
            ))
        payload = {
            'action': 'save_review',
            'supplier': self.supplier.pk,
            'reference_number': 'GRID-BULK-001',
            'auto_generated_reference': '',
            'purchase_date': date.today().isoformat(),
            'notes': '',
            'lines-TOTAL_FORMS': str(len(products)),
            'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0',
            'lines-MAX_NUM_FORMS': '1000',
        }
        for index, product in enumerate(products):
            payload.update({
                f'lines-{index}-product': product.pk,
                f'lines-{index}-quantity': '2',
                f'lines-{index}-unit_cost': '100',
                f'lines-{index}-batch_reference': '',
                f'lines-{index}-expiry_date': '',
                f'lines-{index}-serial_numbers': '',
            })

        response = self.client.post(reverse('inventory:purchase_create'), payload)

        purchase = Purchase.objects.get(reference_number='GRID-BULK-001')
        self.assertRedirects(response, reverse('inventory:purchase_detail', args=[purchase.pk]))
        self.assertEqual(purchase.lines.count(), 25)
        self.assertEqual(purchase.total_cost, Decimal('5000.00'))
        self.assertEqual(purchase.status, Purchase.Status.DRAFT)

    def test_bulk_picker_appends_every_selected_product(self):
        script = (Path(__file__).parent / 'static/inventory/js/inventory-ui.js').read_text()

        self.assertIn('var addedLine = appendLine(result);', script)
        self.assertNotIn('firstAdded = firstAdded || appendLine(result)', script)

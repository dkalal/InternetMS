from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from customers.models import Customer
from products.models import Product
from services.models import Package
from users.models import Organization, UserAccessProfile

from .models import CustomFieldDefinition, CustomFieldValue
from .services import CustomFieldService


User = get_user_model()


class CustomFieldCoreTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Tenant One", slug="tenant-one")
        self.other_organization = Organization.objects.create(name="Tenant Two", slug="tenant-two")

    def test_create_definition_sets_tenant(self):
        definition = CustomFieldDefinition.objects.create(
            organization=self.organization,
            target_model=CustomFieldDefinition.TargetModel.CUSTOMER,
            key="landmark",
            label="Landmark",
            field_type=CustomFieldDefinition.FieldType.TEXT,
        )

        self.assertEqual(definition.tenant_id, self.organization.id)
        self.assertTrue(definition.is_active)

    def test_duplicate_field_key_is_blocked_per_tenant_and_target(self):
        CustomFieldDefinition.objects.create(
            organization=self.organization,
            target_model=CustomFieldDefinition.TargetModel.CUSTOMER,
            key="landmark",
            label="Landmark",
            field_type=CustomFieldDefinition.FieldType.TEXT,
        )

        with self.assertRaises(IntegrityError):
            CustomFieldDefinition.objects.create(
                organization=self.organization,
                target_model=CustomFieldDefinition.TargetModel.CUSTOMER,
                key="landmark",
                label="Duplicate",
                field_type=CustomFieldDefinition.FieldType.TEXT,
            )

    def test_required_text_number_and_choice_validation(self):
        text_def = CustomFieldDefinition.objects.create(
            organization=self.organization,
            target_model=CustomFieldDefinition.TargetModel.CUSTOMER,
            key="landmark",
            label="Landmark",
            field_type=CustomFieldDefinition.FieldType.TEXT,
            required=True,
        )
        number_def = CustomFieldDefinition.objects.create(
            organization=self.organization,
            target_model=CustomFieldDefinition.TargetModel.CUSTOMER,
            key="pole_number",
            label="Pole Number",
            field_type=CustomFieldDefinition.FieldType.NUMBER,
            required=True,
        )
        choice_def = CustomFieldDefinition.objects.create(
            organization=self.organization,
            target_model=CustomFieldDefinition.TargetModel.CUSTOMER,
            key="router_color",
            label="Router Color",
            field_type=CustomFieldDefinition.FieldType.CHOICE,
            required=True,
            choices="Red\nBlue",
        )

        with self.assertRaises(ValidationError):
            CustomFieldService.validate_custom_field_input(
                target_model="customer",
                submitted_data={"cf_landmark": ""},
                organization=self.organization,
            )

        with self.assertRaises(ValidationError):
            CustomFieldService.validate_custom_field_input(
                target_model="customer",
                submitted_data={"cf_pole_number": "abc"},
                organization=self.organization,
            )

        with self.assertRaises(ValidationError):
            CustomFieldService.validate_custom_field_input(
                target_model="customer",
                submitted_data={"cf_router_color": "Green"},
                organization=self.organization,
            )

        cleaned = CustomFieldService.validate_custom_field_input(
            target_model="customer",
            submitted_data={
                "cf_landmark": "Main Road",
                "cf_pole_number": "12",
                "cf_router_color": "Red",
            },
            organization=self.organization,
        )
        self.assertEqual(cleaned["cf_landmark"], "Main Road")
        self.assertEqual(str(cleaned["cf_pole_number"]), "12")
        self.assertEqual(cleaned["cf_router_color"], "Red")


class CustomFieldIntegrationTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Tenant One", slug="tenant-one")
        self.other_organization = Organization.objects.create(name="Tenant Two", slug="tenant-two")
        self.admin = User.objects.create_user(username="admin", password="pass")
        self.staff = User.objects.create_user(username="staff", password="pass")
        UserAccessProfile.objects.create(
            user=self.admin,
            tenant=self.organization,
            role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        UserAccessProfile.objects.create(
            user=self.staff,
            tenant=self.organization,
            role=UserAccessProfile.Role.TENANT_STAFF,
        )

        self.customer_field = CustomFieldDefinition.objects.create(
            organization=self.organization,
            target_model=CustomFieldDefinition.TargetModel.CUSTOMER,
            key="landmark",
            label="Landmark",
            field_type=CustomFieldDefinition.FieldType.TEXT,
            show_on_detail=True,
        )
        self.hidden_customer_field = CustomFieldDefinition.objects.create(
            organization=self.organization,
            target_model=CustomFieldDefinition.TargetModel.CUSTOMER,
            key="router_note",
            label="Router Note",
            field_type=CustomFieldDefinition.FieldType.TEXT,
            is_active=False,
        )
        self.other_org_customer_field = CustomFieldDefinition.objects.create(
            organization=self.other_organization,
            target_model=CustomFieldDefinition.TargetModel.CUSTOMER,
            key="other_note",
            label="Other Org Note",
            field_type=CustomFieldDefinition.FieldType.TEXT,
        )
        self.product_field = CustomFieldDefinition.objects.create(
            organization=self.organization,
            target_model=CustomFieldDefinition.TargetModel.PRODUCT,
            key="color",
            label="Color",
            field_type=CustomFieldDefinition.FieldType.TEXT,
            show_on_detail=True,
        )
        self.package_field = CustomFieldDefinition.objects.create(
            organization=self.organization,
            target_model=CustomFieldDefinition.TargetModel.PACKAGE,
            key="router_note",
            label="Router Note",
            field_type=CustomFieldDefinition.FieldType.TEXT,
            show_on_detail=True,
        )

    def _customer_payload(self, **overrides):
        payload = {
            "name": "Alpha Customer",
            "customer_type": "random",
            "status": Customer.Status.ACTIVE,
            "pricing_tier": Customer.PricingTier.RETAIL,
            "email": "alpha@example.com",
            "phone": "+255712345678",
            "address": "Plot 12",
            "location": "Mbezi",
            "ip_address": "",
            "vlan_id": "",
            "tin_number": "",
            "vrn_number": "",
            "packages": [],
            "status_change_reason": "",
            "cf_landmark": "Near the tower",
        }
        payload.update(overrides)
        return payload

    def _product_payload(self, **overrides):
        payload = {
            "name": "Router X",
            "quantity": "10",
            "measure_unit": "Unit",
            "buying_price": "100.00",
            "selling_price": "150.00",
            "retail_price": "",
            "wholesale_price": "",
            "wholesale_min_quantity": "1",
            "allow_wholesale": "",
            "customer": "",
            "is_active": "on",
            "description": "Router with warranty note",
            "category": "hardware",
            "cf_color": "Black",
        }
        payload.update(overrides)
        return payload

    def _package_payload(self, **overrides):
        payload = {
            "name": "Business Fiber",
            "package_type": "indoor",
            "speed": "20 Mbps",
            "monthly_fee": "90000.00",
            "setup_fee": "15000.00",
            "description": "Business package",
            "is_active": "on",
            "cf_router_note": "Install near the rack",
        }
        payload.update(overrides)
        return payload

    def test_staff_cannot_manage_custom_field_definitions(self):
        self.client.login(username="staff", password="pass")
        response = self.client.get(reverse("custom-field-list"))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_manage_custom_field_definitions_and_deactivate(self):
        self.client.login(username="admin", password="pass")
        response = self.client.get(reverse("custom-field-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Landmark")

        response = self.client.post(reverse("custom-field-deactivate", args=[self.customer_field.id]))
        self.assertEqual(response.status_code, 302)
        self.customer_field.refresh_from_db()
        self.assertFalse(self.customer_field.is_active)

    def test_super_admin_must_enter_explicit_audited_support_context(self):
        super_admin = User.objects.create_superuser(username="root", password="pass")
        UserAccessProfile.objects.create(
            user=super_admin,
            tenant=None,
            role=UserAccessProfile.Role.SUPER_ADMIN,
        )

        self.client.login(username="root", password="pass")
        response = self.client.get(reverse("custom-field-list"))

        self.assertEqual(response.status_code, 403)

        selector_response = self.client.get(reverse("start_support_access"))
        self.assertEqual(selector_response.status_code, 200)
        self.assertContains(selector_response, "audited tenant support mode")
        self.assertContains(selector_response, self.organization.name)

        enter_response = self.client.post(
            reverse("start_support_access"),
            {"tenant_id": self.organization.pk, "reason": "Investigate customer configuration"},
        )
        self.assertEqual(enter_response.status_code, 302)
        self.assertEqual(self.client.get(reverse("custom-field-list")).status_code, 200)

    def test_inactive_and_other_tenant_fields_do_not_appear_on_forms(self):
        self.client.login(username="admin", password="pass")
        response = self.client.get(reverse("customer-create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Landmark")
        self.assertNotContains(response, "Router Note")
        self.assertNotContains(response, "Other Org Note")

    def test_customer_create_update_and_detail_store_custom_fields(self):
        self.client.login(username="admin", password="pass")

        create_response = self.client.post(reverse("customer-create"), self._customer_payload())
        self.assertEqual(create_response.status_code, 302)

        customer = Customer.objects.get(name="Alpha Customer")
        value = CustomFieldValue.objects.get(field_definition=self.customer_field, object_id=str(customer.id))
        self.assertEqual(value.value_text, "Near the tower")

        detail_response = self.client.get(reverse("customer-detail", args=[customer.id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Near the tower")

        update_response = self.client.post(
            reverse("customer-update", args=[customer.id]),
            self._customer_payload(name="Alpha Customer", cf_landmark="Behind the office"),
        )
        self.assertEqual(update_response.status_code, 302)
        value.refresh_from_db()
        self.assertEqual(value.value_text, "Behind the office")

    def test_product_create_update_and_detail_store_custom_fields(self):
        self.client.login(username="admin", password="pass")

        create_response = self.client.post(reverse("product-create"), self._product_payload())
        self.assertEqual(create_response.status_code, 302)

        product = Product.objects.get(name="Router X")
        value = CustomFieldValue.objects.get(field_definition=self.product_field, object_id=str(product.id))
        self.assertEqual(value.value_text, "Black")

        detail_response = self.client.get(reverse("product-detail", args=[product.id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Black")

        update_response = self.client.post(
            reverse("product-update", args=[product.id]),
            self._product_payload(name="Router X", cf_color="White"),
        )
        self.assertEqual(update_response.status_code, 302)
        value.refresh_from_db()
        self.assertEqual(value.value_text, "White")

    def test_package_create_update_and_detail_store_custom_fields(self):
        self.client.login(username="admin", password="pass")

        create_response = self.client.post(reverse("package-create"), self._package_payload())
        self.assertEqual(create_response.status_code, 302)

        package = Package.objects.get(name="Business Fiber")
        value = CustomFieldValue.objects.get(field_definition=self.package_field, object_id=str(package.id))
        self.assertEqual(value.value_text, "Install near the rack")

        detail_response = self.client.get(reverse("package-detail", args=[package.id]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Install near the rack")

        update_response = self.client.post(
            reverse("package-update", args=[package.id]),
            self._package_payload(name="Business Fiber", cf_router_note="Install on the wall"),
        )
        self.assertEqual(update_response.status_code, 302)
        value.refresh_from_db()
        self.assertEqual(value.value_text, "Install on the wall")

    def test_inline_custom_field_modal_reopens_with_validation_errors(self):
        self.client.login(username="admin", password="pass")

        response = self.client.post(
            reverse("custom-field-inline-create"),
            {
                "organization": self.organization.id,
                "target_model": "customer",
                "key": "",
                "label": "Landmark",
                "field_type": CustomFieldDefinition.FieldType.TEXT,
                "required": "on",
                "help_text": "",
                "placeholder": "",
                "default_value": "",
                "choices": "",
                "display_order": "1",
                "is_active": "on",
                "show_on_create": "on",
                "show_on_edit": "on",
                "show_on_detail": "on",
                "next": reverse("customer-create"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("cf_modal=", response.url)

        follow_response = self.client.get(response.url)
        self.assertEqual(follow_response.status_code, 200)
        self.assertContains(follow_response, "Add custom field")
        self.assertContains(follow_response, "This field is required.")

    def test_inline_custom_field_modal_returns_field_html_for_save_and_use_now(self):
        self.client.login(username="admin", password="pass")

        response = self.client.post(
            reverse("custom-field-inline-create"),
            {
                "organization": self.organization.id,
                "target_model": "customer",
                "key": "house_number",
                "label": "House Number",
                "field_type": CustomFieldDefinition.FieldType.TEXT,
                "required": "on",
                "help_text": "Where the customer is located",
                "placeholder": "Near the tower",
                "default_value": "",
                "choices": "",
                "display_order": "1",
                "is_active": "on",
                "show_on_create": "on",
                "show_on_edit": "on",
                "show_on_detail": "on",
                "next": reverse("customer-create"),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["definition"]["key"], "house_number")
        self.assertIn("data-field-name=\"cf_house_number\"", data["definition"]["field_html"])

    def test_inline_custom_field_modal_shows_duplicate_key_error(self):
        self.client.login(username="admin", password="pass")

        response = self.client.post(
            reverse("custom-field-inline-create"),
            {
                "organization": self.organization.id,
                "target_model": "customer",
                "key": "landmark",
                "label": "Landmark",
                "field_type": CustomFieldDefinition.FieldType.TEXT,
                "required": "on",
                "help_text": "",
                "placeholder": "",
                "default_value": "",
                "choices": "",
                "display_order": "1",
                "is_active": "on",
                "show_on_create": "on",
                "show_on_edit": "on",
                "show_on_detail": "on",
                "next": reverse("customer-create"),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertIn("key", data["errors"])

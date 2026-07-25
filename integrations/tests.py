from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIRequestFactory
from rest_framework.settings import api_settings
from rest_framework.test import APIClient

from audit.models import AuditLog
from customers.models import Customer
from users.models import Organization, UserAccessProfile

from .models import IntegrationConsumer
from .services import IntegrationBurstThrottle


User = get_user_model()


class IntegrationCustomerApiTests(TestCase):
    def setUp(self):
        self.org1 = Organization.objects.create(name='Org One', slug='org-one')
        self.org2 = Organization.objects.create(name='Org Two', slug='org-two')
        self.user = User.objects.create_user(username='integration-org1', password='pass')
        UserAccessProfile.objects.create(
            user=self.user,
            tenant=self.org1,
            role=UserAccessProfile.Role.TENANT_ADMIN,
        )
        self.consumer = IntegrationConsumer.objects.create(
            user=self.user,
            organization=self.org1,
            name='AssetMS Org One',
        )
        self.token = Token.objects.create(user=self.user)

        self.active_customer = Customer.all_objects.create(
            organization=self.org1,
            tenant=self.org1,
            name='John Active',
            customer_type='internet',
            status=Customer.Status.ACTIVE,
            email='john@example.com',
            phone='+255712345678',
            address='Mikocheni',
            location='Dar es Salaam',
        )
        self.inactive_customer = Customer.all_objects.create(
            organization=self.org1,
            tenant=self.org1,
            name='Jane Inactive',
            customer_type='random',
            status=Customer.Status.INACTIVE,
            email='jane@example.com',
            phone='+255700000000',
            address='Mbezi',
            location='Dar es Salaam',
        )
        self.deleted_customer = Customer.all_objects.create(
            organization=self.org1,
            tenant=self.org1,
            name='Deleted Customer',
            customer_type='internet',
            status=Customer.Status.ACTIVE,
            email='deleted@example.com',
            phone='+255799999999',
            address='Mwenge',
            location='Dar es Salaam',
            is_deleted=True,
        )
        self.other_org_customer = Customer.all_objects.create(
            organization=self.org2,
            tenant=self.org2,
            name='Cross Tenant',
            customer_type='internet',
            status=Customer.Status.ACTIVE,
            email='cross@example.com',
            phone='+255711111111',
            address='Arusha',
            location='Arusha',
        )

        self.client = APIClient()

    def authenticate(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')

    def test_list_requires_authentication(self):
        response = self.client.get('/api/integrations/customers/')
        self.assertEqual(response.status_code, 401)

    def test_alias_list_requires_authentication(self):
        response = self.client.get('/api/customers/')
        self.assertEqual(response.status_code, 401)

    def test_list_returns_only_active_non_deleted_customers_in_consumer_tenant(self):
        self.authenticate()
        response = self.client.get('/api/integrations/customers/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['uuid'], str(self.active_customer.uuid))
        self.assertEqual(
            response.data['results'][0]['display_label'],
            'John Active (+255712345678 | john@example.com)',
        )
        self.assertEqual(
            set(response.data['results'][0].keys()),
            {
                'uuid',
                'full_name',
                'phone',
                'email',
                'address',
                'customer_status',
                'customer_type',
                'created_at',
                'display_label',
                'contact_summary',
            },
        )

    def test_alias_list_returns_the_same_payload_shape(self):
        self.authenticate()
        response = self.client.get('/api/customers/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['uuid'], str(self.active_customer.uuid))

    def test_detail_respects_tenant_scope(self):
        self.authenticate()
        response = self.client.get(f'/api/integrations/customers/{self.active_customer.uuid}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.active_customer.uuid))

        blocked = self.client.get(f'/api/integrations/customers/{self.other_org_customer.uuid}/')
        self.assertEqual(blocked.status_code, 404)

    def test_alias_detail_respects_tenant_scope(self):
        self.authenticate()
        response = self.client.get(f'/api/customers/{self.active_customer.uuid}/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uuid'], str(self.active_customer.uuid))

        blocked = self.client.get(f'/api/customers/{self.other_org_customer.uuid}/')
        self.assertEqual(blocked.status_code, 404)

    def test_search_matches_name_phone_and_email(self):
        self.authenticate()
        response = self.client.get('/api/integrations/customers/', {'search': 'john@example.com'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['full_name'], 'John Active')

        response = self.client.get('/api/integrations/customers/', {'search': 'Mikocheni'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

    def test_write_methods_are_not_allowed(self):
        self.authenticate()
        response = self.client.post('/api/integrations/customers/', {}, format='json')
        self.assertEqual(response.status_code, 403)

    @override_settings(
        REST_FRAMEWORK={
            'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.TokenAuthentication'],
            'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
            'DEFAULT_PAGINATION_CLASS': 'integrations.views.IntegrationPagination',
            'PAGE_SIZE': 50,
            'DEFAULT_THROTTLE_CLASSES': [
                'integrations.services.IntegrationBurstThrottle',
                'integrations.services.IntegrationSustainedThrottle',
            ],
            'DEFAULT_THROTTLE_RATES': {
                'integration_burst': '2/min',
                'integration_sustained': '10/day',
            },
        }
    )
    def test_throttling_applies(self):
        api_settings.reload()
        cache.clear()
        throttle = IntegrationBurstThrottle()
        throttle.rate = '2/min'
        throttle.num_requests, throttle.duration = throttle.parse_rate(throttle.rate)
        factory = APIRequestFactory()
        request = factory.get('/api/integrations/customers/')
        request.user = self.user

        self.assertTrue(throttle.allow_request(request, None))
        self.assertTrue(throttle.allow_request(request, None))
        self.assertFalse(throttle.allow_request(request, None))
        api_settings.reload()

    def test_access_is_audited(self):
        self.authenticate()
        response = self.client.get('/api/integrations/customers/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(
                organization=self.org1,
                action='integration.customer_api.list',
                object_type='IntegrationConsumer',
                object_id=str(self.consumer.id),
            ).exists()
        )

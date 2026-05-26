from rest_framework import generics

from .permissions import IsActiveIntegrationConsumer
from .pagination import IntegrationPagination
from .serializers import IntegrationCustomerSerializer
from .services import (
    IntegrationBurstThrottle,
    IntegrationSustainedThrottle,
    apply_customer_search,
    build_customer_queryset,
    log_customer_api_access,
    resolve_integration_consumer,
)

class IntegrationCustomerListView(generics.ListAPIView):
    serializer_class = IntegrationCustomerSerializer
    permission_classes = [IsActiveIntegrationConsumer]
    throttle_classes = [IntegrationBurstThrottle, IntegrationSustainedThrottle]
    pagination_class = IntegrationPagination
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        consumer = resolve_integration_consumer(self.request)
        queryset = build_customer_queryset(consumer)
        return apply_customer_search(queryset, self.request.query_params.get('search'))

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        record_count = None
        if isinstance(response.data, dict):
            results = response.data.get('results')
            if isinstance(results, list):
                record_count = len(results)
        log_customer_api_access(
            request=request,
            consumer=resolve_integration_consumer(request),
            action='integration.customer_api.list',
            status_code=response.status_code,
            record_count=record_count,
        )
        return response


class IntegrationCustomerDetailView(generics.RetrieveAPIView):
    serializer_class = IntegrationCustomerSerializer
    permission_classes = [IsActiveIntegrationConsumer]
    throttle_classes = [IntegrationBurstThrottle, IntegrationSustainedThrottle]
    lookup_field = 'uuid'
    lookup_url_kwarg = 'uuid'
    http_method_names = ['get', 'head', 'options']

    def get_queryset(self):
        consumer = resolve_integration_consumer(self.request)
        return build_customer_queryset(consumer)

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        log_customer_api_access(
            request=request,
            consumer=resolve_integration_consumer(request),
            action='integration.customer_api.detail',
            status_code=response.status_code,
            record_count=1 if response.status_code == 200 else 0,
            customer_uuid=kwargs.get('uuid'),
        )
        return response

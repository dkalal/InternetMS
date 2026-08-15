from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .permissions import IsActiveIntegrationConsumer, IsActiveIntegrationConsumerWriter
from .pagination import IntegrationPagination
from .serializers import ExternalAssetSnapshotSerializer, IntegrationCustomerSerializer
from .services import (
    IntegrationBurstThrottle,
    IntegrationSustainedThrottle,
    apply_customer_search,
    build_customer_asset_target_queryset,
    build_customer_queryset,
    log_customer_api_access,
    replace_customer_asset_snapshot,
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


class IntegrationCustomerAssetSnapshotView(APIView):
    permission_classes = [IsActiveIntegrationConsumerWriter]
    throttle_classes = [IntegrationBurstThrottle, IntegrationSustainedThrottle]
    http_method_names = ['put', 'options']

    def put(self, request, uuid):
        consumer = resolve_integration_consumer(request)
        customer = get_object_or_404(
            build_customer_asset_target_queryset(consumer, customer_uuid=uuid)
        )
        raw_assets = request.data.get('assets') if isinstance(request.data, dict) else None
        if not isinstance(raw_assets, list):
            return Response({'assets': ['This field must be a list.']}, status=status.HTTP_400_BAD_REQUEST)
        if len(raw_assets) > 1000:
            return Response({'assets': ['A snapshot cannot exceed 1000 assets.']}, status=status.HTTP_400_BAD_REQUEST)
        serializer = ExternalAssetSnapshotSerializer(data=raw_assets, many=True)
        serializer.is_valid(raise_exception=True)
        external_ids = [asset['external_uuid'] for asset in serializer.validated_data]
        if len(external_ids) != len(set(external_ids)):
            return Response({'assets': ['Duplicate external_uuid values are not allowed.']}, status=status.HTTP_400_BAD_REQUEST)
        result = replace_customer_asset_snapshot(
            consumer=consumer,
            customer=customer,
            assets=serializer.validated_data,
        )
        log_customer_api_access(
            request=request,
            consumer=consumer,
            action='integration.customer_assets.replace',
            status_code=status.HTTP_200_OK,
            record_count=result['total'],
            customer_uuid=uuid,
        )
        return Response(result, status=status.HTTP_200_OK)

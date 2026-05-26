"""
Health check views for application monitoring.
"""
from django.http import JsonResponse
from django.db import connection
from django.core.checks import run_checks
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


def health_check(request):
    """
    Basic health check endpoint.
    Returns 200 if application is running, 503 if there are issues.
    """
    try:
        # Check database connectivity
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        
        return JsonResponse({
            'status': 'healthy',
            'message': 'Application is running',
        }, status=200)
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return JsonResponse({
            'status': 'unhealthy',
            'message': 'Database connection failed',
            'error': str(e) if not settings.DEBUG else None,
        }, status=503)


def readiness_check(request):
    """
    Readiness check for Kubernetes/container orchestration.
    More comprehensive than health check - verifies app is ready to serve requests.
    """
    try:
        # Run Django system checks
        issues = run_checks()
        if issues:
            return JsonResponse({
                'status': 'not_ready',
                'message': 'System checks failed',
                'issues': [str(issue) for issue in issues],
            }, status=503)
        
        # Check database
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        
        return JsonResponse({
            'status': 'ready',
            'message': 'Application is ready to serve requests',
        }, status=200)
    except Exception as e:
        logger.error(f"Readiness check failed: {str(e)}")
        return JsonResponse({
            'status': 'not_ready',
            'message': 'Readiness check failed',
        }, status=503)


def liveness_check(request):
    """
    Liveness check for Kubernetes/container orchestration.
    Minimal check - just verifies the app is running.
    """
    return JsonResponse({
        'status': 'alive',
        'message': 'Application process is alive',
    }, status=200)

from django.apps import AppConfig


class ProductsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'products'

    def ready(self):
        # Register product-image cleanup after Django has loaded all models.
        from . import signals  # noqa: F401

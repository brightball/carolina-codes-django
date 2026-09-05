from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "catalog"
    verbose_name = "Carolina catalog (read-only)"
    default_auto_field = "django.db.models.BigAutoField"

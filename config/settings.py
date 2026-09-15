"""Django settings. Catalog Postgres is never Django's DATABASES default."""

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(50)
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = ["*"]

# No contrib.admin / auth / sessions / contenttypes — those would need tables.
INSTALLED_APPS = [
    "catalog.apps.CatalogConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "catalog.middleware.PolyglotHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# In-memory sqlite is Django's own connection only. It is not carolina_dev.
# Catalog reads go through psycopg + DATABASE_URL (see catalog/db.py).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}


class _NoMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = _NoMigrations()

USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

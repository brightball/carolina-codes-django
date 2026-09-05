import os
import sys

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

if "test" not in sys.argv and os.environ.get("DJANGO_SKIP_REGISTER") != "1":
    from catalog.register import register_with_elixir

    register_with_elixir()

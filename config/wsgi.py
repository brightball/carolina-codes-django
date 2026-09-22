import os
import sys

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()

# Registration uses a multi-second timeout. It runs after the application
# object exists and does not block the worker from accepting connections.
if "test" not in sys.argv and os.environ.get("DJANGO_SKIP_REGISTER") != "1":
    from catalog.register import schedule_registration

    schedule_registration()

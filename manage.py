#!/usr/bin/env python3
"""Django admin. Do not run migrate/makemigrations against the catalog."""
import os
import sys

BLOCKED = {"migrate", "makemigrations", "flush", "sqlmigrate", "syncdb"}


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    cmd = next((a for a in sys.argv[1:] if not a.startswith("-")), "")
    if cmd in BLOCKED:
        sys.stderr.write(
            "refusing django {0}: this API must not alter the shared Postgres catalog.\n"
            "Catalog access is read-only SQL against v1_* views.\n".format(cmd)
        )
        sys.exit(2)
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

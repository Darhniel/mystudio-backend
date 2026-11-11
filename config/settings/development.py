"""
Development settings.

``manage.py`` defaults to this module, so ``python manage.py runserver`` works
without setting ``DJANGO_SETTINGS_MODULE``. PostgreSQL is required: the
connection string comes from ``DATABASE_URL`` in ``.env``.

Only this environment reads ``.env``. Production takes every value from the
process environment.
"""

from pathlib import Path

import environ

# Load the local file before the shared settings evaluate any variable.
environ.Env.read_env(Path(__file__).resolve().parent.parent.parent / ".env")

from django.core.exceptions import ImproperlyConfigured  # noqa: E402

from .base import *  # noqa: E402,F401,F403
from .base import env  # noqa: E402
from .database import require_postgresql  # noqa: E402

DEBUG = env.bool("DEBUG", default=env.bool("DJANGO_DEBUG", default=True))

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=env.list(
        "DJANGO_ALLOWED_HOSTS",
        default=["localhost", "127.0.0.1", "0.0.0.0", "[::1]"],
    ),
)

# PostgreSQL is the database in every environment. Development fails fast
# instead of silently falling back to SQLite, so schema changes are always
# exercised against the database that production uses.
if not DATABASE_URL:  # noqa: F405
    raise ImproperlyConfigured(
        "DATABASE_URL is not set. Development uses PostgreSQL; add a "
        "postgresql:// connection string to .env (see .env.example)."
    )

require_postgresql(DATABASES, "Development")  # noqa: F405

# Development convenience: the frontend runs on a different port.
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=True)

INTERNAL_IPS = ["127.0.0.1"]

# Keep development logs readable and useful.
LOG_LEVEL = env.str("LOG_LEVEL", default=env.str("DJANGO_LOG_LEVEL", default="INFO"))  # noqa: F405
LOGGING["root"]["level"] = LOG_LEVEL  # noqa: F405
LOGGING["loggers"]["django"]["level"] = LOG_LEVEL  # noqa: F405

"""
Base Django settings shared by every environment.

Environment modules (``development``, ``test``, ``production``) import from
this module and override only what differs. Anything deployment-specific is
read from environment variables; see ``.env.example`` for the full list.

Environment variable names follow the plain ``SECRET_KEY`` / ``DEBUG`` /
``ALLOWED_HOSTS`` convention. The legacy ``DJANGO_``-prefixed names remain
accepted as fallbacks so an existing local ``.env`` keeps working.

This module never reads a ``.env`` file. ``development.py`` loads one for local
work; production reads variables exclusively from the process environment so a
stale file on a server cannot silently override the secret manager.
"""

from datetime import timedelta
from email.utils import parseaddr
from pathlib import Path

import environ

from .database import POSTGRES_ENGINE, database_config

# ---------------------------------------------------------------------------
# Paths and environment
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
    CORS_ALLOW_ALL_ORIGINS=(bool, False),
    LOG_LEVEL=(str, "INFO"),
    DB_CONN_MAX_AGE=(int, 60),
)

SECRET_KEY = env.str(
    "SECRET_KEY",
    default=env.str(
        "DJANGO_SECRET_KEY", default="django-insecure-development-only-key"
    ),
)
DEBUG = env.bool("DEBUG", default=env.bool("DJANGO_DEBUG", default=False))
ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS",
    default=env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"]),
)


def _parse_admins(raw):
    """Turn ``Name <email>`` entries into Django's ``ADMINS`` tuples."""
    admins = []
    for entry in raw:
        name, address = parseaddr(entry)
        if address:
            admins.append((name or address, address))
    return admins


# Who receives error mail. Empty by default; production sets it through env.
ADMINS = _parse_admins(env.list("ADMINS", default=[]))
MANAGERS = ADMINS

# ---------------------------------------------------------------------------
# Error reporting
# ---------------------------------------------------------------------------
# Django's debug page shows a cleaned copy of ``settings`` and the request
# environment. The custom filter also hides connection URLs and provider keys,
# so credentials never reach an error page or a captured traceback.
DEFAULT_EXCEPTION_REPORTER_FILTER = (
    "config.exception_filter.SafeExceptionReporterFilter"
)

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
]

PROJECT_APPS = [
    "apps.common",
    "apps.accounts",
    "apps.studios",
    "apps.equipment",
    "apps.bookings",
    "apps.payments",
    "apps.dashboard",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + PROJECT_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
# PostgreSQL is the only supported database. The connection string comes from
# DATABASE_URL: a local PostgreSQL instance, the Supabase Session Pooler
# (IPv4 compatible) or a direct Supabase connection. SSL options travel in the
# URL, e.g. ``?sslmode=require`` (see ``docs/database.md``).
#
# ``development.py`` and ``production.py`` refuse to start without a PostgreSQL
# URL. ``test.py`` replaces this entry with in-memory SQLite when
# TEST_DATABASE_URL is unset, so the test suite needs no database server.
DATABASE_URL = env.str("DATABASE_URL", default="")

if DATABASE_URL:
    DATABASES = {"default": database_config(DATABASE_URL)}
else:
    DATABASES = {"default": {}}

if DATABASES["default"].get("ENGINE") == POSTGRES_ENGINE:
    DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
    # Detect dropped server connections before reusing them. Useful with
    # poolers and managed PostgreSQL that recycle idle sessions.
    DATABASES["default"]["CONN_HEALTH_CHECKS"] = env.bool(
        "DB_CONN_HEALTH_CHECKS", default=True
    )
    # Session pooling (port 5432) supports server-side cursors. Set this to
    # True only when pointing DATABASE_URL at a transaction pooler (port 6543).
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = env.bool(
        "DB_DISABLE_SERVER_SIDE_CURSORS", default=False
    )

    # Bound how long a request can wait for a connection and how long a single
    # statement may run, so a stalled database cannot pin every worker.
    options = DATABASES["default"].setdefault("OPTIONS", {})
    options.setdefault("connect_timeout", env.int("DB_CONNECT_TIMEOUT", default=5))
    statement_timeout = env.int("DB_STATEMENT_TIMEOUT_MS", default=60000)
    if statement_timeout:
        existing = options.get("options", "")
        options["options"] = (
            f"{existing} -c statement_timeout={statement_timeout}".strip()
        )

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static and media files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# ---------------------------------------------------------------------------
# Image hosting (Cloudinary)
# ---------------------------------------------------------------------------
# The application uploads through the official SDK; files themselves are not
# stored on the web server. Credentials are read from the environment and are
# never returned by the API or written to logs.
CLOUDINARY_CLOUD_NAME = env.str("CLOUDINARY_CLOUD_NAME", default="")
CLOUDINARY_API_KEY = env.str("CLOUDINARY_API_KEY", default="")
CLOUDINARY_API_SECRET = env.str("CLOUDINARY_API_SECRET", default="")

# Studio and equipment uploads share one size limit (5 MB).
IMAGE_UPLOAD_MAX_BYTES = env.int("IMAGE_UPLOAD_MAX_BYTES", default=5 * 1024 * 1024)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.common.api.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.common.api.exceptions.api_exception_handler",
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    # Scoped throttles are opt-in per view (the public auth endpoints). Limits
    # are configurable because they depend on the size of the client base.
    "DEFAULT_THROTTLE_RATES": {
        "auth-login": env.str("AUTH_LOGIN_THROTTLE", default="5/min"),
        "auth-refresh": env.str("AUTH_REFRESH_THROTTLE", default="20/min"),
        "auth-verify": env.str("AUTH_VERIFY_THROTTLE", default="20/min"),
    },
}

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# Throttles, sessions and future idempotency keys need a cache. LocMem is fine
# for a single development process; production must point CACHE_URL at a shared
# backend (``redis://host:6379/1``) so every worker sees the same counters.
CACHE_URL = env.str("CACHE_URL", default="")
if CACHE_URL:
    CACHES = {"default": env.cache_url("CACHE_URL")}
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "mystudio",
        }
    }

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=env.int("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", default=30)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=env.int("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=7)
    ),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    # Tolerate small clock differences between application workers.
    "LEEWAY": timedelta(seconds=env.int("JWT_LEEWAY_SECONDS", default=10)),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "TOKEN_OBTAIN_SERIALIZER": "apps.accounts.api.serializers.MyStudioTokenObtainPairSerializer",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "MyStudio API",
    "DESCRIPTION": (
        "API for the MyStudio studio and equipment rental platform: "
        "accounts, studios, equipment, bookings and Paystack payments."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=False)
# Authentication is a bearer token in a header, not a cookie, so browsers must
# not send credentials with cross-origin requests.
CORS_ALLOW_CREDENTIALS = False
# Only the API is meant to be called from the frontend; keep CORS headers off
# the admin site and documentation pages.
CORS_URLS_REGEX = r"^/api/.*$"
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[]),
)

# ---------------------------------------------------------------------------
# API documentation
# ---------------------------------------------------------------------------
# Public while developing. Production defaults to staff-only access; the
# schema is not a secret, but it should not advertise the surface publicly.
API_DOCS_PUBLIC = env.bool("API_DOCS_PUBLIC", default=True)

# ---------------------------------------------------------------------------
# Business rules
# ---------------------------------------------------------------------------
# The platform quotes and settles in Nigerian Naira. Money is always stored as
# a Decimal; Paystack's kobo conversion belongs to the payments app.
MYSTUDIO_CURRENCY = "NGN"
MYSTUDIO_CURRENCY_SYMBOL = "₦"
MYSTUDIO_TIME_ZONE = "Africa/Lagos"

API_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
# Django 6.1 configures mail transport through MAILERS. Local development and
# tests write to the console/in-memory; production switches to SMTP.
MAILERS = {
    "default": {
        "BACKEND": env.str(
            "MAILERS_DEFAULT_BACKEND",
            default="django.core.mail.backends.console.EmailBackend",
        ),
    },
}
DEFAULT_FROM_EMAIL = env.str(
    "DEFAULT_FROM_EMAIL", default="MyStudio <no-reply@mystudio.ng>"
)
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = env.str("LOG_LEVEL", default=env.str("DJANGO_LOG_LEVEL", default="INFO"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
        "verbose": {
            "format": "{levelname} {asctime} {name} pid={process:d} {message}",
            "style": "{",
        },
    },
    "filters": {
        # Masks credentials and tokens before any handler writes a record.
        "redact_secrets": {"()": "config.logging_filters.SensitiveDataFilter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "filters": ["redact_secrets"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django.db.backends": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

# ---------------------------------------------------------------------------
# Paystack Integration
# ---------------------------------------------------------------------------
PAYSTACK_SECRET_KEY = env.str("PAYSTACK_SECRET_KEY", default="sk_test_placeholder")
PAYSTACK_WEBHOOK_SECRET = env.str(
    "PAYSTACK_WEBHOOK_SECRET", default=PAYSTACK_SECRET_KEY
)
PAYSTACK_PUBLIC_KEY = env.str("PAYSTACK_PUBLIC_KEY", default="pk_test_placeholder")
PAYSTACK_CALLBACK_URL = env.str(
    "PAYSTACK_CALLBACK_URL",
    default="http://localhost:3000/payment/callback",
)
PAYSTACK_ENVIRONMENT = env.str("PAYSTACK_ENVIRONMENT", default="test")
"""
Production settings.

Every value that differs between deployments comes from the environment.
Missing critical configuration fails fast at startup rather than at runtime.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env
from .database import require_postgresql

DEBUG = False

# ---------------------------------------------------------------------------
# Required configuration
# ---------------------------------------------------------------------------
SECRET_KEY = env.str("SECRET_KEY", default=env.str("DJANGO_SECRET_KEY", default=""))
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be set in production.")
if SECRET_KEY.startswith("django-insecure-"):
    raise ImproperlyConfigured(
        "SECRET_KEY must be a real secret in production, not the development default."
    )

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS", default=env.list("DJANGO_ALLOWED_HOSTS", default=[])
)
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be set in production.")

# ``base.py`` already built DATABASES from DATABASE_URL (including the SSL
# defaults for Supabase hosts). Production only validates that a PostgreSQL
# URL was actually provided; the raw URL is never printed.
if not DATABASE_URL:  # noqa: F405
    raise ImproperlyConfigured("DATABASE_URL must be set in production.")
require_postgresql(DATABASES, "Production")  # noqa: F405

# ---------------------------------------------------------------------------
# Serving and security
# ---------------------------------------------------------------------------
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405

STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[]),
)
if any(not origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured("CSRF_TRUSTED_ORIGINS must use https in production.")

# API documentation is staff-only in production unless explicitly opened up.
API_DOCS_PUBLIC = env.bool("API_DOCS_PUBLIC", default=False)

# Image hosting is mandatory in production: studio and equipment images are
# stored in Cloudinary, not in the local media directory.
if not CLOUDINARY_CLOUD_NAME:  # noqa: F405
    raise ImproperlyConfigured("CLOUDINARY_CLOUD_NAME must be set in production.")
if not CLOUDINARY_API_KEY:  # noqa: F405
    raise ImproperlyConfigured("CLOUDINARY_API_KEY must be set in production.")
if not CLOUDINARY_API_SECRET:  # noqa: F405
    raise ImproperlyConfigured("CLOUDINARY_API_SECRET must be set in production.")

# In production the frontend origin must be configured explicitly.
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
if not CORS_ALLOWED_ORIGINS:
    raise ImproperlyConfigured("CORS_ALLOWED_ORIGINS must be set in production.")

# ---------------------------------------------------------------------------
# Email (transactional mail is sent through an SMTP provider)
# ---------------------------------------------------------------------------
MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.smtp.EmailBackend",
        "OPTIONS": {
            "host": env.str("EMAIL_HOST"),
            "port": env.int("EMAIL_PORT", default=587),
            "username": env.str("EMAIL_HOST_USER", default=""),
            "password": env.str("EMAIL_HOST_PASSWORD", default=""),
            "use_tls": env.bool("EMAIL_USE_TLS", default=True),
        },
    },
}

# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------
SENTRY_DSN = env.str("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk

    from config.logging_filters import scrub_event

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=env.str("SENTRY_ENVIRONMENT", default="production"),
        release=env.str("SENTRY_RELEASE", default=None),
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
        send_default_pii=False,
        # The log filter only protects log handlers; Sentry receives the
        # exception object directly, so scrub it on the way out too.
        before_send=scrub_event,
    )

LOG_LEVEL = env.str("LOG_LEVEL", default=env.str("DJANGO_LOG_LEVEL", default="INFO"))  # noqa: F405
LOGGING["root"]["level"] = LOG_LEVEL  # noqa: F405
LOGGING["loggers"]["django"]["level"] = LOG_LEVEL  # noqa: F405

# Production logs include the process id (useful when several workers share a
# log stream). The ``redact_secrets`` filter is inherited from ``base.py``.
LOGGING["handlers"]["console"]["formatter"] = "verbose"  # noqa: F405

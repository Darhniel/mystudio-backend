"""Database URL parsing for managed PostgreSQL providers.

The platform uses a plain ``DATABASE_URL`` (django-environ) and Django's
standard PostgreSQL backend. No provider-specific SDK is involved.

Supabase notes:

* The project connection string may point at the direct database host
  (``db.<ref>.supabase.co``), the Session Pooler
  (``...pooler.supabase.com:5432``) or the Transaction Pooler (port ``6543``).
  The Session Pooler is the recommended choice because it is reachable over
  IPv4 and stays compatible with Django's persistent connections.
* Supabase requires TLS. A connection string normally carries
  ``?sslmode=require``; if a Supabase host is detected without an explicit
  ``sslmode``, this module adds ``require`` so credentials are never sent
  unencrypted.
"""

from django.core.exceptions import ImproperlyConfigured

import environ

#: Django's PostgreSQL backend, the only database used outside the test harness.
POSTGRES_ENGINE = "django.db.backends.postgresql"

#: Host suffixes used by Supabase direct connections and both poolers.
SUPABASE_HOST_SUFFIXES = (".supabase.co", ".supabase.com")

#: libpq SSL modes accepted by psycopg.
POSTGRES_SSL_MODES = frozenset(
    {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
)


def is_supabase_host(host: str | None) -> bool:
    """Return True when ``host`` belongs to Supabase."""
    return bool(host) and host.lower().strip().endswith(SUPABASE_HOST_SUFFIXES)


def database_config(url: str) -> dict:
    """Convert a database URL into a Django ``DATABASES`` entry.

    Query-string parameters supported by libpq (for example ``sslmode``) are
    forwarded to psycopg through ``OPTIONS``. Values parsed from the URL are
    never logged or echoed back by this function.
    """
    config = environ.Env.db_url_config(url)
    options = config.setdefault("OPTIONS", {})

    if is_supabase_host(config.get("HOST")):
        options.setdefault("sslmode", "require")

    return config


def require_postgresql(databases: dict, environment: str) -> None:
    """Fail fast unless ``DATABASE_URL`` produced a PostgreSQL configuration.

    The message intentionally contains no host, user or password, so a startup
    failure cannot leak credentials into logs.
    """
    engine = databases.get("default", {}).get("ENGINE")
    if engine != POSTGRES_ENGINE:
        raise ImproperlyConfigured(
            f"{environment} requires a PostgreSQL database. Set DATABASE_URL to a "
            "postgresql:// connection string (Supabase Session Pooler supported)."
        )

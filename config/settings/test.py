"""
Test settings used by pytest.

PostgreSQL is the target database. Provide ``TEST_DATABASE_URL`` to run the
suite against a dedicated test project/branch; without it the suite falls back
to SQLite so tests remain runnable on a machine without a database server.

Never point ``TEST_DATABASE_URL`` at the production Supabase project: the test
runner creates and destroys its own schema.
"""

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env
from .database import database_config

DEBUG = False

SECRET_KEY = "test-secret-key-not-used-in-any-deployment"

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Fast password hashing: tests never need the production hash cost.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

MAILERS = {
    "default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
}

if env.str("TEST_DATABASE_URL", default=""):
    DATABASES = {"default": database_config(env.str("TEST_DATABASE_URL"))}
    # Do not keep test connections open after the run; Django must be able to
    # drop the test database cleanly (also relevant for pooled connections).
    DATABASES["default"]["CONN_MAX_AGE"] = 0
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "test.sqlite3",
            "TEST": {"NAME": ":memory:"},
        }
    }

# Schema generation in tests should be fast and quiet.
SPECTACULAR_SETTINGS["SERVE_INCLUDE_SCHEMA"] = False  # noqa: F405

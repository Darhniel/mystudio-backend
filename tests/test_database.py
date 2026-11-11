import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.database import (
    database_config,
    is_supabase_host,
    require_postgresql,
)

SESSION_POOLER_URL = (
    "postgresql://postgres.abcdefghijkl:super-secret"
    "@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"
)


def test_session_pooler_url_is_parsed():
    config = database_config(SESSION_POOLER_URL)

    assert config["ENGINE"] == "django.db.backends.postgresql"
    assert config["HOST"] == "aws-0-eu-west-1.pooler.supabase.com"
    assert config["PORT"] == 5432
    assert config["USER"] == "postgres.abcdefghijkl"
    assert config["NAME"] == "postgres"
    assert config["OPTIONS"]["sslmode"] == "require"


def test_transaction_pooler_port_is_preserved():
    config = database_config(
        "postgresql://postgres.ref:secret@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"
    )

    assert config["PORT"] == 6543
    assert config["OPTIONS"]["sslmode"] == "require"


def test_supabase_host_defaults_to_ssl_require():
    config = database_config(
        "postgresql://postgres.ref:secret@db.ref.supabase.co:5432/postgres"
    )

    assert config["OPTIONS"]["sslmode"] == "require"


def test_explicit_ssl_mode_is_preserved():
    config = database_config(SESSION_POOLER_URL.replace("require", "verify-full"))

    assert config["OPTIONS"]["sslmode"] == "verify-full"


def test_non_supabase_host_does_not_force_ssl():
    config = database_config("postgresql://user:password@localhost:5432/mystudio")

    assert "sslmode" not in config["OPTIONS"]


def test_url_encoded_password_is_decoded():
    config = database_config(
        "postgresql://postgres.ref:p%40ss%3Aword"
        "@aws-0-eu-west-1.pooler.supabase.com:5432/postgres"
    )

    assert config["PASSWORD"] == "p@ss:word"


@pytest.mark.parametrize(
    "host",
    [
        "db.abcdefghijkl.supabase.co",
        "aws-0-eu-west-1.pooler.supabase.com",
        "aws-1-us-east-1.pooler.supabase.com",
        "DB.ABCDEFGHIJKLM.SUPABASE.CO",
    ],
)
def test_supabase_hosts_are_detected(host):
    assert is_supabase_host(host) is True


@pytest.mark.parametrize("host", ["localhost", "db.example.com", "", None])
def test_non_supabase_hosts_are_not_detected(host):
    assert is_supabase_host(host) is False


def test_require_postgresql_accepts_a_postgresql_config():
    config = database_config("postgresql://user:secret@localhost:5432/mystudio")

    require_postgresql({"default": config}, "Development")


@pytest.mark.parametrize(
    "engine",
    ["django.db.backends.sqlite3", "", None],
)
def test_require_postgresql_rejects_other_databases(engine):
    databases = {"default": {"ENGINE": engine}}

    with pytest.raises(ImproperlyConfigured, match="PostgreSQL"):
        require_postgresql(databases, "Development")


def test_require_postgresql_does_not_echo_credentials():
    databases = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": "postgresql://user:secret@host/db",
        }
    }

    with pytest.raises(ImproperlyConfigured) as exc_info:
        require_postgresql(databases, "Production")

    assert "secret" not in str(exc_info.value)
    assert "host" not in str(exc_info.value)

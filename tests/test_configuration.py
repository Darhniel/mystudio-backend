"""Checks for environment, security and database configuration."""

from django.conf import settings

from config.exception_filter import SafeExceptionReporterFilter

REQUIRED_ENV_VARS = {
    "SECRET_KEY",
    "DEBUG",
    "ALLOWED_HOSTS",
    "DATABASE_URL",
    "CORS_ALLOWED_ORIGINS",
    "CSRF_TRUSTED_ORIGINS",
    "PAYSTACK_PUBLIC_KEY",
    "PAYSTACK_SECRET_KEY",
    "CLOUDINARY_CLOUD_NAME",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
    "RESEND_API_KEY",
}


def _declared_env_names() -> set[str]:
    lines = (settings.BASE_DIR / ".env.example").read_text(encoding="utf-8").splitlines()
    names = set()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        names.add(stripped.split("=", 1)[0].strip())
    return names


def test_env_example_declares_every_required_variable():
    assert REQUIRED_ENV_VARS <= _declared_env_names()


def test_real_env_file_is_gitignored():
    lines = {
        line.strip()
        for line in (settings.BASE_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    }

    assert ".env" in lines
    assert "!.env.example" in lines


def test_exception_filter_masks_database_url():
    reporter_filter = SafeExceptionReporterFilter()

    assert (
        reporter_filter.cleanse_setting(
            "DATABASE_URL", "postgresql://user:secret@db.example.com:5432/mystudio"
        )
        == reporter_filter.cleansed_substitute
    )


def test_exception_filter_masks_nested_database_password():
    reporter_filter = SafeExceptionReporterFilter()

    cleansed = reporter_filter.cleanse_setting(
        "DATABASES",
        {"default": {"PASSWORD": "super-secret", "NAME": "mystudio"}},
    )

    assert cleansed["default"]["PASSWORD"] == reporter_filter.cleansed_substitute
    assert cleansed["default"]["NAME"] == "mystudio"


def test_exception_filter_leaves_non_secret_settings_visible():
    reporter_filter = SafeExceptionReporterFilter()

    assert reporter_filter.cleanse_setting("TIME_ZONE", "Africa/Lagos") == "Africa/Lagos"


def test_gitignore_covers_env_variants_and_key_material():
    lines = {
        line.strip()
        for line in (settings.BASE_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    }

    for pattern in (".env", ".env.*", "!.env.example", "*.key", "secrets*.json"):
        assert pattern in lines


def test_shared_settings_never_read_a_dotenv_file():
    source = (settings.BASE_DIR / "config" / "settings" / "base.py").read_text(
        encoding="utf-8"
    )

    assert "read_env" not in source


def test_production_does_not_load_a_dotenv_file():
    source = (settings.BASE_DIR / "config" / "settings" / "production.py").read_text(
        encoding="utf-8"
    )

    assert "read_env" not in source


def test_auth_throttles_and_cache_are_configured():
    assert set(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]) == {
        "auth-login",
        "auth-refresh",
        "auth-verify",
    }
    assert settings.CACHES["default"]["BACKEND"]


def test_jwt_leeway_tolerates_clock_skew():
    from datetime import timedelta

    assert settings.SIMPLE_JWT["LEEWAY"] == timedelta(seconds=10)


def test_api_docs_are_public_by_default_in_the_shared_settings():
    assert settings.API_DOCS_PUBLIC is True

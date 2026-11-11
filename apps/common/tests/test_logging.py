import logging

from django.conf import settings

from config.logging_filters import REDACTED, SensitiveDataFilter, redact


def test_database_url_credentials_are_redacted():
    message = (
        "connecting to postgresql://postgres:sup3r-secret@"
        "db.example.com:5432/postgres failed"
    )

    result = redact(message)

    assert "sup3r-secret" not in result
    assert "postgresql://***:***@db.example.com:5432/postgres" in result


def test_bearer_token_is_redacted():
    result = redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature")

    assert "eyJhbGciOiJIUzI1NiJ9" not in result
    assert "Bearer ***" in result


def test_password_and_token_pairs_are_redacted():
    assert redact("login password=hunter2") == "login password=***"
    assert redact("token: abc123") == "token: ***"
    assert redact('{"refresh": "secret-token"}') == '{"refresh": ***}'


def test_configured_secret_key_value_is_redacted():
    result = redact(f"unexpected value {settings.SECRET_KEY} in a message")

    assert settings.SECRET_KEY not in result
    assert REDACTED in result


def test_ordinary_messages_are_unchanged():
    message = "GET /api/v1/health/ 200 12ms"

    assert redact(message) == message


def test_filter_redacts_a_formatted_record():
    record = logging.LogRecord(
        name="mystudio.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="login attempt password=%s",
        args=("hunter2",),
        exc_info=None,
    )

    assert SensitiveDataFilter().filter(record) is True
    assert record.getMessage() == "login attempt password=***"
    assert record.args == ()


def test_console_handler_uses_the_redaction_filter():
    assert settings.LOGGING["handlers"]["console"]["filters"] == ["redact_secrets"]


def test_filter_redacts_exception_tracebacks():
    """Tracebacks are formatted by the filter, not the formatter."""
    try:
        raise RuntimeError(
            "connection postgresql://user:sup3r-secret@db.example.com:5432/mystudio "
            "failed token=abc123"
        )
    except RuntimeError:
        import sys

        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="mystudio.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="unhandled request",
        args=(),
        exc_info=exc_info,
    )

    assert SensitiveDataFilter().filter(record) is True
    assert record.exc_text is not None
    assert "sup3r-secret" not in record.exc_text
    assert "abc123" not in record.exc_text
    assert REDACTED in record.exc_text


def test_scrub_event_redacts_sentry_payloads():
    from config.logging_filters import scrub_event

    event = {
        "message": "token=abc123",
        "exception": {
            "values": [
                {"value": "postgresql://user:sup3r-secret@db.example.com:5432/db"}
            ]
        },
        "logentry": {"message": "password=hunter2"},
    }

    scrubbed = scrub_event(event)

    assert "abc123" not in scrubbed["message"]
    assert "sup3r-secret" not in scrubbed["exception"]["values"][0]["value"]
    assert "hunter2" not in scrubbed["logentry"]["message"]

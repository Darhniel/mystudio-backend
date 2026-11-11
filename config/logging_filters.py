"""Filters that keep credentials out of log output.

Django's default logging does not print settings, but connection strings,
tokens or provider keys can still reach a log line through an exception
message, a management command or a third-party library. Every console handler
runs :class:`SensitiveDataFilter`, which masks:

* credentials embedded in database/Redis URLs;
* bearer tokens;
* ``password=``, ``token:`` and similar key/value pairs;
* the literal values of sensitive settings (``SECRET_KEY``, ``DATABASE_URL``,
  provider secrets) wherever they appear.

Uncaught exception *tracebacks* are rendered and redacted here as well, because
log formatters would otherwise write ``exc_text`` after the filter runs. The
same :func:`redact` helper is exposed as ``scrub_event`` for Sentry.
"""

import logging
import re
import traceback

#: Replacement used for every masked value.
REDACTED = "***"

#: Settings whose values must never appear in a log record.
SECRET_SETTINGS = (
    "SECRET_KEY",
    "DATABASE_URL",
    "SENTRY_DSN",
    "EMAIL_HOST_PASSWORD",
    "PAYSTACK_SECRET_KEY",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
    "RESEND_API_KEY",
)

_CONNECTION_STRING = re.compile(
    r"(?P<scheme>postgres(?:ql)?|mysql|redis)://[^\s:/@]+:[^\s@]*@",
    re.IGNORECASE,
)

_BEARER_TOKEN = re.compile(
    r"\b(?P<scheme>bearer)\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)

_SECRET_ASSIGNMENT = re.compile(
    r"(?P<key>\b(?:database_url|dsn|password|passwd|secret(?:_key)?"
    r"|api[_-]?key|access[_-]?token|refresh|token)\b\"?"
    r"\s*[=:]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)


def _configured_secrets():
    """Yield literal secret values from settings, ignoring empty placeholders."""
    from django.conf import settings

    for name in SECRET_SETTINGS:
        value = getattr(settings, name, "")
        if isinstance(value, str) and len(value) >= 8:
            yield value


def redact(message: str) -> str:
    """Return ``message`` with credentials and tokens replaced by ``***``."""
    message = _CONNECTION_STRING.sub(
        lambda match: f"{match.group('scheme')}://{REDACTED}:{REDACTED}@",
        message,
    )
    message = _BEARER_TOKEN.sub(
        lambda match: f"{match.group('scheme')} {REDACTED}",
        message,
    )
    message = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}{REDACTED}",
        message,
    )

    for secret in _configured_secrets():
        if secret in message:
            message = message.replace(secret, REDACTED)

    return message


class SensitiveDataFilter(logging.Filter):
    """Redact a log record's formatted message before a handler emits it."""

    def filter(self, record: logging.LogRecord) -> bool:
        # ``exc_text`` is only populated by the formatter, which runs *after*
        # filters. Render the traceback here so exception messages are redacted
        # before the handler writes them.
        if record.exc_info and not record.exc_text:
            record.exc_text = "".join(traceback.format_exception(*record.exc_info))
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)

        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed logging calls
            # Never let the filter break logging; the formatter will report it.
            return True

        redacted = redact(message)
        if redacted != message:
            # The message is already formatted, so clear the args to stop the
            # formatter from interpolating placeholders a second time.
            record.msg = redacted
            record.args = ()

        return True


def scrub_event(event, hint=None):
    """Sentry ``before_send`` hook that applies :func:`redact` to an event.

    Sentry receives exception objects directly, so the logging filter never
    sees them. Scrubbing the outgoing event keeps credentials out of the
    error tracker as well as the log stream.
    """
    for key in ("message", "culprit"):
        value = event.get(key)
        if isinstance(value, str):
            event[key] = redact(value)

    exception = event.get("exception")
    if isinstance(exception, dict):
        for value in exception.get("values", []):
            if isinstance(value, dict) and isinstance(value.get("value"), str):
                value["value"] = redact(value["value"])

    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        for key in ("message", "formatted"):
            value = logentry.get(key)
            if isinstance(value, str):
                logentry[key] = redact(value)

    return event

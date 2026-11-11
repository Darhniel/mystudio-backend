"""Exception reporter filter that keeps credentials out of error pages.

Django's own ``SafeExceptionReporterFilter`` redacts settings whose names look
sensitive (``SECRET``, ``KEY``, ``PASS``, ...). It does not redact
``DATABASE_URL``, because that name contains none of those words. This subclass
extends the pattern so connection strings and third-party provider credentials
are masked in the debug page, the traceback log and any captured report.
"""

import re

from django.views.debug import (
    SafeExceptionReporterFilter as DjangoSafeExceptionReporterFilter,
)


class SafeExceptionReporterFilter(DjangoSafeExceptionReporterFilter):
    """Mask database URLs and provider keys in addition to Django's defaults."""

    hidden_settings = re.compile(
        r"API|AUTH|TOKEN|KEY|SECRET|PASS|SIGNATURE|HTTP_COOKIE"
        r"|DATABASE_URL|DSN|WEBHOOK|PAYSTACK|CLOUDINARY|RESEND",
        flags=re.IGNORECASE,
    )

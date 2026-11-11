import secrets

from django.db import migrations, models


def populate_access_tokens(apps, schema_editor):
    Booking = apps.get_model("bookings", "Booking")
    database = schema_editor.connection.alias

    bookings_without_tokens = (
        Booking.objects.using(database)
        .filter(access_token__isnull=True)
        .iterator()
    )
    for booking in bookings_without_tokens:
        Booking.objects.using(database).filter(pk=booking.pk).update(
            access_token=secrets.token_urlsafe(32)
        )


class Migration(migrations.Migration):
    dependencies = [
        ("bookings", "0002_reconcile_legacy_booking_schema"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="access_token",
            field=models.CharField(
                editable=False,
                help_text="High-entropy token for secure guest retrieval of booking details.",
                max_length=64,
                null=True,
                unique=True,
            ),
        ),
        migrations.RunPython(
            populate_access_tokens,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="booking",
            name="access_token",
            field=models.CharField(
                editable=False,
                help_text="High-entropy token for secure guest retrieval of booking details.",
                max_length=64,
                unique=True,
            ),
        ),
    ]

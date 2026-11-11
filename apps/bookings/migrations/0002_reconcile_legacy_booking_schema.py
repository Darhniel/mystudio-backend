"""Reconcile empty tables created by the pre-snapshot booking schema.

The original booking tables were created before migration tracking and the
current 0001 migration was later recorded as applied. Their columns therefore
do not match the model state represented by 0001. Rebuild only those legacy,
empty tables; never discard booking records.
"""

from django.db import migrations


LEGACY_BOOKING_COLUMNS = {
    "studio_price_kobo",
    "equipment_price_kobo",
    "total_price_kobo",
    "expires_at",
}


def reconcile_legacy_schema(apps, schema_editor):
    connection = schema_editor.connection
    table_names = set(connection.introspection.table_names())
    booking = apps.get_model("bookings", "Booking")
    booking_equipment = apps.get_model("bookings", "BookingEquipment")
    blocked_slot = apps.get_model("bookings", "BlockedSlot")
    models_to_rebuild = [booking_equipment, blocked_slot, booking]

    if booking._meta.db_table not in table_names:
        return

    with connection.cursor() as cursor:
        columns = {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, booking._meta.db_table
            )
        }

    if "payment_hold_expires_at" in columns:
        return
    if not LEGACY_BOOKING_COLUMNS.issubset(columns):
        raise RuntimeError(
            "The bookings table has an unrecognized schema. "
            "Inspect it before applying this migration."
        )

    existing_tables = [
        model._meta.db_table
        for model in models_to_rebuild
        if model._meta.db_table in table_names
    ]

    with connection.cursor() as cursor:
        if connection.vendor == "postgresql" and existing_tables:
            quoted_tables = ", ".join(
                connection.ops.quote_name(table) for table in existing_tables
            )
            cursor.execute(f"LOCK TABLE {quoted_tables} IN ACCESS EXCLUSIVE MODE")

        for table in existing_tables:
            quoted_table = connection.ops.quote_name(table)
            cursor.execute(f"SELECT COUNT(*) FROM {quoted_table}")
            if cursor.fetchone()[0]:
                raise RuntimeError(
                    f"Cannot reconcile legacy table {table!r} because it contains "
                    "records. Preserve or migrate those records before retrying."
                )

    for model in models_to_rebuild:
        if model._meta.db_table in table_names:
            schema_editor.delete_model(model)

    for model in reversed(models_to_rebuild):
        schema_editor.create_model(model)


class Migration(migrations.Migration):
    dependencies = [
        ("bookings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            reconcile_legacy_schema,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

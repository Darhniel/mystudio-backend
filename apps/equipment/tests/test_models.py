"""Model-level behaviour for the global equipment catalogue."""

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.equipment.models import Equipment
from apps.equipment.tests.factories import make_equipment, make_equipment_image

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Identity, defaults and ordering
# ---------------------------------------------------------------------------


def test_equipment_uses_a_uuid_primary_key():
    assert isinstance(make_equipment().pk, uuid.UUID)


def test_equipment_defaults():
    equipment = make_equipment()

    assert equipment.is_active is True
    assert equipment.created_at is not None
    assert equipment.updated_at is not None
    assert equipment.image_url is None
    assert equipment.cloudinary_public_id is None
    assert str(equipment) == equipment.name


def test_equipment_is_ordered_by_name():
    make_equipment(name="Zebra stand")
    make_equipment(name="Apple box")

    assert list(Equipment.objects.values_list("name", flat=True)) == [
        "Apple box",
        "Zebra stand",
    ]


def test_equipment_inventory_is_global():
    """Decision A: no studio relationship and a single shared stock pool."""

    field_names = {field.name for field in Equipment._meta.get_fields()}

    assert "studio" not in field_names
    assert "stock_quantity" in field_names


# ---------------------------------------------------------------------------
# Validation and database constraints
# ---------------------------------------------------------------------------


def test_full_clean_accepts_a_valid_item():
    make_equipment().full_clean()


def test_full_clean_accepts_zero_stock():
    equipment = make_equipment(stock_quantity=0)

    equipment.full_clean()

    assert equipment.stock_quantity == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fixed_price_kobo", 0),
        ("fixed_price_kobo", -1),
        ("stock_quantity", -1),
    ],
)
def test_full_clean_rejects_invalid_numbers(field, value):
    equipment = make_equipment()
    setattr(equipment, field, value)

    with pytest.raises(ValidationError) as exc_info:
        equipment.full_clean()

    assert field in exc_info.value.message_dict


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fixed_price_kobo", 0),
        ("fixed_price_kobo", -5),
        ("stock_quantity", -2),
    ],
)
def test_database_rejects_invalid_numbers(field, value):
    payload = {
        "name": "Invalid",
        "fixed_price_kobo": 100,
        "stock_quantity": 1,
    }
    payload[field] = value

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Equipment.objects.bulk_create([Equipment(**payload)])


def test_full_clean_requires_a_name():
    equipment = make_equipment()
    equipment.name = ""

    with pytest.raises(ValidationError) as exc_info:
        equipment.full_clean()

    assert "name" in exc_info.value.message_dict


def test_full_clean_rejects_an_invalid_image_url():
    equipment = Equipment(
        name="Invalid image URL",
        fixed_price_kobo=100,
        stock_quantity=1,
        image_url="not-a-url",
        cloudinary_public_id="mystudio/equipment/example",
    )

    with pytest.raises(ValidationError) as exc_info:
        equipment.full_clean()

    assert "image_url" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Optional Cloudinary image pairing
# ---------------------------------------------------------------------------


def test_full_clean_accepts_a_complete_image_pair():
    make_equipment_image().full_clean()


def test_full_clean_rejects_an_image_url_without_a_public_id():
    equipment = Equipment(
        name="Missing public ID",
        fixed_price_kobo=100,
        stock_quantity=1,
        image_url="https://example.com/light.jpg",
    )

    with pytest.raises(ValidationError) as exc_info:
        equipment.full_clean()

    assert {"image_url", "cloudinary_public_id"}.issubset(
        exc_info.value.message_dict
    )


def test_full_clean_rejects_a_public_id_without_an_image_url():
    equipment = Equipment(
        name="Missing image URL",
        fixed_price_kobo=100,
        stock_quantity=1,
        cloudinary_public_id="mystudio/equipment/example",
    )

    with pytest.raises(ValidationError) as exc_info:
        equipment.full_clean()

    assert {"image_url", "cloudinary_public_id"}.issubset(
        exc_info.value.message_dict
    )


def test_blank_image_fields_are_stored_as_null():
    equipment = Equipment.objects.create(
        name="No image",
        fixed_price_kobo=100,
        stock_quantity=1,
        image_url="",
        cloudinary_public_id="",
    )

    equipment.refresh_from_db()

    assert equipment.image_url is None
    assert equipment.cloudinary_public_id is None


def test_duplicate_cloudinary_public_id_is_rejected():
    first = make_equipment_image()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Equipment.objects.bulk_create(
                [
                    Equipment(
                        name="Duplicate image",
                        fixed_price_kobo=100,
                        stock_quantity=1,
                        image_url="https://example.com/photo.jpg",
                        cloudinary_public_id=first.cloudinary_public_id,
                    )
                ]
            )


def test_many_items_may_have_no_image():
    make_equipment(name="First")
    make_equipment(name="Second")

    assert Equipment.objects.filter(cloudinary_public_id__isnull=True).count() == 2


# ---------------------------------------------------------------------------
# Deactivation
# ---------------------------------------------------------------------------


def test_instance_delete_deactivates_instead_of_removing():
    equipment = make_equipment()
    equipment_pk = equipment.pk

    result = equipment.delete()

    assert result == (1, {"equipment.Equipment": 1})
    equipment.refresh_from_db()
    assert equipment.is_active is False
    assert Equipment.objects.filter(pk=equipment_pk).exists()


def test_queryset_delete_deactivates_every_row():
    first = make_equipment(name="First")
    second = make_equipment(name="Second")

    deleted, per_model = Equipment.objects.filter(
        pk__in=[first.pk, second.pk]
    ).delete()

    assert deleted == 2
    assert per_model == {"equipment.Equipment": 2}
    assert Equipment.objects.filter(is_active=False).count() == 2


def test_hard_delete_removes_the_row():
    equipment = make_equipment()
    equipment_pk = equipment.pk

    Equipment.objects.filter(pk=equipment_pk).hard_delete()

    assert not Equipment.objects.filter(pk=equipment_pk).exists()

"""Model-level behaviour for studios and their images."""

import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.studios.models import Studio, StudioImage
from apps.studios.tests.factories import make_studio, make_studio_image
from apps.studios.utils import build_unique_slug

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Studio identity, defaults and slug handling
# ---------------------------------------------------------------------------


def test_studio_uses_a_uuid_primary_key():
    assert isinstance(make_studio().pk, uuid.UUID)


def test_studio_defaults():
    studio = make_studio()

    assert studio.is_active is True
    assert studio.created_at is not None
    assert studio.updated_at is not None
    assert str(studio) == studio.name


def test_amenities_default_to_an_empty_list():
    studio = Studio.objects.create(name="Bare", hourly_rate_kobo=100, capacity=1)

    assert studio.amenities == []
    assert studio.slug == "bare"


def test_amenities_round_trip_through_the_database():
    studio = make_studio(amenities=["WiFi", "Air conditioning", "Parking"])
    studio.refresh_from_db()

    assert studio.amenities == ["WiFi", "Air conditioning", "Parking"]


def test_slug_is_generated_from_the_name():
    assert make_studio(name="The Loft Studio").slug == "the-loft-studio"


def test_generated_slugs_get_numeric_suffixes_on_collision():
    first = make_studio(name="Studio One")
    second = make_studio(name="Studio One")
    third = make_studio(name="Studio One")

    assert [first.slug, second.slug, third.slug] == [
        "studio-one",
        "studio-one-2",
        "studio-one-3",
    ]


def test_explicit_slug_is_preserved():
    assert make_studio(name="Anything", slug="custom-slug").slug == "custom-slug"


def test_duplicate_explicit_slug_is_rejected():
    make_studio(slug="shared-slug")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_studio(slug="shared-slug")


def test_unsluggable_name_falls_back_to_studio():
    assert make_studio(name="!!!").slug == "studio"


def test_resaving_without_a_slug_does_not_collide_with_itself():
    studio = make_studio(name="Unique Name")

    studio.slug = ""
    studio.save()

    assert studio.slug == "unique-name"


def test_generated_slug_respects_the_field_length():
    studio = make_studio(name="A" * 300)

    assert len(studio.slug) <= Studio._meta.get_field("slug").max_length


def test_build_unique_slug_suffix_stays_within_max_length():
    make_studio(slug="a" * 160)

    slug = build_unique_slug(Studio, "a" * 300, max_length=160)

    assert len(slug) == 160
    assert slug.endswith("-2")


# ---------------------------------------------------------------------------
# Studio validation and database constraints
# ---------------------------------------------------------------------------


def test_full_clean_accepts_a_valid_studio():
    make_studio().full_clean()


def test_full_clean_requires_a_name():
    studio = make_studio()
    studio.name = ""

    with pytest.raises(ValidationError) as exc_info:
        studio.full_clean()

    assert "name" in exc_info.value.message_dict


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hourly_rate_kobo", 0),
        ("hourly_rate_kobo", -1),
        ("capacity", 0),
        ("capacity", -1),
    ],
)
def test_full_clean_rejects_non_positive_numbers(field, value):
    studio = make_studio()
    setattr(studio, field, value)

    with pytest.raises(ValidationError) as exc_info:
        studio.full_clean()

    assert field in exc_info.value.message_dict


@pytest.mark.parametrize(
    "amenities",
    [
        "WiFi",
        None,
        ["WiFi", ""],
        ["WiFi", "   "],
        ["WiFi", 3],
    ],
)
def test_full_clean_rejects_invalid_amenities(amenities):
    studio = make_studio()
    studio.amenities = amenities

    with pytest.raises(ValidationError) as exc_info:
        studio.full_clean()

    assert "amenities" in exc_info.value.message_dict


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hourly_rate_kobo", 0),
        ("hourly_rate_kobo", -5),
        ("capacity", 0),
        ("capacity", -2),
    ],
)
def test_database_rejects_invalid_numbers(field, value):
    payload = {
        "name": "Invalid",
        "slug": f"invalid-{field}-{value}",
        "hourly_rate_kobo": 100,
        "capacity": 1,
    }
    payload[field] = value

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Studio.objects.create(**payload)


# ---------------------------------------------------------------------------
# Ordering and deactivation
# ---------------------------------------------------------------------------


def test_studios_are_ordered_by_name():
    make_studio(name="Zulu Studio")
    make_studio(name="Alpha Studio")

    assert list(Studio.objects.values_list("name", flat=True)) == [
        "Alpha Studio",
        "Zulu Studio",
    ]


def test_instance_delete_deactivates_instead_of_removing():
    studio = make_studio()
    studio_pk = studio.pk

    result = studio.delete()

    assert result == (1, {"studios.Studio": 1})
    studio.refresh_from_db()
    assert studio.is_active is False
    assert Studio.objects.filter(pk=studio_pk).exists()


def test_queryset_delete_deactivates_every_row():
    first = make_studio(name="First")
    second = make_studio(name="Second")

    deleted, per_model = Studio.objects.filter(
        pk__in=[first.pk, second.pk]
    ).delete()

    assert deleted == 2
    assert per_model == {"studios.Studio": 2}
    assert Studio.objects.filter(is_active=False).count() == 2


def test_hard_delete_removes_the_row():
    studio = make_studio()
    studio_pk = studio.pk

    Studio.objects.filter(pk=studio_pk).hard_delete()

    assert not Studio.objects.filter(pk=studio_pk).exists()


# ---------------------------------------------------------------------------
# StudioImage
# ---------------------------------------------------------------------------


def test_image_uses_a_uuid_primary_key_and_created_at():
    image = make_studio_image()

    assert isinstance(image.pk, uuid.UUID)
    assert image.created_at is not None


def test_image_belongs_to_its_studio():
    studio = make_studio()
    image = make_studio_image(studio=studio)

    assert image.studio == studio
    assert list(studio.images.all()) == [image]
    assert str(image) == f"{studio.name} image {image.display_order}"


def test_images_are_ordered_by_display_order():
    studio = make_studio()
    later = make_studio_image(studio=studio, display_order=2)
    earlier = make_studio_image(studio=studio, display_order=1)

    assert list(studio.images.all()) == [earlier, later]


def test_image_requires_its_core_fields():
    image = StudioImage(
        studio=make_studio(),
        image_url="",
        cloudinary_public_id="",
        display_order=0,
    )

    with pytest.raises(ValidationError) as exc_info:
        image.full_clean()

    assert {"image_url", "cloudinary_public_id"}.issubset(
        exc_info.value.message_dict
    )


def test_image_rejects_a_non_url_image_url():
    image = StudioImage(
        studio=make_studio(),
        image_url="not-a-url",
        cloudinary_public_id="mystudio/studios/example",
        display_order=0,
    )

    with pytest.raises(ValidationError) as exc_info:
        image.full_clean()

    assert "image_url" in exc_info.value.message_dict


def test_duplicate_cloudinary_public_id_is_rejected():
    first = make_studio_image()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            make_studio_image(cloudinary_public_id=first.cloudinary_public_id)


def test_images_can_be_deleted():
    image = make_studio_image()
    image_pk = image.pk

    image.delete()

    assert not StudioImage.objects.filter(pk=image_pk).exists()


def test_studio_with_images_cannot_be_hard_deleted():
    studio = make_studio()
    make_studio_image(studio=studio)

    with pytest.raises(ProtectedError):
        Studio.objects.filter(pk=studio.pk).hard_delete()


def test_studio_with_images_can_still_be_deactivated():
    studio = make_studio()
    make_studio_image(studio=studio)

    studio.delete()
    studio.refresh_from_db()

    assert studio.is_active is False
    assert studio.images.count() == 1

"""Administrative studio CRUD, image management and role permissions."""

from unittest import mock

import pytest
from django.test import override_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Role
from apps.accounts.tests.factories import make_admin, make_user
from apps.common.cloudinary import (
    ImageDeleteFailed,
    ImageUploadFailed,
    UploadedImage,
)
from apps.common.tests.images import image_upload
from apps.studios.models import Studio, StudioImage
from apps.studios.tests.factories import make_studio, make_studio_image

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/admin/studios/"
DETAIL_URL = "/api/v1/admin/studios/{studio_id}/"
IMAGE_LIST_URL = "/api/v1/admin/studios/{studio_id}/images/"
IMAGE_DETAIL_URL = "/api/v1/admin/studios/{studio_id}/images/{image_id}/"

UPLOADED = UploadedImage(
    url="https://res.cloudinary.com/demo/image/upload/admin-studio.jpg",
    public_id="mystudio/studios/admin-studio",
)

VALID_PAYLOAD = {
    "name": "Admin Studio",
    "description": "A studio created through the administrative API.",
    "hourly_rate_kobo": 30_000_00,
    "capacity": 10,
    "amenities": ["WiFi", "Parking"],
}


def _authenticate(api_client, user):
    access = RefreshToken.for_user(user).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return api_client


@pytest.fixture
def manager_client(api_client):
    manager = make_user(email="manager@example.com", role=Role.MANAGER)
    return _authenticate(api_client, manager)


@pytest.fixture
def owner_client(api_client):
    return _authenticate(api_client, make_admin(email="owner@example.com"))


@pytest.fixture
def staff_client(api_client):
    staff = make_user(email="staff@example.com", role=Role.STAFF)
    return _authenticate(api_client, staff)


# ---------------------------------------------------------------------------
# Authentication and permissions
# ---------------------------------------------------------------------------


def test_anonymous_users_cannot_list(api_client):
    make_studio()

    assert api_client.get(LIST_URL).status_code == 401


def test_anonymous_users_cannot_create(api_client):
    response = api_client.post(LIST_URL, VALID_PAYLOAD, format="json")

    assert response.status_code == 401
    assert Studio.objects.count() == 0


def test_staff_can_read_administrative_studios(staff_client):
    studio = make_studio(is_active=False)

    listed = staff_client.get(LIST_URL)
    detail = staff_client.get(DETAIL_URL.format(studio_id=studio.pk))

    assert listed.status_code == 200
    assert listed.data["count"] == 1
    assert detail.status_code == 200
    assert detail.data["id"] == str(studio.pk)


def test_staff_cannot_create_update_or_deactivate(staff_client):
    studio = make_studio()

    created = staff_client.post(LIST_URL, VALID_PAYLOAD, format="json")
    updated = staff_client.patch(
        DETAIL_URL.format(studio_id=studio.pk),
        {"capacity": 20},
        format="json",
    )
    deleted = staff_client.delete(DETAIL_URL.format(studio_id=studio.pk))

    assert created.status_code == 403
    assert updated.status_code == 403
    assert deleted.status_code == 403
    studio.refresh_from_db()
    assert studio.is_active is True
    assert studio.capacity == 12


def test_managers_can_create_update_and_deactivate(manager_client):
    created = manager_client.post(LIST_URL, VALID_PAYLOAD, format="json")
    assert created.status_code == 201

    studio_id = created.data["id"]
    updated = manager_client.patch(
        DETAIL_URL.format(studio_id=studio_id),
        {"capacity": 25, "amenities": ["WiFi"]},
        format="json",
    )
    deleted = manager_client.delete(DETAIL_URL.format(studio_id=studio_id))

    assert updated.status_code == 200
    assert updated.data["capacity"] == 25
    assert updated.data["amenities"] == ["WiFi"]
    assert deleted.status_code == 204

    studio = Studio.objects.get(pk=studio_id)
    assert studio.is_active is False


def test_owners_have_full_access(owner_client):
    created = owner_client.post(LIST_URL, VALID_PAYLOAD, format="json")
    assert created.status_code == 201

    studio_id = created.data["id"]
    updated = owner_client.patch(
        DETAIL_URL.format(studio_id=studio_id),
        {"capacity": 18},
        format="json",
    )
    deleted = owner_client.delete(DETAIL_URL.format(studio_id=studio_id))

    assert updated.status_code == 200
    assert updated.data["capacity"] == 18
    assert deleted.status_code == 204
    assert Studio.objects.get(pk=studio_id).is_active is False


def test_staff_cannot_manage_images(staff_client):
    studio = make_studio()

    response = staff_client.post(
        IMAGE_LIST_URL.format(studio_id=studio.pk),
        {"image": image_upload()},
        format="multipart",
    )

    assert response.status_code == 403
    assert StudioImage.objects.filter(studio=studio).count() == 0


# ---------------------------------------------------------------------------
# Create and validation
# ---------------------------------------------------------------------------


def test_create_generates_a_slug_and_returns_the_studio(manager_client):
    response = manager_client.post(LIST_URL, VALID_PAYLOAD, format="json")

    assert response.status_code == 201
    assert response.data["id"]
    assert response.data["slug"] == "admin-studio"
    assert response.data["is_active"] is True
    assert response.data["images"] == []
    assert response.data["created_at"]
    assert response.data["updated_at"]

    studio = Studio.objects.get(pk=response.data["id"])
    assert studio.name == "Admin Studio"
    assert studio.hourly_rate_kobo == 30_000_00
    assert studio.amenities == ["WiFi", "Parking"]


def test_create_accepts_an_explicit_slug(manager_client):
    payload = {**VALID_PAYLOAD, "slug": "custom-admin-slug"}

    response = manager_client.post(LIST_URL, payload, format="json")

    assert response.status_code == 201
    assert response.data["slug"] == "custom-admin-slug"


def test_create_rejects_a_duplicate_slug(manager_client):
    make_studio(name="Existing", slug="taken-slug")
    payload = {**VALID_PAYLOAD, "slug": "taken-slug"}

    response = manager_client.post(LIST_URL, payload, format="json")

    assert response.status_code == 400
    assert "slug" in response.data
    assert Studio.objects.filter(slug="taken-slug").count() == 1


def test_create_requires_a_name(manager_client):
    payload = {**VALID_PAYLOAD}
    payload.pop("name")

    response = manager_client.post(LIST_URL, payload, format="json")

    assert response.status_code == 400
    assert "name" in response.data


@pytest.mark.parametrize("field", ["hourly_rate_kobo", "capacity"])
@pytest.mark.parametrize("value", [0, -1])
def test_create_rejects_non_positive_numbers(manager_client, field, value):
    payload = {**VALID_PAYLOAD, field: value}

    response = manager_client.post(LIST_URL, payload, format="json")

    assert response.status_code == 400
    assert field in response.data


@pytest.mark.parametrize(
    "amenities",
    ["WiFi", ["WiFi", ""], [1, 2]],
)
def test_create_validates_amenities(manager_client, amenities):
    payload = {**VALID_PAYLOAD, "amenities": amenities}

    response = manager_client.post(LIST_URL, payload, format="json")

    assert response.status_code == 400
    assert "amenities" in response.data


# ---------------------------------------------------------------------------
# List, filtering, search and ordering
# ---------------------------------------------------------------------------


def test_list_includes_inactive_studios(manager_client):
    make_studio(name="Active Studio")
    make_studio(name="Inactive Studio", is_active=False)

    response = manager_client.get(LIST_URL)

    assert response.status_code == 200
    assert response.data["count"] == 2
    assert {item["name"] for item in response.data["results"]} == {
        "Active Studio",
        "Inactive Studio",
    }


def test_list_filters_by_active_state(manager_client):
    make_studio(name="Active Studio")
    make_studio(name="Inactive Studio", is_active=False)

    inactive = manager_client.get(LIST_URL, {"is_active": "false"})

    assert inactive.data["count"] == 1
    assert inactive.data["results"][0]["name"] == "Inactive Studio"


def test_list_searches_name_slug_and_description(manager_client):
    make_studio(name="Natural Light", slug="natural-light")
    make_studio(
        name="Black Box",
        slug="black-box",
        description="Includes a cyclorama.",
    )

    by_slug = manager_client.get(LIST_URL, {"search": "black-box"})
    by_description = manager_client.get(LIST_URL, {"search": "cyclorama"})

    assert [item["slug"] for item in by_slug.data["results"]] == ["black-box"]
    assert [item["slug"] for item in by_description.data["results"]] == [
        "black-box"
    ]


def test_list_supports_ordering_and_pagination(manager_client):
    make_studio(name="Alpha", capacity=5)
    make_studio(name="Bravo", capacity=15)
    make_studio(name="Charlie", capacity=25)

    ordered = manager_client.get(LIST_URL, {"ordering": "-capacity"})
    page = manager_client.get(LIST_URL, {"page_size": 2, "page": 1})

    assert [item["name"] for item in ordered.data["results"]] == [
        "Charlie",
        "Bravo",
        "Alpha",
    ]
    assert page.data["count"] == 3
    assert len(page.data["results"]) == 2
    assert page.data["next"] is not None


# ---------------------------------------------------------------------------
# Detail, update and deactivation
# ---------------------------------------------------------------------------


def test_unknown_studio_returns_404(manager_client):
    response = manager_client.get(
        DETAIL_URL.format(studio_id="00000000-0000-0000-0000-000000000000")
    )

    assert response.status_code == 404


def test_update_allows_partial_changes(manager_client):
    studio = make_studio()

    response = manager_client.patch(
        DETAIL_URL.format(studio_id=studio.pk),
        {"description": "Updated copy.", "is_active": False},
        format="json",
    )

    assert response.status_code == 200
    studio.refresh_from_db()
    assert studio.description == "Updated copy."
    assert studio.is_active is False
    assert studio.name == "Natural Light Studio"


def test_update_rejects_a_duplicate_slug(manager_client):
    make_studio(name="First", slug="first")
    studio = make_studio(name="Second", slug="second")

    response = manager_client.patch(
        DETAIL_URL.format(studio_id=studio.pk),
        {"slug": "first"},
        format="json",
    )

    assert response.status_code == 400
    assert "slug" in response.data


def test_update_can_regenerate_a_blank_slug(manager_client):
    studio = make_studio(name="Old Name", slug="old-name")

    response = manager_client.patch(
        DETAIL_URL.format(studio_id=studio.pk),
        {"name": "New Name", "slug": ""},
        format="json",
    )

    assert response.status_code == 200
    studio.refresh_from_db()
    assert studio.slug == "new-name"


def test_delete_deactivates_and_keeps_the_row(manager_client):
    studio = make_studio()
    image = make_studio_image(studio=studio)

    response = manager_client.delete(DETAIL_URL.format(studio_id=studio.pk))

    assert response.status_code == 204
    studio.refresh_from_db()
    assert studio.is_active is False
    assert Studio.objects.filter(pk=studio.pk).exists()
    assert StudioImage.objects.filter(pk=image.pk).exists()
    assert studio.images.count() == 1


def test_deleted_studios_disappear_from_the_public_catalogue(
    manager_client,
    api_client,
):
    studio = make_studio()

    manager_client.delete(DETAIL_URL.format(studio_id=studio.pk))

    assert api_client.get("/api/v1/studios/").data["count"] == 0
    assert (
        api_client.get(f"/api/v1/studios/{studio.slug}/").status_code == 404
    )


def test_deleting_an_inactive_studio_is_safe(manager_client):
    studio = make_studio(is_active=False)

    response = manager_client.delete(DETAIL_URL.format(studio_id=studio.pk))

    assert response.status_code == 204
    assert Studio.objects.filter(pk=studio.pk).exists()


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def test_manager_can_upload_an_image(manager_client):
    studio = make_studio()

    with mock.patch(
        "apps.studios.services.upload_image", return_value=UPLOADED
    ) as upload_mock:
        response = manager_client.post(
            IMAGE_LIST_URL.format(studio_id=studio.pk),
            {
                "image": image_upload(),
                "alt_text": "Admin upload",
                "display_order": 4,
            },
            format="multipart",
        )

    assert response.status_code == 201
    assert response.data["image_url"] == UPLOADED.url
    assert response.data["cloudinary_public_id"] == UPLOADED.public_id
    assert response.data["alt_text"] == "Admin upload"
    assert response.data["display_order"] == 4
    assert upload_mock.call_args.kwargs["folder"] == "mystudio/studios"


def test_anonymous_users_cannot_upload_images(api_client):
    studio = make_studio()

    response = api_client.post(
        IMAGE_LIST_URL.format(studio_id=studio.pk),
        {"image": image_upload()},
        format="multipart",
    )

    assert response.status_code == 401


def test_uploading_to_an_unknown_studio_returns_404(manager_client):
    response = manager_client.post(
        IMAGE_LIST_URL.format(
            studio_id="00000000-0000-0000-0000-000000000000"
        ),
        {"image": image_upload()},
        format="multipart",
    )

    assert response.status_code == 404


def test_upload_rejects_an_invalid_image(manager_client):
    studio = make_studio()

    with mock.patch(
        "apps.studios.services.upload_image"
    ) as upload_mock:
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = manager_client.post(
            IMAGE_LIST_URL.format(studio_id=studio.pk),
            {
                "image": SimpleUploadedFile(
                    "photo.jpg",
                    b"not an image",
                    content_type="image/jpeg",
                )
            },
            format="multipart",
        )

    assert response.status_code == 400
    assert "image" in response.data
    assert not upload_mock.called


@override_settings(IMAGE_UPLOAD_MAX_BYTES=20)
def test_upload_rejects_an_oversized_image(manager_client):
    studio = make_studio()

    response = manager_client.post(
        IMAGE_LIST_URL.format(studio_id=studio.pk),
        {"image": image_upload()},
        format="multipart",
    )

    assert response.status_code == 400


@mock.patch(
    "apps.studios.services.upload_image",
    side_effect=ImageUploadFailed("provider down"),
)
def test_upload_provider_failure_returns_502(upload_mock, manager_client):
    studio = make_studio()

    response = manager_client.post(
        IMAGE_LIST_URL.format(studio_id=studio.pk),
        {"image": image_upload()},
        format="multipart",
    )

    assert response.status_code == 502
    assert upload_mock.called
    assert StudioImage.objects.filter(studio=studio).count() == 0


def test_manager_can_update_image_metadata(manager_client):
    studio = make_studio()
    image = make_studio_image(studio=studio, alt_text="Old", display_order=1)

    response = manager_client.patch(
        IMAGE_DETAIL_URL.format(studio_id=studio.pk, image_id=image.pk),
        {"alt_text": "New", "display_order": 7},
        format="json",
    )

    assert response.status_code == 200
    image.refresh_from_db()
    assert image.alt_text == "New"
    assert image.display_order == 7


def test_image_update_requires_at_least_one_field(manager_client):
    studio = make_studio()
    image = make_studio_image(studio=studio)

    response = manager_client.patch(
        IMAGE_DETAIL_URL.format(studio_id=studio.pk, image_id=image.pk),
        {},
        format="json",
    )

    assert response.status_code == 400


def test_images_are_scoped_to_their_studio(manager_client):
    first = make_studio(name="First Studio")
    second = make_studio(name="Second Studio")
    image = make_studio_image(studio=first)

    response = manager_client.patch(
        IMAGE_DETAIL_URL.format(studio_id=second.pk, image_id=image.pk),
        {"display_order": 3},
        format="json",
    )

    assert response.status_code == 404


def test_manager_can_delete_an_image(manager_client):
    studio = make_studio()
    image = make_studio_image(studio=studio)
    image_id = image.pk
    public_id = image.cloudinary_public_id

    with mock.patch("apps.studios.services.delete_image") as delete_mock:
        response = manager_client.delete(
            IMAGE_DETAIL_URL.format(studio_id=studio.pk, image_id=image_id)
        )

    assert response.status_code == 204
    assert not StudioImage.objects.filter(pk=image_id).exists()
    delete_mock.assert_called_once_with(public_id)


@mock.patch(
    "apps.studios.services.delete_image",
    side_effect=ImageDeleteFailed("provider down"),
)
def test_failed_image_delete_returns_502(delete_mock, manager_client):
    studio = make_studio()
    image = make_studio_image(studio=studio)

    response = manager_client.delete(
        IMAGE_DETAIL_URL.format(studio_id=studio.pk, image_id=image.pk)
    )

    assert response.status_code == 502
    assert delete_mock.called

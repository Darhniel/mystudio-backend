"""Public studio catalogue endpoints (no authentication required)."""

import pytest

from apps.studios.models import StudioImage
from apps.studios.tests.factories import make_studio, make_studio_image

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/studios/"
DETAIL_URL = "/api/v1/studios/{slug}/"

LIST_FIELDS = {
    "id",
    "name",
    "slug",
    "short_description",
    "hourly_rate_kobo",
    "capacity",
    "amenities",
    "primary_image",
    "image_count",
}

IMAGE_FIELDS = {"id", "image_url", "alt_text", "display_order"}

DETAIL_FIELDS = {
    "id",
    "name",
    "slug",
    "description",
    "hourly_rate_kobo",
    "capacity",
    "amenities",
    "images",
    "created_at",
}


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_list_returns_active_studios_with_public_fields(api_client):
    make_studio(name="Natural Light Studio", amenities=["WiFi", "Parking"])

    response = api_client.get(LIST_URL)

    assert response.status_code == 200
    assert response.data["count"] == 1

    studio = response.data["results"][0]
    assert set(studio) == LIST_FIELDS
    assert studio["name"] == "Natural Light Studio"
    assert studio["slug"] == "natural-light-studio"
    assert studio["hourly_rate_kobo"] == 25_000_00
    assert studio["capacity"] == 12
    assert studio["amenities"] == ["WiFi", "Parking"]
    assert studio["image_count"] == 0
    assert studio["primary_image"] is None


def test_list_does_not_expose_administrative_fields(api_client):
    studio = make_studio()
    make_studio_image(studio=studio)

    response = api_client.get(LIST_URL)

    payload = response.data["results"][0]
    assert "is_active" not in payload
    assert "cloudinary_public_id" not in str(payload)
    assert "cloudinary_public_id" not in str(payload["primary_image"])
    assert set(payload["primary_image"]) == IMAGE_FIELDS


def test_short_description_is_truncated(api_client):
    make_studio(description="A" * 200)
    make_studio(name="Short", slug="short", description="Compact description")

    response = api_client.get(LIST_URL)
    by_slug = {item["slug"]: item for item in response.data["results"]}

    assert len(by_slug["natural-light-studio"]["short_description"]) <= 160
    assert by_slug["natural-light-studio"]["short_description"].endswith("...")
    assert by_slug["short"]["short_description"] == "Compact description"


def test_primary_image_is_the_lowest_display_order(api_client):
    studio = make_studio()
    later = make_studio_image(studio=studio, display_order=5, alt_text="Later")
    first = make_studio_image(studio=studio, display_order=1, alt_text="First")

    response = api_client.get(LIST_URL)

    payload = response.data["results"][0]
    assert payload["image_count"] == 2
    assert payload["primary_image"]["id"] == str(first.pk)
    assert payload["primary_image"]["alt_text"] == "First"
    assert payload["primary_image"]["image_url"] == first.image_url
    assert str(later.pk) != payload["primary_image"]["id"]


def test_list_is_paginated(api_client):
    for index in range(3):
        make_studio(name=f"Studio {index}")

    first_page = api_client.get(LIST_URL, {"page_size": 2})
    second_page = api_client.get(LIST_URL, {"page_size": 2, "page": 2})

    assert first_page.status_code == 200
    assert first_page.data["count"] == 3
    assert len(first_page.data["results"]) == 2
    assert first_page.data["next"] is not None
    assert first_page.data["previous"] is None

    assert second_page.data["count"] == 3
    assert len(second_page.data["results"]) == 1
    assert second_page.data["next"] is None
    assert second_page.data["previous"] is not None


def test_list_excludes_inactive_studios(api_client):
    make_studio(name="Visible Studio")
    make_studio(name="Hidden Studio", is_active=False)

    response = api_client.get(LIST_URL)

    assert response.data["count"] == 1
    assert response.data["results"][0]["name"] == "Visible Studio"


def test_list_is_ordered_by_name_by_default(api_client):
    make_studio(name="Zeta Studio")
    make_studio(name="Alpha Studio")
    make_studio(name="Mango Studio")

    response = api_client.get(LIST_URL)

    names = [item["name"] for item in response.data["results"]]
    assert names == ["Alpha Studio", "Mango Studio", "Zeta Studio"]


def test_list_can_be_ordered_by_price_and_capacity(api_client):
    make_studio(name="Cheap", slug="cheap", hourly_rate_kobo=10_000_00, capacity=4)
    make_studio(name="Pricey", slug="pricey", hourly_rate_kobo=90_000_00, capacity=40)
    make_studio(name="Middle", slug="middle", hourly_rate_kobo=50_000_00, capacity=12)

    by_price = api_client.get(LIST_URL, {"ordering": "-hourly_rate_kobo"})
    by_capacity = api_client.get(LIST_URL, {"ordering": "capacity"})

    assert [item["slug"] for item in by_price.data["results"]] == [
        "pricey",
        "middle",
        "cheap",
    ]
    assert [item["slug"] for item in by_capacity.data["results"]] == [
        "cheap",
        "middle",
        "pricey",
    ]


def test_list_searches_name_and_description(api_client):
    make_studio(name="Natural Light Studio", description="North-facing windows.")
    make_studio(
        name="Black Box Studio",
        slug="black-box",
        description="Includes a white cyclorama.",
    )

    by_name = api_client.get(LIST_URL, {"search": "natural"})
    by_description = api_client.get(LIST_URL, {"search": "cyclorama"})

    assert [item["slug"] for item in by_name.data["results"]] == [
        "natural-light-studio"
    ]
    assert [item["slug"] for item in by_description.data["results"]] == ["black-box"]


def test_list_filters_by_capacity_and_hourly_rate(api_client):
    make_studio(name="Small", slug="small", capacity=4, hourly_rate_kobo=10_000_00)
    make_studio(name="Medium", slug="medium", capacity=12, hourly_rate_kobo=30_000_00)
    make_studio(name="Large", slug="large", capacity=40, hourly_rate_kobo=90_000_00)

    def slugs(**params):
        return [
            item["slug"]
            for item in api_client.get(LIST_URL, params).data["results"]
        ]

    assert slugs(capacity_min=12) == ["large", "medium"]
    assert slugs(capacity_max=12) == ["medium", "small"]
    assert slugs(hourly_rate_min=30_000_00) == ["large", "medium"]
    assert slugs(hourly_rate_max=30_000_00) == ["medium", "small"]


def test_list_rejects_a_non_numeric_filter(api_client):
    response = api_client.get(LIST_URL, {"capacity_min": "many"})

    assert response.status_code == 400


def test_anonymous_and_invalid_tokens_can_browse(api_client):
    make_studio()

    anonymous = api_client.get(LIST_URL)
    api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    invalid_token = api_client.get(LIST_URL)

    assert anonymous.status_code == 200
    assert invalid_token.status_code == 200


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def test_detail_returns_the_full_studio(api_client):
    studio = make_studio(description="A bright north-facing studio.")
    make_studio_image(studio=studio, display_order=2, alt_text="Second")
    make_studio_image(studio=studio, display_order=1, alt_text="First")

    response = api_client.get(DETAIL_URL.format(slug=studio.slug))

    assert response.status_code == 200
    assert set(response.data) == DETAIL_FIELDS
    assert response.data["id"] == str(studio.pk)
    assert response.data["name"] == studio.name
    assert response.data["slug"] == studio.slug
    assert response.data["description"] == "A bright north-facing studio."
    assert response.data["hourly_rate_kobo"] == studio.hourly_rate_kobo
    assert response.data["capacity"] == studio.capacity
    assert response.data["amenities"] == studio.amenities
    assert response.data["created_at"] is not None
    assert [image["alt_text"] for image in response.data["images"]] == [
        "First",
        "Second",
    ]
    assert all(set(image) == IMAGE_FIELDS for image in response.data["images"])
    assert "cloudinary_public_id" not in str(response.data)


def test_detail_does_not_require_authentication(api_client):
    studio = make_studio()

    api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    response = api_client.get(DETAIL_URL.format(slug=studio.slug))

    assert response.status_code == 200


def test_inactive_studio_detail_returns_404(api_client):
    studio = make_studio(is_active=False)

    response = api_client.get(DETAIL_URL.format(slug=studio.slug))

    assert response.status_code == 404
    assert "detail" in response.data


def test_unknown_studio_detail_returns_404(api_client):
    response = api_client.get(DETAIL_URL.format(slug="does-not-exist"))

    assert response.status_code == 404
    assert "detail" in response.data


def test_inactive_studios_keep_their_rows_for_history(api_client):
    studio = make_studio(is_active=False)
    image = make_studio_image(studio=studio)

    response = api_client.get(DETAIL_URL.format(slug=studio.slug))

    assert response.status_code == 404
    assert StudioImage.objects.filter(pk=image.pk).exists()

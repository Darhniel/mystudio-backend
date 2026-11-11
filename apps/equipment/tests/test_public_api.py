"""Public equipment catalogue endpoint (no authentication required)."""

import pytest

from apps.equipment.tests.factories import (
    make_equipment,
    make_equipment_image,
)

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/equipment/"

PUBLIC_FIELDS = {
    "id",
    "name",
    "description",
    "fixed_price_kobo",
    "image_url",
}


def _results(response):
    return response.data["results"]


def test_list_returns_active_equipment_with_public_fields(api_client):
    make_equipment(
        name="Godox SL-60W light",
        description="Continuous LED light with a Bowens mount.",
    )

    response = api_client.get(LIST_URL)

    assert response.status_code == 200
    assert response.data["count"] == 1
    equipment = _results(response)[0]
    assert set(equipment) == PUBLIC_FIELDS
    assert equipment["name"] == "Godox SL-60W light"
    assert equipment["description"] == "Continuous LED light with a Bowens mount."
    assert equipment["fixed_price_kobo"] == 5_000_00
    assert equipment["image_url"] is None


def test_list_does_not_expose_inventory_or_administrative_fields(api_client):
    make_equipment_image(name="Reflector")

    equipment = _results(api_client.get(LIST_URL))[0]

    assert "stock_quantity" not in equipment
    assert "is_active" not in equipment
    assert "created_at" not in equipment
    assert "updated_at" not in equipment
    assert "cloudinary_public_id" not in equipment
    assert "cloudinary_public_id" not in str(equipment)
    assert equipment["image_url"].startswith("https://res.cloudinary.com/")


def test_list_is_paginated(api_client):
    for index in range(3):
        make_equipment(name=f"Item {index}")

    first_page = api_client.get(LIST_URL, {"page_size": 2})
    second_page = api_client.get(LIST_URL, {"page_size": 2, "page": 2})

    assert first_page.data["count"] == 3
    assert len(first_page.data["results"]) == 2
    assert first_page.data["next"] is not None
    assert first_page.data["previous"] is None

    assert second_page.data["results"]
    assert second_page.data["next"] is None
    assert second_page.data["previous"] is not None


def test_list_excludes_inactive_equipment(api_client):
    make_equipment(name="Available light")
    make_equipment(name="Retired light", is_active=False)

    response = api_client.get(LIST_URL)

    assert response.data["count"] == 1
    assert _results(response)[0]["name"] == "Available light"


def test_list_is_ordered_by_name_by_default(api_client):
    make_equipment(name="Zoom lens")
    make_equipment(name="Apple box")
    make_equipment(name="Monopod")

    response = api_client.get(LIST_URL)

    assert [item["name"] for item in _results(response)] == [
        "Apple box",
        "Monopod",
        "Zoom lens",
    ]


def test_list_can_be_ordered_by_price(api_client):
    make_equipment(name="Cheap", fixed_price_kobo=1_000_00)
    make_equipment(name="Pricey", fixed_price_kobo=9_000_00)

    response = api_client.get(LIST_URL, {"ordering": "-fixed_price_kobo"})

    assert [item["name"] for item in _results(response)] == ["Pricey", "Cheap"]


def test_list_searches_name_and_description(api_client):
    make_equipment(name="Godox SL-60W light", description="Continuous LED.")
    make_equipment(
        name="Reflector",
        description="Five-in-one collapsible reflector.",
    )

    by_name = api_client.get(LIST_URL, {"search": "godox"})
    by_description = api_client.get(LIST_URL, {"search": "collapsible"})

    assert [item["name"] for item in _results(by_name)] == ["Godox SL-60W light"]
    assert [item["name"] for item in _results(by_description)] == ["Reflector"]


def test_list_filters_by_fixed_price(api_client):
    make_equipment(name="Cheap", fixed_price_kobo=1_000_00)
    make_equipment(name="Middle", fixed_price_kobo=5_000_00)
    make_equipment(name="Pricey", fixed_price_kobo=9_000_00)

    def names(**params):
        response = api_client.get(LIST_URL, params)
        return [item["name"] for item in _results(response)]

    assert names(fixed_price_min=5_000_00) == ["Middle", "Pricey"]
    assert names(fixed_price_max=5_000_00) == ["Cheap", "Middle"]
    assert names(fixed_price_min=2_000_00, fixed_price_max=6_000_00) == ["Middle"]


def test_list_rejects_a_non_numeric_filter(api_client):
    response = api_client.get(LIST_URL, {"fixed_price_min": "cheap"})

    assert response.status_code == 400


def test_anonymous_and_invalid_tokens_can_browse(api_client):
    make_equipment()

    anonymous = api_client.get(LIST_URL)
    api_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    invalid_token = api_client.get(LIST_URL)

    assert anonymous.status_code == 200
    assert invalid_token.status_code == 200


def test_there_is_no_public_equipment_detail_route(api_client):
    equipment = make_equipment()

    response = api_client.get(f"/api/v1/equipment/{equipment.pk}/")

    assert response.status_code == 404

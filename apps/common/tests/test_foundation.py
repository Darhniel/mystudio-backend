import pytest
from rest_framework.settings import api_settings

from apps.common.api.pagination import DefaultPagination
from apps.common.constants import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from apps.common.fields import MoneyField


def test_health_check_is_public(api_client, db):
    response = api_client.get("/api/v1/health/")

    assert response.status_code == 200
    assert response.data["status"] == "ok"
    assert response.data["checks"]["database"] == "ok"


def test_openapi_schema_is_available(api_client):
    response = api_client.get("/api/schema/")

    assert response.status_code == 200


def test_swagger_ui_is_available(api_client):
    response = api_client.get("/api/docs/")

    assert response.status_code == 200


def test_redoc_is_available(api_client):
    response = api_client.get("/api/redoc/")

    assert response.status_code == 200


def test_unhandled_exception_returns_generic_error():
    from django.test import override_settings
    from rest_framework.test import APIRequestFactory
    from rest_framework.views import APIView

    class BoomView(APIView):
        authentication_classes = ()
        permission_classes = ()

        def get(self, request):
            raise RuntimeError("boom")

    with override_settings(DEBUG=False):
        response = BoomView.as_view()(APIRequestFactory().get("/boom/"))
        response.render()

    assert response.status_code == 500
    assert response.data == {
        "detail": "An unexpected error occurred. Please try again later."
    }


def test_money_field_defaults():
    field = MoneyField()

    assert field.max_digits == MONEY_MAX_DIGITS
    assert field.decimal_places == MONEY_DECIMAL_PLACES


def test_default_pagination_is_bounded():
    pagination = DefaultPagination()

    assert pagination.page_size == 20
    assert pagination.max_page_size == 100
    assert pagination.page_size_query_param == "page_size"


def test_router_uses_project_pagination():
    assert api_settings.DEFAULT_PAGINATION_CLASS is DefaultPagination


def test_router_supports_filtering_for_future_lists():
    from django_filters.rest_framework import DjangoFilterBackend
    from rest_framework.filters import OrderingFilter, SearchFilter

    assert api_settings.DEFAULT_FILTER_BACKENDS == [
        DjangoFilterBackend,
        SearchFilter,
        OrderingFilter,
    ]


def test_api_requires_authentication_by_default():
    from rest_framework.permissions import IsAuthenticated

    assert api_settings.DEFAULT_PERMISSION_CLASSES == [IsAuthenticated]


def test_centralized_exception_handler_is_configured():
    from apps.common.api.exceptions import api_exception_handler

    assert api_settings.EXCEPTION_HANDLER is api_exception_handler


def test_jwt_rotation_and_blacklisting_are_enabled():
    from django.conf import settings

    assert settings.SIMPLE_JWT["ROTATE_REFRESH_TOKENS"] is True
    assert settings.SIMPLE_JWT["BLACKLIST_AFTER_ROTATION"] is True


def test_jwt_lifetimes_are_positive_and_ordered():
    from datetime import timedelta

    from django.conf import settings

    access = settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"]
    refresh = settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]

    assert isinstance(access, timedelta)
    assert isinstance(refresh, timedelta)
    assert timedelta(0) < access < refresh


@pytest.mark.django_db(transaction=True)
def test_soft_delete_keeps_row_but_hides_it():
    from django.db import connection, models
    from django.test.utils import isolate_apps

    from apps.common.models import SoftDeleteModel

    with isolate_apps("apps.common"):

        class Widget(SoftDeleteModel):
            name = models.CharField(max_length=50)

            class Meta:
                app_label = "common"

        with connection.schema_editor() as editor:
            editor.create_model(Widget)

        try:
            widget = Widget.objects.create(name="light")
            widget.delete()

            assert Widget.objects.count() == 0
            assert Widget.all_objects.count() == 1
            assert Widget.all_objects.get().is_deleted is True

            Widget.all_objects.get().restore()
            assert Widget.objects.count() == 1
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(Widget)


@pytest.mark.django_db(transaction=True)
def test_soft_delete_queryset_marks_every_row():
    from django.db import connection, models
    from django.test.utils import isolate_apps

    from apps.common.models import SoftDeleteModel

    with isolate_apps("apps.common"):

        class Gadget(SoftDeleteModel):
            name = models.CharField(max_length=50)

            class Meta:
                app_label = "common"

        with connection.schema_editor() as editor:
            editor.create_model(Gadget)

        try:
            Gadget.objects.bulk_create([Gadget(name="a"), Gadget(name="b")])

            deleted, _ = Gadget.objects.all().delete()

            assert deleted == 2
            assert Gadget.all_objects.filter(is_deleted=True).count() == 2
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(Gadget)

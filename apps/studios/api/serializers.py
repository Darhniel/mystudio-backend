"""Serializers for studios and their images.

Serializers only shape and validate data; upload, visibility and deletion rules
live in ``apps.studios.services`` and ``apps.studios.selectors``.
"""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from apps.common.constants import MAX_DATABASE_INTEGER
from apps.studios import services
from apps.studios.models import Studio, StudioImage
from apps.studios.utils import normalise_slug, validate_amenities

SHORT_DESCRIPTION_MAX_LENGTH = 160


class NormalisedSlugField(serializers.SlugField):
    """Slug field that canonicalises input before uniqueness validation.

    Lower-casing here means ``My-Studio`` is rejected when ``my-studio``
    already exists and that the database always stores the public form.
    """

    def to_internal_value(self, data):
        return normalise_slug(super().to_internal_value(data))


def _short_description(value: str) -> str:
    """Trim a description for the list payload.

    The full text remains available from the detail endpoint.
    """

    text = (value or "").strip()
    if len(text) <= SHORT_DESCRIPTION_MAX_LENGTH:
        return text
    return f"{text[: SHORT_DESCRIPTION_MAX_LENGTH - 3].rstrip()}..."


class StudioPublicImageSerializer(serializers.ModelSerializer):
    """Public image representation. Cloudinary IDs stay administrative."""

    class Meta:
        model = StudioImage
        fields = ("id", "image_url", "alt_text", "display_order")
        read_only_fields = fields


class StudioListSerializer(serializers.ModelSerializer):
    """Catalogue card for an active studio."""

    short_description = serializers.SerializerMethodField()
    primary_image = serializers.SerializerMethodField()
    image_count = serializers.SerializerMethodField()

    class Meta:
        model = Studio
        fields = (
            "id",
            "name",
            "slug",
            "short_description",
            "hourly_rate_kobo",
            "capacity",
            "amenities",
            "primary_image",
            "image_count",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_short_description(self, obj) -> str:
        return _short_description(obj.description)

    @extend_schema_field(StudioPublicImageSerializer(allow_null=True))
    def get_primary_image(self, obj):
        images = list(obj.images.all())
        if not images:
            return None
        return StudioPublicImageSerializer(
            images[0],
            context=self.context,
        ).data

    @extend_schema_field(serializers.IntegerField())
    def get_image_count(self, obj) -> int:
        return len(obj.images.all())


class StudioDetailSerializer(serializers.ModelSerializer):
    """Full public studio page, including every ordered image."""

    images = StudioPublicImageSerializer(many=True, read_only=True)

    class Meta:
        model = Studio
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "hourly_rate_kobo",
            "capacity",
            "amenities",
            "images",
            "created_at",
        )
        read_only_fields = fields


class StudioImageSerializer(serializers.ModelSerializer):
    """Administrative representation of a stored studio image."""

    class Meta:
        model = StudioImage
        fields = (
            "id",
            "image_url",
            "cloudinary_public_id",
            "alt_text",
            "display_order",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class StudioAdminSerializer(serializers.ModelSerializer):
    """Create/update payload and administrative representation of a studio."""

    slug = NormalisedSlugField(
        required=False,
        allow_blank=True,
        validators=[UniqueValidator(queryset=Studio.objects.all())],
    )
    hourly_rate_kobo = serializers.IntegerField(
        min_value=1,
        max_value=MAX_DATABASE_INTEGER,
    )
    capacity = serializers.IntegerField(
        min_value=1,
        max_value=MAX_DATABASE_INTEGER,
    )
    images = StudioImageSerializer(many=True, read_only=True)

    class Meta:
        model = Studio
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "hourly_rate_kobo",
            "capacity",
            "amenities",
            "is_active",
            "images",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "images", "created_at", "updated_at")
        extra_kwargs = {
            "description": {"required": False, "allow_blank": True},
        }

    def validate_amenities(self, value):
        validate_amenities(value)
        return value

    def create(self, validated_data):
        return services.create_studio(**validated_data)

    def update(self, instance, validated_data):
        return services.update_studio(studio=instance, **validated_data)


class StudioImageUploadSerializer(serializers.Serializer):
    """Multipart payload for adding one image to a studio."""

    image = serializers.ImageField()
    alt_text = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        default="",
    )
    display_order = serializers.IntegerField(
        min_value=0,
        max_value=MAX_DATABASE_INTEGER,
        required=False,
        allow_null=True,
        default=None,
    )


class StudioImageUpdateSerializer(serializers.Serializer):
    """Fields an administrator may change without re-uploading the file."""

    alt_text = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
    )
    display_order = serializers.IntegerField(
        min_value=0,
        max_value=MAX_DATABASE_INTEGER,
        required=False,
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(
                "Provide alt_text or display_order."
            )
        return attrs

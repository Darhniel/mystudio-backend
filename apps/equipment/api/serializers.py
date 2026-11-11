"""Serializers for equipment and its image.

The public serializer deliberately omits stock and administrative fields;
availability is a future bookings concern.
"""

from rest_framework import serializers

from apps.equipment import services
from apps.equipment.models import Equipment


class EquipmentPublicSerializer(serializers.ModelSerializer):
    """Read-only catalogue item shown to customers."""

    class Meta:
        model = Equipment
        fields = (
            "id",
            "name",
            "description",
            "fixed_price_kobo",
            "image_url",
        )
        read_only_fields = fields


class EquipmentImageSerializer(serializers.ModelSerializer):
    """The image fields of an equipment item."""

    class Meta:
        model = Equipment
        fields = ("id", "image_url", "cloudinary_public_id")
        read_only_fields = fields


class EquipmentImageUploadSerializer(serializers.Serializer):
    """Multipart payload for uploading or replacing an equipment image."""

    image = serializers.ImageField()


class EquipmentAdminSerializer(serializers.ModelSerializer):
    """Create/update payload and administrative representation of an item.

    ``image`` uploads or replaces the single image; ``remove_image`` clears it.
    Both are write-only, and ``cloudinary_public_id`` is never exposed here.
    """

    fixed_price_kobo = serializers.IntegerField(min_value=1)
    stock_quantity = serializers.IntegerField(min_value=0)
    image = serializers.ImageField(write_only=True, required=False)
    remove_image = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
    )

    class Meta:
        model = Equipment
        fields = (
            "id",
            "name",
            "description",
            "fixed_price_kobo",
            "stock_quantity",
            "image_url",
            "is_active",
            "image",
            "remove_image",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "image_url", "created_at", "updated_at")
        extra_kwargs = {
            "description": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        if attrs.get("image") and attrs.get("remove_image"):
            raise serializers.ValidationError(
                {
                    "remove_image": (
                        "Upload a new image or remove the current one, not both."
                    )
                }
            )
        return attrs

    def create(self, validated_data):
        image = validated_data.pop("image", None)
        validated_data.pop("remove_image", None)
        return services.create_equipment(image=image, **validated_data)

    def update(self, instance, validated_data):
        image = validated_data.pop("image", None)
        remove_image = validated_data.pop("remove_image", False)
        return services.update_equipment(
            equipment=instance,
            image=image,
            remove_image=remove_image,
            **validated_data,
        )

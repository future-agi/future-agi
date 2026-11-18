from rest_framework import serializers

from model_hub.models.prompt_folders import PromptFolder


class PromptFolderSerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = PromptFolder
        fields = [
            "id",
            "name",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
            "is_sample",
            "parent_folder",
            "created_by",
        ]
        read_only_fields = ["organization", "workspace", "is_sample"]

    def get_created_by(self, obj):
        """
        Return the name of the user who created this folder.
        Returns None if created_by is None.
        """
        if obj.created_by:
            return obj.created_by.name
        return obj.organization.name if obj.organization else None

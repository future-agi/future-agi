from rest_framework import serializers

from accounts.models import User
from model_hub.models import AnnotationTask
from model_hub.serializers.ai_model import AIModelSerializer


class AnnotationTaskAssigneeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "name", "email"]
        read_only_fields = fields


class AnnotationTaskSerializer(serializers.ModelSerializer):
    assigned_users = AnnotationTaskAssigneeSerializer(many=True, read_only=True)
    ai_model = AIModelSerializer(read_only=True)

    class Meta:
        model = AnnotationTask
        fields = [
            "id",
            "assigned_users",
            "created_at",
            "updated_at",
            "ai_model",
            "task_name",
        ]

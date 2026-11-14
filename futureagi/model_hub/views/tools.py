import json
import traceback

import yaml
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from model_hub.serializers.tools import ToolsSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods


class ToolsViewSet(BaseModelViewSetMixinWithUserOrg, viewsets.ModelViewSet):
    _gm = GeneralMethods()
    serializer_class = ToolsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Get base queryset with automatic filtering from mixin
        return super().get_queryset()

    def create(self, request, *args, **kwargs):
        processed_data = self._process_config_based_on_format(request.data)

        serializer = self.get_serializer(data=processed_data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)

        return self._gm.create_response(serializer.data)

    def perform_create(self, serializer):
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            workspace=getattr(self.request, "workspace", None),
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        processed_data = self._process_config_based_on_format(request.data)
        serializer = self.get_serializer(instance, data=processed_data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return self._gm.success_response(serializer.data)

    def perform_update(self, serializer):
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            workspace=getattr(self.request, "workspace", None),
        )

    def _process_config_based_on_format(self, data):
        """Process the config data based on the format key"""
        data = data.copy()
        if "config_type" not in data:
            return data

        format_type = data.get("config_type", "json")
        config_data = data.get("config", {})

        if format_type == "yaml":
            try:
                if isinstance(config_data, str):
                    parsed_config = yaml.safe_load(config_data)
                    data["config"] = parsed_config
                else:
                    raise ValidationError("Invalid config structure: must be a string")
            except yaml.YAMLError as e:
                traceback.print_exc()
                raise ValidationError(f"Invalid YAML format: {str(e)}")  # noqa: B904
        elif format_type == "json":
            try:
                if isinstance(config_data, str):
                    data["config"] = json.loads(config_data)
                elif isinstance(config_data, dict):
                    pass
                else:
                    raise ValidationError(
                        "Invalid JSON format: expected a string or dictionary."
                    )
            except Exception as e:
                raise ValidationError(f"Invalid JSON format: {str(e)}")  # noqa: B904
        return data

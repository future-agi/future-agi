from typing import Any

from rest_framework import serializers

from simulate.services.agent_definition import (
    mask_websocket_headers,
    mask_websocket_url,
)


class WebsocketHeadersField(serializers.JSONField):
    def to_representation(self, value):
        headers: Any = super().to_representation(value)
        return mask_websocket_headers(headers)


class WebsocketUrlField(serializers.URLField):
    def to_representation(self, value):
        return mask_websocket_url(super().to_representation(value))

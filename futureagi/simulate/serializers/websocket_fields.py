from rest_framework import serializers

from simulate.services.agent_definition import (
    keep_stored_websocket_headers,
    keep_stored_websocket_url,
    mask_websocket_headers,
    mask_websocket_url,
)


def _stored(field, name):
    instance = getattr(field.parent, "instance", None)
    return getattr(instance, name, None)


class WebsocketHeadersField(serializers.JSONField):
    def to_representation(self, value):
        return mask_websocket_headers(super().to_representation(value))

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        return keep_stored_websocket_headers(value, _stored(self, "websocket_headers"))


class WebsocketUrlField(serializers.URLField):
    def to_representation(self, value):
        return mask_websocket_url(super().to_representation(value))

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        return keep_stored_websocket_url(value, _stored(self, "websocket_url"))
